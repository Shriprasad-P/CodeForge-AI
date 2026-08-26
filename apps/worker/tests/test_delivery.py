from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.db.redis import close_redis, init_redis
from app.db.session import close_db, get_session_factory, init_db
from app.auth.security import hash_password
from app.core.config import get_settings
from app.models.execution import ExecutionJob, ExecutionJobStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.services.execution import create_execution_job
from app.services.outbox import EXECUTION_REQUESTED, event_dedupe_key
from sandbox_sdk.docker_provider import DockerSandboxProvider
from src.delivery import (
    DeliveryClaimLost,
    OUTBOX_DISPATCHED,
    OUTBOX_PENDING,
    OUTBOX_PROCESSED,
    OUTBOX_PROCESSING,
    bind_workflow_claim,
    claim_outbox_event,
    dispatch_pending_outbox,
    mark_outbox_processed,
    process_outbox_event,
    reconcile_durable_delivery,
)


async def _start_infra():
    await init_db()
    await init_redis()
    return get_session_factory()


async def _stop_infra() -> None:
    await close_redis()
    await close_db()


def _event() -> OutboxEvent:
    aggregate_id = uuid4()
    return OutboxEvent(
        event_type="test.requested",
        aggregate_id=aggregate_id,
        dedupe_key=f"test.requested:{aggregate_id}",
        payload={"aggregate_id": str(aggregate_id)},
    )


async def _seed_execution(factory) -> tuple[object, object]:
    async with factory() as session:
        user = User(
            email=f"delivery-exec-{uuid4().hex[:8]}@example.com",
            display_name="Execution delivery",
            password_hash=hash_password("password123"),
            auth_provider="password",
        )
        session.add(user)
        await session.flush()
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=int(uuid4().int % 10_000_000),
            account_login="fixture",
            account_type="User",
            account_id=int(uuid4().int % 10_000_000),
            repository_selection="all",
        )
        session.add(installation)
        await session.flush()
        connection = RepositoryConnection(
            user_id=user.id,
            installation_id=installation.id,
            github_repository_id=int(uuid4().int % 10_000_000),
            owner="fixture",
            name="sample-repo",
            full_name="fixture/sample-repo",
            default_branch="main",
            private=False,
            html_url="https://github.com/fixture/sample-repo",
            is_active=True,
        )
        session.add(connection)
        await session.flush()
        job = ExecutionJob(
            user_id=user.id,
            repository_connection_id=connection.id,
            command=["python", "-c", "print('durable execution')"],
            status=ExecutionJobStatus.queued,
        )
        session.add(job)
        await session.flush()
        event = OutboxEvent(
            event_type=EXECUTION_REQUESTED,
            aggregate_id=job.id,
            dedupe_key=event_dedupe_key(EXECUTION_REQUESTED, job.id),
            payload={"execution_id": str(job.id)},
        )
        session.add(event)
        await session.commit()
        return job.id, event.id


async def _seed_owner_connection(factory) -> tuple[User, object]:
    async with factory() as session:
        user = User(
            email=f"delivery-api-{uuid4().hex[:8]}@example.com",
            display_name="API delivery",
            password_hash=hash_password("password123"),
            auth_provider="password",
        )
        session.add(user)
        await session.flush()
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=int(uuid4().int % 10_000_000),
            account_login="fixture",
            account_type="User",
            account_id=int(uuid4().int % 10_000_000),
            repository_selection="all",
        )
        session.add(installation)
        await session.flush()
        connection = RepositoryConnection(
            user_id=user.id,
            installation_id=installation.id,
            github_repository_id=int(uuid4().int % 10_000_000),
            owner="fixture",
            name="sample-repo",
            full_name="fixture/sample-repo",
            default_branch="main",
            private=False,
            html_url="https://github.com/fixture/sample-repo",
            is_active=True,
        )
        session.add(connection)
        await session.commit()
        return user, connection.id


@pytest.mark.asyncio
async def test_redis_outage_leaves_outbox_pending_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = await _start_infra()
    try:
        event = _event()
        async with factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id

        async def unavailable(_event_id):
            raise ConnectionError("Redis unavailable")

        monkeypatch.setattr("src.delivery.enqueue_outbox_event", unavailable)
        assert await dispatch_pending_outbox() == 0
        async with factory() as session:
            stored = await session.get(OutboxEvent, event_id)
            assert stored is not None
            assert stored.status == OUTBOX_PENDING
            assert stored.dispatch_attempt_count == 1
            assert stored.last_error == "Redis unavailable"
            stored.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        delivered: list = []

        async def available(event_id):
            delivered.append(event_id)

        monkeypatch.setattr("src.delivery.enqueue_outbox_event", available)
        assert await dispatch_pending_outbox() == 1
        assert delivered == [event_id]
        async with factory() as session:
            stored = await session.get(OutboxEvent, event_id)
            assert stored is not None
            assert stored.status == OUTBOX_DISPATCHED
    finally:
        await _stop_infra()


