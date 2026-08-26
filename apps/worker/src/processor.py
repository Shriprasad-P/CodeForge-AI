from __future__ import annotations

import shutil
import tempfile
import asyncio
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import bind_observability, classify_error, clear_observability, metrics
from app.db.session import get_session_factory
from app.models.execution import (
    ACTIVE_STATUSES,
    ExecutionErrorType,
    ExecutionJob,
    ExecutionJobStatus,
    TERMINAL_STATUSES,
)
from app.services.github_client import GitHubClient
from sandbox_sdk import SandboxSpec
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.checkout import assert_remote_sanitized, clone_github_repo, prepare_fixture_checkout, sanitize_text
from src.authorization import RepositoryRevokedError, require_repository_authorized
from src.delivery import DeliveryClaim, DeliveryClaimLost
from src.runtime import run_blocking

logger = get_logger(__name__)


async def claim_job(
    db: AsyncSession,
    job_id: UUID,
    delivery_claim: DeliveryClaim | None = None,
) -> ExecutionJob | None:
    """Atomically claim a queued or recoverable execution delivery."""
    conditions = [ExecutionJob.id == job_id, ExecutionJob.cancel_requested.is_(False)]
    if delivery_claim is None:
        conditions.append(ExecutionJob.status == ExecutionJobStatus.queued)
    else:
        conditions.extend(
            [
                ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)),
                ExecutionJob.delivery_claim_token == delivery_claim.token,
            ]
        )
    result = await db.execute(
        update(ExecutionJob)
        .where(*conditions)
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
    delivery_claim: DeliveryClaim | None = None,
) -> None:
    # Do not overwrite cancellation, a terminal state, or a newer delivery claim.
    fresh = await db.get(ExecutionJob, job.id)
    if fresh is None:
        return
    if delivery_claim is not None and fresh.delivery_claim_token != delivery_claim.token:
        raise DeliveryClaimLost("execution delivery claim lost")
    if fresh.status in TERMINAL_STATUSES:
        return
    state_from = fresh.status.value
    if fresh.cancel_requested and status not in {ExecutionJobStatus.cancelled, ExecutionJobStatus.repository_revoked}:
        status = ExecutionJobStatus.cancelled
        error_type = ExecutionErrorType.cancelled
        error_message = "Cancelled"
    values = {
        "status": status,
        "exit_code": exit_code,
        "error_type": error_type,
        "error_message": (error_message or "")[:1024] or None,
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": truncated,
        "finished_at": datetime.now(timezone.utc),
    }
    conditions = [ExecutionJob.id == job.id, ExecutionJob.status.in_(tuple(ACTIVE_STATUSES))]
    if delivery_claim is not None:
        conditions.append(ExecutionJob.delivery_claim_token == delivery_claim.token)
    if status == ExecutionJobStatus.succeeded:
        conditions.append(ExecutionJob.cancel_requested.is_(False))
    changed = await db.execute(update(ExecutionJob).where(*conditions).values(**values))
    if delivery_claim is not None and changed.rowcount != 1:
        await db.rollback()
        raise DeliveryClaimLost("execution completion claim lost")
    await db.commit()
    metrics.inc(f"agentdock_executions_{status.value}_total")
    duration_ms = None
    if fresh.started_at is not None:
        duration_ms = int((datetime.now(timezone.utc) - fresh.started_at).total_seconds() * 1000)
    logger.info(
        "execution.state_transition",
        execution_job_id=str(job.id),
        repository_connection_id=str(fresh.repository_connection_id),
        workflow_correlation_id=str(fresh.workflow_correlation_id),
        state_from=state_from,
        state_to=status.value,
        duration_ms=duration_ms,
        error_class=classify_error(error_message) if error_message else None,
        retryable=False,
        terminal=status in TERMINAL_STATUSES,
    )


async def _set_job_state(
    db: AsyncSession,
    job_id: UUID,
    values: dict,
    delivery_claim: DeliveryClaim | None,
) -> None:
    conditions = [
        ExecutionJob.id == job_id,
        ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)),
        ExecutionJob.cancel_requested.is_(False),
    ]
    if delivery_claim is not None:
        conditions.append(ExecutionJob.delivery_claim_token == delivery_claim.token)
    changed = await db.execute(update(ExecutionJob).where(*conditions).values(**values))
    if delivery_claim is not None and changed.rowcount != 1:
        await db.rollback()
        raise DeliveryClaimLost("execution state claim lost")
    await db.commit()


