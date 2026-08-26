from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import (
    bind_observability,
    claim_ref,
    classify_error,
    clear_observability,
    metrics,
    persist_metric,
    safe_error,
)
from app.db.session import get_session_factory
from app.models.agent_run import AGENT_ACTIVE, AGENT_TERMINAL, AgentRun, AgentRunErrorType, AgentRunStatus
from app.models.execution import ACTIVE_STATUSES, TERMINAL_STATUSES, ExecutionErrorType, ExecutionJob, ExecutionJobStatus
from app.models.outbox import OutboxEvent
from app.services.outbox import AGENT_RUN_REQUESTED, EXECUTION_REQUESTED, PUBLICATION_REQUESTED
from app.services.queue import enqueue_outbox_event

logger = get_logger(__name__)

OUTBOX_PENDING = "pending"
OUTBOX_DISPATCHING = "dispatching"
OUTBOX_DISPATCHED = "dispatched"
OUTBOX_PROCESSING = "processing"
OUTBOX_PROCESSED = "processed"
OUTBOX_DEAD = "dead"
SUPPORTED_EVENT_TYPES = frozenset({EXECUTION_REQUESTED, AGENT_RUN_REQUESTED, PUBLICATION_REQUESTED})


class DeliveryClaimLost(RuntimeError):
    """The worker no longer owns the durable delivery lease."""


@dataclass(frozen=True)
class DeliveryClaim:
    event_id: UUID
    event_type: str
    aggregate_id: UUID
    token: str
    attempt_count: int
    workflow_correlation_id: str | None = None
    request_id: str | None = None

    @property
    def claim_reference(self) -> str | None:
        return claim_ref(self.token)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _backoff(attempt: int) -> timedelta:
    settings = get_settings()
    seconds = settings.outbox_retry_backoff_seconds * max(1, min(attempt, 8))
    return timedelta(seconds=seconds)


async def ensure_durable_events(db) -> None:
    """Backfill missing delivery records for pre-outbox queued workflow rows."""
    execution_rows = await db.scalars(
        select(ExecutionJob).where(ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)))
    )
    agent_rows = await db.scalars(select(AgentRun).where(AgentRun.status.in_(tuple(AGENT_ACTIVE))))
    publication_rows = await db.scalars(
        select(AgentRun).where(
            AgentRun.approval_status == "approved",
            AgentRun.publication_status.in_(["approved", "publication_failed", "publishing"]),
            AgentRun.publication_artifact_status == "ready",
        )
    )
    values: list[dict[str, Any]] = []
    for job in execution_rows:
        values.append(
            {
                "id": uuid4(),
                "event_type": EXECUTION_REQUESTED,
                "aggregate_id": job.id,
                "dedupe_key": f"{EXECUTION_REQUESTED}:{job.id}",
                "payload": {
                    "execution_id": str(job.id),
                    "workflow_correlation_id": str(job.workflow_correlation_id),
                },
            }
        )
    for run in agent_rows:
        values.append(
            {
                "id": uuid4(),
                "event_type": AGENT_RUN_REQUESTED,
                "aggregate_id": run.id,
                "dedupe_key": f"{AGENT_RUN_REQUESTED}:{run.id}",
                "payload": {
                    "agent_run_id": str(run.id),
                    "workflow_correlation_id": str(run.workflow_correlation_id),
                },
            }
        )
    for run in publication_rows:
        values.append(
            {
                "id": uuid4(),
                "event_type": PUBLICATION_REQUESTED,
                "aggregate_id": run.id,
                "dedupe_key": f"{PUBLICATION_REQUESTED}:{run.id}",
                "payload": {
                    "agent_run_id": str(run.id),
                    "workflow_correlation_id": str(run.workflow_correlation_id),
                },
            }
        )
    if values:
        await db.execute(insert(OutboxEvent).values(values).on_conflict_do_nothing(index_elements=["dedupe_key"]))


async def find_durable_event(event_type: str, aggregate_id: UUID) -> UUID | None:
    """Map a legacy queue notification to the authoritative outbox row."""
    factory = get_session_factory()
    async with factory() as db:
        await ensure_durable_events(db)
        event = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == event_type,
                OutboxEvent.aggregate_id == aggregate_id,
            )
        )
        await db.commit()
        if event is None or event.status == OUTBOX_DEAD:
            return None
        return event.id


