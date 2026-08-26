from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.agent_run import (
    AGENT_ACTIVE,
    AGENT_TERMINAL,
    AgentRun,
    AgentRunErrorType,
    AgentRunStatus,
    AgentStep,
)
from app.models.agent_session import AgentSession
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.user import User
from app.services.queue import enqueue_agent_run, enqueue_publication

logger = get_logger(__name__)


def _is_valid_change_manifest(value: object) -> bool:
    if not isinstance(value, list):
        return False
    allowed_types = {"added", "deleted", "modified", "renamed", "mode_changed"}
    for entry in value:
        if not isinstance(entry, dict):
            return False
        path = entry.get("path")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or any(part == ".." for part in path.split("/"))
        ):
            return False
        if entry.get("change_type") not in allowed_types:
            return False
        if any(
            key in entry and not isinstance(entry[key], str)
            for key in ("old_mode", "new_mode", "old_blob", "new_blob", "previous_path")
        ):
            return False
        if "binary" in entry and not isinstance(entry["binary"], bool):
            return False
    return True


async def create_agent_run(
    db: AsyncSession,
    *,
    user: User,
    repository_connection_id: UUID,
    task: str,
    agent_session_id: UUID | None,
) -> AgentRun:
    settings = get_settings()
    if not settings.agent_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent LLM is not configured",
        )

    connection = await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.id == repository_connection_id,
            RepositoryConnection.user_id == user.id,
            RepositoryConnection.is_active.is_(True),
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository connection not found")

    installation = await db.get(GitHubInstallation, connection.installation_id)
    if installation is None or installation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository connection not found")

    if agent_session_id is not None:
        session = await db.scalar(
            select(AgentSession).where(AgentSession.id == agent_session_id, AgentSession.user_id == user.id)
        )
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")

    active = await db.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.user_id == user.id, AgentRun.status.in_(tuple(AGENT_ACTIVE)))
    )
    if int(active or 0) >= settings.agent_max_active_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many active agent runs for this account",
        )

    run = AgentRun(
        user_id=user.id,
        repository_connection_id=connection.id,
        agent_session_id=agent_session_id,
        status=AgentRunStatus.queued,
        task=task,
        model_provider=settings.llm_provider,
        model_name=settings.llm_model,
        max_steps=settings.agent_max_steps,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    await enqueue_agent_run(run.id)
    logger.info("agent.run.queued", agent_run_id=str(run.id), user_id=str(user.id))
    try:
        from app.services.agent_events import publish_agent_event

        await publish_agent_event(run.id, "agent.run.queued", {"status": "queued"})
    except Exception:
        pass
    return run


async def get_user_run(db: AsyncSession, *, user_id: UUID, run_id: UUID) -> AgentRun:
    run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


async def list_user_runs(db: AsyncSession, *, user_id: UUID, limit: int = 50) -> list[AgentRun]:
    rows = await db.scalars(
        select(AgentRun).where(AgentRun.user_id == user_id).order_by(AgentRun.created_at.desc()).limit(limit)
    )
    return list(rows)


async def list_run_steps(db: AsyncSession, *, user_id: UUID, run_id: UUID) -> list[AgentStep]:
    await get_user_run(db, user_id=user_id, run_id=run_id)
    rows = await db.scalars(
        select(AgentStep).where(AgentStep.agent_run_id == run_id).order_by(AgentStep.step_number.asc())
    )
    return list(rows)


async def request_cancel(db: AsyncSession, *, user_id: UUID, run_id: UUID) -> AgentRun:
    run = await get_user_run(db, user_id=user_id, run_id=run_id)
    if run.status == AgentRunStatus.queued:
        run.status = AgentRunStatus.cancelled
        run.cancel_requested = True
        run.error_type = AgentRunErrorType.cancelled
        run.error_message = "Cancelled before start"
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(run)
        logger.info("agent.run.cancelled", agent_run_id=str(run.id), phase="queued")
        try:
            from app.services.agent_events import publish_agent_event

            await publish_agent_event(run.id, "agent.run.cancelled", {"status": "cancelled"})
        except Exception:
            pass
        return run
    if run.status in AGENT_TERMINAL or run.status in {
        AgentRunStatus.awaiting_approval,
        AgentRunStatus.publishing,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Agent run already finished")
    run.cancel_requested = True
    await db.commit()
    await db.refresh(run)
    logger.info("agent.run.cancel_requested", agent_run_id=str(run.id))
    try:
        from app.services.agent_events import publish_agent_event

        await publish_agent_event(run.id, "agent.run.status", {"status": run.status.value, "cancel_requested": True})
    except Exception:
        pass
    return run


async def approve_agent_run(
    db: AsyncSession,
    *,
    user_id: UUID,
    run_id: UUID,
    artifact_hash: str,
    artifact_version: int,
    base_commit_sha: str,
) -> AgentRun:
    """Atomically authorize exactly one immutable, validated artifact for publication."""
    run = await get_user_run(db, user_id=user_id, run_id=run_id)
    if run.status != AgentRunStatus.awaiting_approval:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run is not awaiting approval")
    if run.approval_status != "pending" or run.publication_status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run approval is no longer available")
    artifact = bytes(run.publication_artifact) if run.publication_artifact is not None else None
    persisted_hash = run.publication_artifact_hash
    if (
        run.publication_artifact_status != "ready"
        or artifact is None
        or not persisted_hash
        or run.publication_artifact_size is None
        or run.publication_artifact_version is None
        or not _is_valid_change_manifest(run.publication_change_manifest)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run has no publishable artifact")
    if len(artifact) != run.publication_artifact_size:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Publication artifact is inconsistent")
    calculated_hash = hashlib.sha256(artifact).hexdigest()
    if not hmac.compare_digest(calculated_hash, persisted_hash) or not hmac.compare_digest(persisted_hash, run.diff_hash or ""):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Publication artifact integrity check failed")
    if artifact_version != run.publication_artifact_version or not hmac.compare_digest(artifact_hash, persisted_hash):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval references a stale artifact")
    if not run.base_commit_sha or base_commit_sha != run.base_commit_sha:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval references a stale base commit")
    if (
        not isinstance(run.validation, dict)
        or run.validation.get("ok") is not True
        or not run.validation_artifact_hash
        or not hmac.compare_digest(run.validation_artifact_hash, persisted_hash)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Artifact validation is missing or stale")
    connection = await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.id == run.repository_connection_id,
            RepositoryConnection.user_id == user_id,
            RepositoryConnection.is_active.is_(True),
        )
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Repository connection is no longer valid")
    now = datetime.now(timezone.utc)
    changed = await db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.user_id == user_id,
            AgentRun.status == AgentRunStatus.awaiting_approval,
            AgentRun.approval_status == "pending",
            AgentRun.publication_status == "pending",
            AgentRun.publication_artifact_status == "ready",
            AgentRun.publication_artifact_hash == persisted_hash,
            AgentRun.publication_artifact_version == artifact_version,
            AgentRun.base_commit_sha == base_commit_sha,
            AgentRun.validation_artifact_hash == persisted_hash,
        )
        .values(
            approval_status="approved",
            approved_by_user_id=user_id,
            approved_at=now,
            approval_artifact_hash=persisted_hash,
            approval_artifact_version=artifact_version,
            approval_base_commit_sha=base_commit_sha,
            publication_status="approved",
        )
    )
    if changed.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run approval is already being processed")
    await db.commit()
    try:
        await enqueue_publication(run_id)
    except Exception as exc:  # noqa: BLE001
        # The approval is durable in PostgreSQL. A worker startup reconciler
        # will requeue it, so Redis loss cannot silently lose publication.
        logger.error("agent.publication_enqueue_failed", agent_run_id=str(run_id), error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Publication queued durably; retry shortly",
        ) from None
    from app.services.agent_events import publish_agent_event

    await publish_agent_event(run_id, "agent.approved", {"publication_status": "approved"})
    return await get_user_run(db, user_id=user_id, run_id=run_id)


async def reject_agent_run(db: AsyncSession, *, user_id: UUID, run_id: UUID) -> AgentRun:
    run = await get_user_run(db, user_id=user_id, run_id=run_id)
    if run.status != AgentRunStatus.awaiting_approval or run.approval_status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Run is not awaiting approval")
    run.approval_status = "rejected"
    run.publication_status = "rejected"
    run.status = AgentRunStatus.rejected
    run.rejected_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    from app.services.agent_events import publish_agent_event

    await publish_agent_event(run_id, "agent.rejected", {"publication_status": "rejected"})
    return run
