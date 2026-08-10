from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.models.execution import (
    ACTIVE_STATUSES,
    ExecutionErrorType,
    ExecutionJob,
    ExecutionJobStatus,
    TERMINAL_STATUSES,
)
from app.models.github import GitHubInstallation, RepositoryConnection
from app.services.github_client import GitHubClient
from sandbox_sdk import SandboxSpec
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.checkout import assert_remote_sanitized, clone_github_repo, prepare_fixture_checkout, sanitize_text

logger = get_logger(__name__)


async def claim_job(db: AsyncSession, job_id: UUID) -> ExecutionJob | None:
    """Atomic claim: only queued jobs move to starting."""
    result = await db.execute(
        update(ExecutionJob)
        .where(
            ExecutionJob.id == job_id,
            ExecutionJob.status == ExecutionJobStatus.queued,
            ExecutionJob.cancel_requested.is_(False),
        )
        .values(
            status=ExecutionJobStatus.starting,
            started_at=datetime.now(timezone.utc),
        )
        .returning(ExecutionJob.id)
    )
    row = result.first()
    await db.commit()
    if row is None:
        return None
    return await db.get(ExecutionJob, job_id)


async def finish_job(
    db: AsyncSession,
    job: ExecutionJob,
    *,
    status: ExecutionJobStatus,
    exit_code: int | None = None,
    error_type: ExecutionErrorType | None = None,
    error_message: str | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    truncated: bool = False,
) -> None:
    # Do not overwrite cancelled if cancel won the race after success path started writing.
    fresh = await db.get(ExecutionJob, job.id)
    if fresh is None:
        return
    if fresh.status == ExecutionJobStatus.cancelled:
        return
    if fresh.cancel_requested and status == ExecutionJobStatus.succeeded:
        status = ExecutionJobStatus.cancelled
        error_type = ExecutionErrorType.cancelled
        error_message = "Cancelled"
    fresh.status = status
    fresh.exit_code = exit_code
    fresh.error_type = error_type
    fresh.error_message = (error_message or "")[:1024] or None
    fresh.stdout = stdout
    fresh.stderr = stderr
    fresh.output_truncated = truncated
    fresh.finished_at = datetime.now(timezone.utc)
    await db.commit()