async def _mark_dead_workflow(db, event: OutboxEvent, message: str) -> None:
    now = datetime.now(timezone.utc)
    if event.event_type == EXECUTION_REQUESTED:
        await db.execute(
            update(ExecutionJob)
            .where(ExecutionJob.id == event.aggregate_id, ExecutionJob.status.not_in(tuple(TERMINAL_STATUSES)))
            .values(
                status=ExecutionJobStatus.failed,
                error_type=ExecutionErrorType.internal_error,
                error_message=message[:1024],
                finished_at=now,
            )
        )
    elif event.event_type == AGENT_RUN_REQUESTED:
        await db.execute(
            update(AgentRun)
            .where(AgentRun.id == event.aggregate_id, AgentRun.status.not_in(tuple(AGENT_TERMINAL)))
            .values(
                status=AgentRunStatus.failed,
                error_type=AgentRunErrorType.internal_error,
                error_message=message[:1024],
                finished_at=now,
            )
        )
    elif event.event_type == PUBLICATION_REQUESTED:
        await db.execute(
            update(AgentRun)
            .where(
                AgentRun.id == event.aggregate_id,
                AgentRun.publication_status.not_in(["published", "rejected", "revoked"]),
                AgentRun.status.not_in(tuple(AGENT_TERMINAL)),
            )
            .values(
                status=AgentRunStatus.failed,
                publication_status="publication_failed",
                error_type=AgentRunErrorType.publication_failed,
                error_message=message[:1024],
                finished_at=now,
            )
        )