@pytest.mark.asyncio
async def test_api_commit_and_worker_completion_survive_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(root / "fixtures" / "sample-repo"))
    get_settings.cache_clear()
    factory = await _start_infra()
    try:
        user, connection_id = await _seed_owner_connection(factory)

        def redis_unavailable():
            raise ConnectionError("Redis unavailable after database commit")

        monkeypatch.setattr("app.services.execution.get_redis", redis_unavailable)
        async with factory() as session:
            job = await create_execution_job(
                session,
                user=user,
                repository_connection_id=connection_id,
                command=["python", "-c", "print('redis outage recovery')"],
                working_directory=None,
                agent_session_id=None,
            )
            job_id = job.id

        async with factory() as session:
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == EXECUTION_REQUESTED,
                    OutboxEvent.aggregate_id == job_id,
                )
            )
            assert event is not None
            event_id = event.id

        async def unavailable(_event_id):
            raise ConnectionError("Redis unavailable")

        monkeypatch.setattr("src.delivery.enqueue_outbox_event", unavailable)
        assert await dispatch_pending_outbox() == 0
        async with factory() as session:
            event = await session.get(OutboxEvent, event_id)
            assert event is not None
            assert event.status == OUTBOX_PENDING
            event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

        monkeypatch.setattr("src.delivery.enqueue_outbox_event", lambda _event_id: asyncio.sleep(0))
        assert await dispatch_pending_outbox() == 1
        await process_outbox_event(event_id, DockerSandboxProvider())

        async with factory() as session:
            finished = await session.get(ExecutionJob, job_id)
            delivery = await session.get(OutboxEvent, event_id)
            assert finished is not None
            assert delivery is not None
            assert finished.status == ExecutionJobStatus.succeeded
            assert delivery.status == OUTBOX_PROCESSED
    finally:
        await _stop_infra()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_concurrent_claim_has_one_owner_and_expiry_recovers() -> None:
    factory = await _start_infra()
    try:
        event = _event()
        async with factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id

        first, second = await asyncio.gather(
            claim_outbox_event(event_id, worker_id="worker-a"),
            claim_outbox_event(event_id, worker_id="worker-b"),
        )
        claims = [claim for claim in (first, second) if claim is not None]
        assert len(claims) == 1
        first_claim = claims[0]

        async with factory() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await session.commit()
        await reconcile_durable_delivery()

        recovered = await claim_outbox_event(event_id, worker_id="worker-recovery")
        assert recovered is not None
        assert recovered.token != first_claim.token
        assert recovered.attempt_count == first_claim.attempt_count + 1
        async with factory() as session:
            stored = await session.get(OutboxEvent, event_id)
            assert stored is not None
            assert stored.status == OUTBOX_PROCESSING
            assert stored.claimed_by == "worker-recovery"
    finally:
        await _stop_infra()


@pytest.mark.asyncio
async def test_stale_worker_cannot_complete_after_lease_recovery() -> None:
    factory = await _start_infra()
    try:
        event = _event()
        async with factory() as session:
            session.add(event)
            await session.commit()
            event_id = event.id

        first = await claim_outbox_event(event_id, worker_id="worker-crashed")
        assert first is not None
        async with factory() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await session.commit()
        await reconcile_durable_delivery()
        recovered = await claim_outbox_event(event_id, worker_id="worker-recovered")
        assert recovered is not None

        with pytest.raises(DeliveryClaimLost):
            await mark_outbox_processed(first)
        await mark_outbox_processed(recovered)
        async with factory() as session:
            stored = await session.get(OutboxEvent, event_id)
            assert stored is not None
            assert stored.status == OUTBOX_PROCESSED
            assert stored.claim_token is None
    finally:
        await _stop_infra()


@pytest.mark.asyncio
async def test_worker_crash_after_claim_recovers_and_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(root / "fixtures" / "sample-repo"))
    get_settings.cache_clear()
    factory = await _start_infra()
    try:
        job_id, event_id = await _seed_execution(factory)
        crashed = await claim_outbox_event(event_id, worker_id="worker-crashed")
        assert crashed is not None
        assert await bind_workflow_claim(crashed)

        # Deterministic crash injection: worker A exits after durable claim,
        # before invoking the workflow. No Redis requeue is performed.
        async with factory() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            await session.commit()
        await reconcile_durable_delivery()

        await process_outbox_event(event_id, DockerSandboxProvider())
        async with factory() as session:
            finished = await session.get(ExecutionJob, job_id)
            delivery = await session.get(OutboxEvent, event_id)
            assert finished is not None
            assert delivery is not None
            assert finished.status == ExecutionJobStatus.succeeded
            assert delivery.status == OUTBOX_PROCESSED
            assert delivery.attempt_count == 2
    finally:
        await _stop_infra()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_cancelled_active_execution_is_not_resurrected() -> None:
    factory = await _start_infra()
    try:
        job_id, event_id = await _seed_execution(factory)
        async with factory() as session:
            await session.execute(
                update(ExecutionJob)
                .where(ExecutionJob.id == job_id)
                .values(status=ExecutionJobStatus.running, cancel_requested=True)
            )
            await session.commit()

        await process_outbox_event(event_id, DockerSandboxProvider())
        async with factory() as session:
            finished = await session.get(ExecutionJob, job_id)
            delivery = await session.get(OutboxEvent, event_id)
            assert finished is not None
            assert delivery is not None
            assert finished.status == ExecutionJobStatus.cancelled
            assert delivery.status == OUTBOX_PROCESSED
    finally:
        await _stop_infra()


@pytest.mark.asyncio
async def test_durable_execution_delivery_is_terminal_and_duplicate_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(root / "fixtures" / "sample-repo"))
    get_settings.cache_clear()
    factory = await _start_infra()
    try:
        job_id, event_id = await _seed_execution(factory)

        await process_outbox_event(event_id, DockerSandboxProvider())
        async with factory() as session:
            finished = await session.get(ExecutionJob, job_id)
            delivery = await session.get(OutboxEvent, event_id)
            assert finished is not None
            assert delivery is not None
            assert finished.status == ExecutionJobStatus.succeeded
            assert "durable execution" in (finished.stdout or "")
            assert delivery.status == OUTBOX_PROCESSED

        await process_outbox_event(event_id, DockerSandboxProvider())
        async with factory() as session:
            duplicate = await session.get(ExecutionJob, job_id)
            assert duplicate is not None
            assert duplicate.status == ExecutionJobStatus.succeeded
    finally:
        await _stop_infra()
        get_settings.cache_clear()