async def process_job(job_id: UUID, provider: DockerSandboxProvider) -> None:
    settings = get_settings()
    factory = get_session_factory()
    sandbox_id: str | None = None
    work_dir: Path | None = None

    async with factory() as db:
        job = await claim_job(db, job_id)
        if job is None:
            # Duplicate delivery or already cancelled/terminal.
            existing = await db.get(ExecutionJob, job_id)
            if existing and existing.status == ExecutionJobStatus.queued and existing.cancel_requested:
                existing.status = ExecutionJobStatus.cancelled
                existing.error_type = ExecutionErrorType.cancelled
                existing.error_message = "Cancelled before start"
                existing.finished_at = datetime.now(timezone.utc)
                await db.commit()
            logger.info("execution.skip", execution_id=str(job_id))
            return

        logger.info("execution.started", execution_id=str(job.id))
        try:
            # Refresh cancel flag.
            await db.refresh(job)
            if job.cancel_requested:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.cancelled,
                    error_type=ExecutionErrorType.cancelled,
                    error_message="Cancelled",
                )
                return

            connection = await db.get(RepositoryConnection, job.repository_connection_id)
            if connection is None or connection.user_id != job.user_id:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.failed,
                    error_type=ExecutionErrorType.invalid_request,
                    error_message="Repository connection missing",
                )
                return

            job.status = ExecutionJobStatus.cloning
            await db.commit()
            logger.info("execution.clone_started", execution_id=str(job.id))

            work_dir = Path(tempfile.mkdtemp(prefix=f"agentdock-job-{job.id}-"))
            mode = settings.sandbox_checkout_mode.lower()
            if mode == "fixture":
                fixture = Path(settings.sandbox_fixture_repo_path)
                if not fixture.is_dir():
                    raise RuntimeError("Fixture repository path is not configured")
                prepare_fixture_checkout(fixture, work_dir / "repo")
                repo_path = work_dir / "repo"
            else:
                installation = await db.get(GitHubInstallation, connection.installation_id)
                if installation is None or installation.suspended_at is not None:
                    raise RuntimeError("GitHub installation unavailable")
                client = GitHubClient()
                token = await client.create_installation_token(installation.github_installation_id)
                try:
                    clone_github_repo(
                        dest=work_dir / "repo",
                        owner=connection.owner,
                        name=connection.name,
                        default_branch=connection.default_branch,
                        installation_token=token,
                    )
                finally:
                    del token
                repo_path = work_dir / "repo"
                assert_remote_sanitized(repo_path)

            await db.refresh(job)
            if job.cancel_requested:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.cancelled,
                    error_type=ExecutionErrorType.cancelled,
                    error_message="Cancelled",
                )
                return

            nano_cpus = int(settings.sandbox_cpu_limit * 1_000_000_000)
            try:
                sandbox_id = provider.create(
                    SandboxSpec(
                        image=settings.sandbox_image,
                        execution_id=str(job.id),
                        memory_limit=settings.sandbox_memory_limit,
                        nano_cpus=nano_cpus,
                        pids_limit=settings.sandbox_pids_limit,
                        network_disabled=settings.sandbox_network_disabled,
                    )
                )
            except Exception as exc:
                logger.error("sandbox.create_failed", execution_id=str(job.id))
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.failed,
                    error_type=ExecutionErrorType.sandbox_start_failed,
                    error_message="Failed to start sandbox",
                )
                raise exc

            job.sandbox_id = sandbox_id
            job.status = ExecutionJobStatus.running
            await db.commit()
            logger.info("sandbox.created", execution_id=str(job.id), sandbox_id=sandbox_id[:12])
            logger.info("execution.running", execution_id=str(job.id))

            provider.put_directory(sandbox_id, str(repo_path), "/workspace")

            workdir = "/workspace"
            if job.working_directory:
                workdir = f"/workspace/{job.working_directory.strip('/')}"

            result = provider.exec(
                sandbox_id,
                list(job.command),
                workdir=workdir,
                timeout_seconds=float(settings.sandbox_timeout_seconds),
                max_output_bytes=settings.sandbox_max_output_bytes,
            )
            stdout = sanitize_text(result.stdout.decode("utf-8", errors="replace"))
            stderr = sanitize_text(result.stderr.decode("utf-8", errors="replace"))

            await db.refresh(job)
            if result.timed_out:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.timed_out,
                    exit_code=result.exit_code,
                    error_type=ExecutionErrorType.execution_timeout,
                    error_message="Execution timed out",
                    stdout=stdout,
                    stderr=stderr,
                    truncated=result.truncated,
                )
                logger.info("execution.timed_out", execution_id=str(job.id))
            elif job.cancel_requested:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.cancelled,
                    exit_code=result.exit_code,
                    error_type=ExecutionErrorType.cancelled,
                    error_message="Cancelled",
                    stdout=stdout,
                    stderr=stderr,
                    truncated=result.truncated,
                )
                logger.info("execution.cancelled", execution_id=str(job.id))
            elif result.exit_code == 0:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.succeeded,
                    exit_code=0,
                    stdout=stdout,
                    stderr=stderr,
                    truncated=result.truncated,
                )
                logger.info("execution.succeeded", execution_id=str(job.id))
            else:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.failed,
                    exit_code=result.exit_code,
                    error_type=ExecutionErrorType.command_failed,
                    error_message=f"Command exited with code {result.exit_code}",
                    stdout=stdout,
                    stderr=stderr,
                    truncated=result.truncated,
                )
                logger.info("execution.failed", execution_id=str(job.id), exit_code=result.exit_code)
        except Exception as exc:
            logger.exception("execution.internal_error", execution_id=str(job_id))
            message = sanitize_text(str(exc))[:512]
            err_type = ExecutionErrorType.internal_error
            if "clone" in message.lower() or "fixture" in message.lower():
                err_type = ExecutionErrorType.repository_clone_failed
            async with factory() as db2:
                job2 = await db2.get(ExecutionJob, job_id)
                if job2 and job2.status not in TERMINAL_STATUSES:
                    await finish_job(
                        db2,
                        job2,
                        status=ExecutionJobStatus.failed,
                        error_type=err_type,
                        error_message=message or "Internal error",
                    )
        finally:
            if sandbox_id:
                provider.destroy(sandbox_id)
                provider.destroy_labeled(execution_id=str(job_id))
                logger.info("sandbox.destroyed", execution_id=str(job_id))
            if work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)


async def reconcile_stale_jobs(provider: DockerSandboxProvider) -> None:
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.worker_reconcile_stale_seconds)
    factory = get_session_factory()
    async with factory() as db:
        rows = await db.scalars(
            select(ExecutionJob).where(
                ExecutionJob.status.in_(tuple(ACTIVE_STATUSES - {ExecutionJobStatus.queued})),
                ExecutionJob.updated_at < cutoff,
            )
        )
        for job in rows:
            logger.warning("execution.reconcile_stale", execution_id=str(job.id), status=job.status.value)
            if job.sandbox_id:
                provider.destroy(job.sandbox_id)
            provider.destroy_labeled(execution_id=str(job.id))
            job.status = ExecutionJobStatus.failed
            job.error_type = ExecutionErrorType.internal_error
            job.error_message = "Interrupted (worker reconciliation)"
            job.finished_at = datetime.now(timezone.utc)
        await db.commit()