async def reconcile_durable_delivery() -> None:
    """Recover expired dispatch/worker leases and backfill durable events."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    visibility_cutoff = now - timedelta(seconds=settings.outbox_dispatch_visibility_seconds)
    factory = get_session_factory()
    recovered_count = 0
    expired_count = 0
    async with factory() as db:
        await ensure_durable_events(db)

        expired_dispatches = await db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.status == OUTBOX_DISPATCHING,
                OutboxEvent.dispatch_lease_expires_at.is_not(None),
                OutboxEvent.dispatch_lease_expires_at < now,
            )
        )
        for event in expired_dispatches:
            expired_count += 1
            event.dispatch_token = None
            event.dispatch_lease_expires_at = None
            if event.dispatch_attempt_count >= settings.outbox_max_attempts:
                event.status = OUTBOX_DEAD
                event.last_error = "dispatcher attempts exhausted"
                await _mark_dead_workflow(db, event, "Dispatcher attempts exhausted")
            else:
                event.status = OUTBOX_PENDING
                event.next_attempt_at = now
                event.last_error = "dispatcher lease expired"
            metrics.inc("agentdock_outbox_leases_expired_total")
            await persist_metric("agentdock_outbox_leases_expired_total")
        stale_dispatched = await db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.status == OUTBOX_DISPATCHED,
                OutboxEvent.claim_token.is_(None),
                OutboxEvent.dispatched_at.is_not(None),
                OutboxEvent.dispatched_at < visibility_cutoff,
            )
        )
        for event in stale_dispatched:
            recovered_count += 1
            if event.dispatch_attempt_count >= settings.outbox_max_attempts:
                event.status = OUTBOX_DEAD
                event.last_error = "worker delivery visibility attempts exhausted"
                await _mark_dead_workflow(db, event, "Worker delivery visibility attempts exhausted")
            else:
                event.status = OUTBOX_PENDING
                event.dispatched_at = None
                event.next_attempt_at = now
                event.last_error = "worker delivery visibility expired"
            metrics.inc("agentdock_outbox_recovered_total")
            await persist_metric("agentdock_outbox_recovered_total")

        expired = await db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.status == OUTBOX_PROCESSING,
                OutboxEvent.lease_expires_at.is_not(None),
                OutboxEvent.lease_expires_at < now,
            )
        )
        for event in expired:
            expired_count += 1
            if event.attempt_count >= settings.outbox_max_attempts:
                event.status = OUTBOX_DEAD
                event.last_error = "worker delivery attempts exhausted"
                event.claim_token = None
                event.claimed_by = None
                event.lease_expires_at = None
                await _mark_dead_workflow(db, event, "Worker delivery attempts exhausted")
            else:
                event.status = OUTBOX_PENDING
                event.claim_token = None
                event.claimed_by = None
                event.lease_expires_at = None
                event.next_attempt_at = now
                event.last_error = "worker lease expired"
            metrics.inc("agentdock_outbox_leases_expired_total")
            metrics.inc("agentdock_outbox_recovered_total")
            await persist_metric("agentdock_outbox_leases_expired_total")
            await persist_metric("agentdock_outbox_recovered_total")
        await db.commit()
    if recovered_count or expired_count:
        logger.info(
            "outbox.reconciled",
            recovered_jobs=recovered_count,
            expired_leases=expired_count,
        )


async def dispatch_pending_outbox() -> int:
    """Publish claimed PostgreSQL outbox rows to Redis after durable selection."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    factory = get_session_factory()
    async with factory() as db:
        rows = list(
            await db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == OUTBOX_PENDING,
                    OutboxEvent.next_attempt_at <= now,
                    OutboxEvent.dispatch_attempt_count < settings.outbox_max_attempts,
                    OutboxEvent.processed_at.is_(None),
                )
                .order_by(OutboxEvent.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(settings.outbox_dispatch_batch_size)
            )
        )
        claimed: list[tuple[UUID, str]] = []
        for event in rows:
            token = uuid4().hex
            event.status = OUTBOX_DISPATCHING
            event.dispatch_token = token
            event.dispatch_lease_expires_at = now + timedelta(seconds=settings.outbox_dispatch_lease_seconds)
            event.dispatch_attempt_count += 1
            claimed.append((event.id, token))
        await db.commit()

    delivered = 0
    for event_id, token in claimed:
        try:
            await enqueue_outbox_event(event_id)
        except Exception as exc:  # noqa: BLE001
            message = safe_error(exc, 1024) or "Redis dispatch failed"
            async with factory() as db:
                event = await db.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.id == event_id,
                        OutboxEvent.status == OUTBOX_DISPATCHING,
                        OutboxEvent.dispatch_token == token,
                    )
                )
                if event is not None:
                    exhausted = event.dispatch_attempt_count >= settings.outbox_max_attempts
                    event.status = OUTBOX_DEAD if exhausted else OUTBOX_PENDING
                    event.dispatch_token = None
                    event.dispatch_lease_expires_at = None
                    event.next_attempt_at = datetime.now(timezone.utc) + _backoff(event.dispatch_attempt_count)
                    event.last_error = message
                    if exhausted:
                        await _mark_dead_workflow(db, event, "Redis dispatch attempts exhausted")
                await db.commit()
            logger.warning(
                "outbox.dispatch_failed",
                outbox_event_id=str(event_id),
                error_class=classify_error(exc),
                retryable=True,
            )
            metrics.inc("agentdock_outbox_dispatch_failures_total")
            await persist_metric("agentdock_outbox_dispatch_failures_total")
            continue
        async with factory() as db:
            changed = await db.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.status == OUTBOX_DISPATCHING,
                    OutboxEvent.dispatch_token == token,
                )
                .values(
                    status=OUTBOX_DISPATCHED,
                    dispatch_token=None,
                    dispatch_lease_expires_at=None,
                    dispatched_at=datetime.now(timezone.utc),
                    last_error=None,
                )
            )
            await db.commit()
            if changed.rowcount:
                delivered += 1
                metrics.inc("agentdock_outbox_dispatched_total")
                await persist_metric("agentdock_outbox_dispatched_total")
    return delivered


