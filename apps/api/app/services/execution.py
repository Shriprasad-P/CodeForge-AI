from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.redis import get_redis
from app.models.agent_session import AgentSession
from app.models.execution import ACTIVE_STATUSES, ExecutionJob, ExecutionJobStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.user import User
from app.services.queue import enqueue_execution

logger = get_logger(__name__)


def resolve_workdir(working_directory: str | None) -> str:
    """Resolve relative workdir under /workspace; raise on escape."""
    if not working_directory:
        return "/workspace"
    parts = [p for p in working_directory.replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts) or working_directory.startswith(("/", "~")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid working_directory")
    return "/workspace/" + "/".join(parts)


async def enforce_execution_rate_limit(user_id: UUID) -> None:
    settings = get_settings()
    try:
        redis = get_redis()
        key = f"exec:rl:{user_id}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.execution_rate_limit_window_seconds)
        if count > settings.execution_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many execution requests. Try again shortly.",
            )
    except HTTPException:
        raise
    except Exception:
        logger.warning("execution.rate_limit_unavailable")


async def create_execution_job(
    db: AsyncSession,
    *,
    user: User,
    repository_connection_id: UUID,
    command: list[str],
    working_directory: str | None,
    agent_session_id: UUID | None,
) -> ExecutionJob:
    settings = get_settings()
    await enforce_execution_rate_limit(user.id)

    # Validate workdir early (API layer also validates).
    resolve_workdir(working_directory)

    connection = await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.id == repository_connection_id,
            RepositoryConnection.user_id == user.id,
        )
    )
    if connection is None or not connection.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository connection not found")

    installation = await db.get(GitHubInstallation, connection.installation_id)
    if installation is None or installation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository connection not found")
    if installation.suspended_at is not None and settings.sandbox_checkout_mode == "github":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="GitHub installation is suspended")

    if agent_session_id is not None:
        session = await db.scalar(
            select(AgentSession).where(AgentSession.id == agent_session_id, AgentSession.user_id == user.id)
        )
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent session not found")

    active = await db.scalar(
        select(func.count())
        .select_from(ExecutionJob)
        .where(ExecutionJob.user_id == user.id, ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)))
    )
    if int(active or 0) >= settings.execution_max_active_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many active executions for this account",
        )

    job = ExecutionJob(
        user_id=user.id,
        repository_connection_id=connection.id,
        agent_session_id=agent_session_id,
        status=ExecutionJobStatus.queued,
        command=list(command),
        working_directory=working_directory,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await enqueue_execution(job.id)
    logger.info("execution.queued", execution_id=str(job.id), user_id=str(user.id))
    return job


async def get_user_job(db: AsyncSession, *, user_id: UUID, job_id: UUID) -> ExecutionJob:
    job = await db.scalar(
        select(ExecutionJob).where(ExecutionJob.id == job_id, ExecutionJob.user_id == user_id)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return job


async def list_user_jobs(db: AsyncSession, *, user_id: UUID, limit: int = 50) -> list[ExecutionJob]:
    result = await db.scalars(
        select(ExecutionJob)
        .where(ExecutionJob.user_id == user_id)
        .order_by(ExecutionJob.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def request_cancel(db: AsyncSession, *, user_id: UUID, job_id: UUID) -> ExecutionJob:
    from app.models.execution import ExecutionErrorType

    job = await get_user_job(db, user_id=user_id, job_id=job_id)
    if job.status == ExecutionJobStatus.queued:
        job.status = ExecutionJobStatus.cancelled
        job.cancel_requested = True
        job.error_type = ExecutionErrorType.cancelled
        job.error_message = "Cancelled before start"
        job.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(job)
        logger.info("execution.cancelled", execution_id=str(job.id), phase="queued")
        return job
    if job.status in {
        ExecutionJobStatus.succeeded,
        ExecutionJobStatus.failed,
        ExecutionJobStatus.cancelled,
        ExecutionJobStatus.timed_out,
    }:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Execution already finished")
    job.cancel_requested = True
    await db.commit()
    await db.refresh(job)
    logger.info("execution.cancel_requested", execution_id=str(job.id))
    return job
