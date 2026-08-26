from __future__ import annotations

# Imports below intentionally follow runtime path setup for the monorepo layout.
# ruff: noqa: E402

import asyncio
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow importing the API package and sandbox SDK when run from Compose or locally.
_ROOT = Path(__file__).resolve().parents[2]
_API = _ROOT / "api"
_SDK = _ROOT.parent / "packages" / "sandbox-sdk"
for path in (_API, _SDK, Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(path))

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import metrics
from app.db.redis import close_redis, get_redis, init_redis
from app.db.session import close_db, init_db
from app.services.queue import dequeue_work
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.delivery import (
    AGENT_RUN_REQUESTED,
    EXECUTION_REQUESTED,
    PUBLICATION_REQUESTED,
    dispatch_pending_outbox,
    find_durable_event,
    process_outbox_event,
    reconcile_durable_delivery,
)
from src.processor import reconcile_stale_jobs
from src.runtime import shutdown_blocking_executor

configure_logging()
logger = get_logger(__name__)


def build_worker_heartbeat(
    worker_id: str,
    *,
    active_claims: int,
    last_success: str | None,
    version: str,
) -> dict[str, object]:
    return {
        "worker_id": worker_id,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "active_claims": max(0, active_claims),
        "last_success": last_success,
    }


async def worker_loop() -> None:
    settings = get_settings()
    await init_db()
    await init_redis()
    provider = DockerSandboxProvider()
    await reconcile_stale_jobs(provider)
    await reconcile_durable_delivery()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    logger.info(
        "worker.started",
        worker_id=worker_id,
        concurrency=settings.worker_concurrency,
        checkout_mode=settings.sandbox_checkout_mode,
        llm_provider=settings.llm_provider or "unset",
    )

    sem = asyncio.Semaphore(settings.worker_concurrency)
    tasks: set[asyncio.Task] = set()
    last_reconcile = 0.0
    heartbeat_key = f"agentdock:worker:heartbeat:{worker_id}"
    active_claims = 0
    last_success: str | None = None

    async def heartbeat() -> None:
        nonlocal last_success
        while True:
            payload = build_worker_heartbeat(
                worker_id,
                active_claims=active_claims,
                last_success=last_success,
                version=settings.app_name,
            )
            try:
                await get_redis().set(
                    heartbeat_key,
                    json.dumps(payload, separators=(",", ":")),
                    ex=max(5, settings.worker_heartbeat_ttl_seconds),
                )
                metrics.set_gauge("agentdock_worker_active_claims", active_claims)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "worker.heartbeat_failed",
                    worker_id=worker_id,
                    error_class=type(exc).__name__,
                    retryable=True,
                )
            await asyncio.sleep(max(1, settings.worker_heartbeat_interval_seconds))

    async def _run_outbox(event_id) -> None:
        nonlocal active_claims, last_success
        async with sem:
            active_claims += 1
            try:
                await process_outbox_event(event_id, provider)
                last_success = datetime.now(timezone.utc).isoformat()
            finally:
                active_claims = max(0, active_claims - 1)

    async def _run_legacy_notification(kind: str, aggregate_id) -> None:
        event_type = {
            "execution": EXECUTION_REQUESTED,
            "agent": AGENT_RUN_REQUESTED,
            "publication": PUBLICATION_REQUESTED,
        }[kind]
        event_id = await find_durable_event(event_type, aggregate_id)
        if event_id is not None:
            await _run_outbox(event_id)
        else:
            logger.info("worker.legacy_notification_ignored", kind=kind, aggregate_id=str(aggregate_id))

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        while True:
            now = asyncio.get_running_loop().time()
            if now - last_reconcile >= settings.outbox_reconcile_interval_seconds:
                try:
                    await reconcile_durable_delivery()
                except Exception:  # noqa: BLE001
                    logger.exception("outbox.reconcile_failed")
                last_reconcile = now
            try:
                await dispatch_pending_outbox()
            except Exception:  # noqa: BLE001
                logger.warning("outbox.dispatch_unavailable")
            try:
                item = await dequeue_work(timeout_seconds=1)
            except Exception:  # noqa: BLE001
                logger.warning("worker.redis_unavailable")
                await asyncio.sleep(1)
                continue
            if item is None:
                await asyncio.sleep(0.1)
                continue
            kind, work_id = item
            if kind == "outbox":
                task = asyncio.create_task(_run_outbox(work_id))
            else:
                task = asyncio.create_task(_run_legacy_notification(kind, work_id))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        try:
            await get_redis().delete(heartbeat_key)
        except Exception:
            pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await close_redis()
        await close_db()
        shutdown_blocking_executor()
        logger.info("worker.stopped")


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