async def claim_outbox_event(event_id: UUID, *, worker_id: str | None = None) -> DeliveryClaim | None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    token = uuid4().hex
    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.status.in_([OUTBOX_PENDING, OUTBOX_DISPATCHING, OUTBOX_DISPATCHED]),
                OutboxEvent.processed_at.is_(None),
                OutboxEvent.attempt_count < settings.outbox_max_attempts,
                or_(OutboxEvent.claim_token.is_(None), OutboxEvent.lease_expires_at < now),
            )
            .values(
                status=OUTBOX_PROCESSING,
                claim_token=token,
                claimed_by=worker_id or _worker_id(),
                lease_expires_at=now + timedelta(seconds=settings.outbox_worker_lease_seconds),
                started_at=func.coalesce(OutboxEvent.started_at, now),
                attempt_count=OutboxEvent.attempt_count + 1,
            )
            .returning(OutboxEvent.event_type, OutboxEvent.aggregate_id, OutboxEvent.attempt_count, OutboxEvent.payload)
        )
        row = result.first()
        await db.commit()
        if row is None:
            return None
        payload = row.payload if isinstance(row.payload, dict) else {}
        metrics.inc("agentdock_outbox_delivery_attempts_total")
        await persist_metric("agentdock_outbox_delivery_attempts_total")
        return DeliveryClaim(
            event_id,
            row.event_type,
            row.aggregate_id,
            token,
            row.attempt_count,
            workflow_correlation_id=str(payload.get("workflow_correlation_id"))
            if payload.get("workflow_correlation_id")
            else None,
            request_id=str(payload.get("request_id")) if payload.get("request_id") else None,
        )


async def bind_workflow_claim(claim: DeliveryClaim) -> bool:
    factory = get_session_factory()
    async with factory() as db:
        if claim.event_type == EXECUTION_REQUESTED:
            changed = await db.execute(
                update(ExecutionJob)
                .where(ExecutionJob.id == claim.aggregate_id, ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)))
                .values(delivery_claim_token=claim.token)
            )
        elif claim.event_type in {AGENT_RUN_REQUESTED, PUBLICATION_REQUESTED}:
            conditions = [AgentRun.id == claim.aggregate_id]
            if claim.event_type == AGENT_RUN_REQUESTED:
                conditions.append(AgentRun.status.in_(tuple(AGENT_ACTIVE)))
            else:
                conditions.extend(
                    [
                        AgentRun.approval_status == "approved",
                        AgentRun.publication_status.in_(["approved", "publication_failed", "publishing"]),
                        AgentRun.publication_artifact_status == "ready",
                    ]
                )
            changed = await db.execute(update(AgentRun).where(*conditions).values(delivery_claim_token=claim.token))
        else:
            await db.rollback()
            return False
        await db.commit()
        return changed.rowcount == 1


async def heartbeat_outbox_claim(claim: DeliveryClaim) -> None:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as db:
        changed = await db.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == claim.event_id,
                OutboxEvent.status == OUTBOX_PROCESSING,
                OutboxEvent.claim_token == claim.token,
            )
            .values(lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.outbox_worker_lease_seconds))
        )
        await db.commit()
    if changed.rowcount != 1:
        raise DeliveryClaimLost("delivery claim lost")


async def mark_outbox_processed(claim: DeliveryClaim) -> None:
    factory = get_session_factory()
    async with factory() as db:
        changed = await db.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == claim.event_id,
                OutboxEvent.status == OUTBOX_PROCESSING,
                OutboxEvent.claim_token == claim.token,
            )
            .values(
                status=OUTBOX_PROCESSED,
                processed_at=datetime.now(timezone.utc),
                claim_token=None,
                claimed_by=None,
                lease_expires_at=None,
                last_error=None,
            )
        )
        await db.commit()
    if changed.rowcount != 1:
        raise DeliveryClaimLost("delivery claim lost before completion")
    metrics.inc("agentdock_outbox_processed_total")
    await persist_metric("agentdock_outbox_processed_total")
    logger.info(
        "outbox.processed",
        outbox_event_id=str(claim.event_id),
        delivery_attempt=claim.attempt_count,
        claim_ref=claim.claim_reference,
        retryable=False,
    )


