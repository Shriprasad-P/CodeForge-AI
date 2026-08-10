from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
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
from app.services.queue import enqueue_agent_run

logger = get_logger(__name__)


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
    if run.status in AGENT_TERMINAL:
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