async def process_job(
    job_id: UUID,
    provider: DockerSandboxProvider,
    delivery_claim: DeliveryClaim | None = None,
) -> None:
    settings = get_settings()
    factory = get_session_factory()
    sandbox_id: str | None = None
    work_dir: Path | None = None
    cancel_event = threading.Event()
    monitor_stop = asyncio.Event()
    monitor_task: asyncio.Task | None = None

    async with factory() as db:
        job = await claim_job(db, job_id, delivery_claim)
        if job is None:
            # Duplicate delivery or already cancelled/terminal.
            existing = await db.get(ExecutionJob, job_id)
            if existing and existing.cancel_requested and existing.status in ACTIVE_STATUSES:
                cancel_conditions = [
                    ExecutionJob.id == job_id,
                    ExecutionJob.cancel_requested.is_(True),
                    ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)),
                ]
                if delivery_claim is not None:
                    cancel_conditions.append(ExecutionJob.delivery_claim_token == delivery_claim.token)
                await db.execute(
                    update(ExecutionJob)
                    .where(*cancel_conditions)
                    .values(
                        status=ExecutionJobStatus.cancelled,
                        error_type=ExecutionErrorType.cancelled,
                        error_message="Cancelled before start",
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
            logger.info("execution.skip", execution_id=str(job_id))
            return

        bind_observability(
            workflow_correlation_id=str(job.workflow_correlation_id),
            execution_job_id=str(job.id),
            repository_connection_id=str(job.repository_connection_id),
        )
        logger.info(
            "execution.started",
            execution_job_id=str(job.id),
            workflow_correlation_id=str(job.workflow_correlation_id),
            repository_connection_id=str(job.repository_connection_id),
        )
        metrics.inc("agentdock_executions_started_total")
        async def monitor_control() -> None:
            while not monitor_stop.is_set():
                try:
                    await asyncio.wait_for(monitor_stop.wait(), timeout=0.2)
                    return
                except asyncio.TimeoutError:
                    pass
                async with factory() as control_db:
                    current = await control_db.get(ExecutionJob, job_id)
                    if current is None or current.cancel_requested:
                        cancel_event.set()
                        return
                    if delivery_claim is not None and current.delivery_claim_token != delivery_claim.token:
                        cancel_event.set()
                        return

        monitor_task = asyncio.create_task(monitor_control())
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
                    delivery_claim=delivery_claim,
                )
                return

            try:
                connection, installation = await require_repository_authorized(
                    db,
                    user_id=job.user_id,
                    connection_id=job.repository_connection_id,
                )
            except RepositoryRevokedError:
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.repository_revoked,
                    error_type=ExecutionErrorType.repository_revoked,
                    error_message="Repository authorization revoked",
                    delivery_claim=delivery_claim,
                )
                return

            await _set_job_state(db, job.id, {"status": ExecutionJobStatus.cloning}, delivery_claim)
            await db.refresh(job)
            logger.info("execution.clone_started", execution_id=str(job.id))

            work_dir = Path(tempfile.mkdtemp(prefix=f"agentdock-job-{job.id}-"))
            mode = settings.sandbox_checkout_mode.lower()
            connection, installation = await require_repository_authorized(
                db,
                user_id=job.user_id,
                connection_id=job.repository_connection_id,
            )
            if mode == "fixture":
                fixture = Path(settings.sandbox_fixture_repo_path)
                if not fixture.is_dir():
                    raise RuntimeError("Fixture repository path is not configured")
                prepare_fixture_checkout(fixture, work_dir / "repo")
                repo_path = work_dir / "repo"
            else:
                connection, installation = await require_repository_authorized(
                    db,
                    user_id=job.user_id,
                    connection_id=job.repository_connection_id,
                )
                client = GitHubClient()
                token = await client.create_installation_token(installation.github_installation_id)
                try:
                    connection, installation = await require_repository_authorized(
                        db,
                        user_id=job.user_id,
                        connection_id=job.repository_connection_id,
                    )
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
                    delivery_claim=delivery_claim,
                )
                return

            nano_cpus = int(settings.sandbox_cpu_limit * 1_000_000_000)
            try:
                sandbox_started = time.perf_counter()
                await require_repository_authorized(
                    db,
                    user_id=job.user_id,
                    connection_id=job.repository_connection_id,
                )
                sandbox_id = await run_blocking(provider.create,
                    SandboxSpec(
                        image=settings.sandbox_image,
                        execution_id=str(job.id),
                        memory_limit=settings.sandbox_memory_limit,
                        nano_cpus=nano_cpus,
                        pids_limit=settings.sandbox_pids_limit,
                        network_disabled=settings.sandbox_network_disabled,
                    )
                )
                metrics.observe_duration("agentdock_sandbox_startup_duration_ms", (time.perf_counter() - sandbox_started) * 1000)
            except Exception as exc:
                logger.error("sandbox.create_failed", execution_id=str(job.id))
                await finish_job(
                    db,
                    job,
                    status=ExecutionJobStatus.failed,
                    error_type=ExecutionErrorType.sandbox_start_failed,
                    error_message="Failed to start sandbox",
                    delivery_claim=delivery_claim,
                )
                raise exc

            await _set_job_state(
                db,
                job.id,
                {"sandbox_id": sandbox_id, "status": ExecutionJobStatus.running},
                delivery_claim,
            )
            await db.refresh(job)
            logger.info("sandbox.created", execution_id=str(job.id), sandbox_id=sandbox_id[:12])
            logger.info("execution.running", execution_id=str(job.id))

            await require_repository_authorized(
                db,
                user_id=job.user_id,
                connection_id=job.repository_connection_id,
            )
            await run_blocking(provider.put_directory, sandbox_id, str(repo_path), "/workspace")

            workdir = "/workspace"
            if job.working_directory:
                workdir = f"/workspace/{job.working_directory.strip('/')}"

            execution_started = time.perf_counter()
            result = await run_blocking(
                provider.exec,
                sandbox_id,
                list(job.command),
                workdir=workdir,
                timeout_seconds=float(settings.sandbox_timeout_seconds),
                max_output_bytes=settings.sandbox_max_output_bytes,
                cancel_event=cancel_event,
            )
            metrics.observe_duration("agentdock_sandbox_execution_duration_ms", (time.perf_counter() - execution_started) * 1000)
            stdout = sanitize_text(result.stdout.decode("utf-8", errors="replace"))
            stderr = sanitize_text(result.stderr.decode("utf-8", errors="replace"))

            await db.refresh(job)
            if result.cancelled or job.cancel_requested:
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
                    delivery_claim=delivery_claim,
                )
                logger.info("execution.cancelled", execution_id=str(job.id))
            elif result.timed_out:
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
                    delivery_claim=delivery_claim,
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
                    delivery_claim=delivery_claim,
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
                    delivery_claim=delivery_claim,
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
                    delivery_claim=delivery_claim,
                )
                logger.info("execution.failed", execution_id=str(job.id), exit_code=result.exit_code)
        except Exception as exc:
            logger.exception("execution.internal_error", execution_id=str(job_id))
            message = sanitize_text(str(exc))[:512]
            err_type = ExecutionErrorType.internal_error
            if isinstance(exc, RepositoryRevokedError):
                err_type = ExecutionErrorType.repository_revoked
            if not isinstance(exc, RepositoryRevokedError) and ("clone" in message.lower() or "fixture" in message.lower()):
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
                        delivery_claim=delivery_claim,
                    )
        finally:
            monitor_stop.set()
            if monitor_task is not None:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
            if sandbox_id:
                await run_blocking(provider.destroy, sandbox_id)
            # Label cleanup also covers a create call that completed after its
            # bounded await was released.
            await run_blocking(provider.destroy_labeled, execution_id=str(job_id))
            logger.info("sandbox.destroyed", execution_id=str(job_id))
            if work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
            clear_observability()


async def reconcile_stale_jobs(provider: DockerSandboxProvider) -> None:
    # PostgreSQL outbox reconciliation, rather than terminalizing recoverable work.
    from src.delivery import reconcile_durable_delivery

    await reconcile_durable_delivery()