async def mark_outbox_retry(claim: DeliveryClaim, error: str, *, retryable: bool = True) -> None:
    settings = get_settings()
    error = safe_error(error, 1024)
    factory = get_session_factory()
    async with factory() as db:
        event = await db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.id == claim.event_id,
                OutboxEvent.status == OUTBOX_PROCESSING,
                OutboxEvent.claim_token == claim.token,
            )
        )
        if event is None:
            await db.rollback()
            return
        exhausted = not retryable or event.attempt_count >= settings.outbox_max_attempts
        event.status = OUTBOX_DEAD if exhausted else OUTBOX_PENDING
        event.next_attempt_at = datetime.now(timezone.utc) + _backoff(event.attempt_count)
        event.last_error = error[:1024]
        event.claim_token = None
        event.claimed_by = None
        event.lease_expires_at = None
        if exhausted:
            await _mark_dead_workflow(db, event, error)
        await db.commit()
    metrics.inc("agentdock_outbox_retries_total" if not exhausted else "agentdock_outbox_dead_total")
    await persist_metric("agentdock_outbox_retries_total" if not exhausted else "agentdock_outbox_dead_total")
    logger.warning(
        "outbox.retry_scheduled" if not exhausted else "outbox.retry_exhausted",
        outbox_event_id=str(claim.event_id),
        delivery_attempt=claim.attempt_count,
        error_class=classify_error(error),
        error_message=safe_error(error),
        retryable=retryable and not exhausted,
    )


async def process_outbox_event(event_id: UUID, provider, llm=None) -> None:
    """Claim one delivery and route it to the existing idempotent workflow."""
    from src.agent.loop import process_agent_run
    from src.processor import process_job
    from src.publication import process_publication

    claim = await claim_outbox_event(event_id)
    if claim is None:
        return
    bind_observability(
        workflow_correlation_id=claim.workflow_correlation_id,
        outbox_event_id=str(claim.event_id),
        delivery_attempt=claim.attempt_count,
        claim_ref=claim.claim_reference,
        request_id=claim.request_id,
        worker_id=_worker_id(),
    )
    logger.info(
        "outbox.lease_acquired",
        outbox_event_id=str(claim.event_id),
        delivery_attempt=claim.attempt_count,
        claim_ref=claim.claim_reference,
        worker_id=_worker_id(),
    )
    if claim.event_type not in SUPPORTED_EVENT_TYPES:
        try:
            await mark_outbox_retry(claim, f"unsupported outbox event type: {claim.event_type}", retryable=False)
        finally:
            clear_observability()
        return
    try:
        bound = await bind_workflow_claim(claim)
    except Exception:
        clear_observability()
        raise
    if not bound:
        try:
            await mark_outbox_processed(claim)
        finally:
            clear_observability()
        return

    stop = asyncio.Event()

    async def heartbeat() -> None:
        settings = get_settings()
        interval = max(1, settings.outbox_worker_lease_seconds // 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                await heartbeat_outbox_claim(claim)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        if claim.event_type == EXECUTION_REQUESTED:
            await process_job(claim.aggregate_id, provider, delivery_claim=claim)
        elif claim.event_type == AGENT_RUN_REQUESTED:
            await process_agent_run(claim.aggregate_id, provider, llm=llm, delivery_claim=claim)
        elif claim.event_type == PUBLICATION_REQUESTED:
            await process_publication(claim.aggregate_id, provider, delivery_claim=claim)
        else:  # guarded above; keep this branch defensive for future event types
            raise RuntimeError(f"unsupported outbox event type: {claim.event_type}")

        if claim.event_type == PUBLICATION_REQUESTED:
            factory = get_session_factory()
            async with factory() as db:
                run = await db.get(AgentRun, claim.aggregate_id)
                publication_retry = bool(run and run.publication_status == "publication_failed")
            if publication_retry:
                await mark_outbox_retry(claim, "publication failed", retryable=True)
            else:
                await mark_outbox_processed(claim)
        else:
            await mark_outbox_processed(claim)
    except DeliveryClaimLost:
        metrics.inc("agentdock_outbox_claim_lost_total")
        await persist_metric("agentdock_outbox_claim_lost_total")
        logger.warning(
            "outbox.claim_lost",
            outbox_event_id=str(event_id),
            delivery_attempt=claim.attempt_count,
            claim_ref=claim.claim_reference,
            error_class="delivery_claim_lost",
            retryable=True,
        )
    except Exception as exc:  # noqa: BLE001
        await mark_outbox_retry(claim, safe_error(exc) or "workflow delivery failed", retryable=True)
        logger.exception(
            "outbox.workflow_failed",
            outbox_event_id=str(event_id),
            delivery_attempt=claim.attempt_count,
            claim_ref=claim.claim_reference,
            error_class=classify_error(exc),
            retryable=True,
        )
    finally:
        stop.set()
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        clear_observability()
