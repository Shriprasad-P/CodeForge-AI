from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import bind_observability, clear_observability, metrics, normalize_request_id
from app.db.redis import get_redis
from app.db.session import get_session_factory
from app.models.agent_run import AgentRun
from app.services import auth as auth_service
from app.services.agent_events import (
    EPHEMERAL_EVENTS,
    build_event,
    channel_for_run,
    acquire_ws_slot,
    release_ws_slot,
    renew_ws_slot,
    serialize_event,
    ws_connected,
    ws_disconnected,
)

router = APIRouter(tags=["websocket"])
logger = get_logger(__name__)

WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_NOT_FOUND = 4404
WS_CLOSE_LIMIT = 4429
WS_CLOSE_BAD_VERSION = 4400
WS_CLOSE_SESSION_REVOKED = 4401

MAX_CLIENT_QUEUE = 256
HEARTBEAT_SECONDS = 25


async def _user_from_websocket(websocket: WebSocket):
    settings = get_settings()
    token = websocket.cookies.get(settings.session_cookie_name)
    factory = get_session_factory()
    async with factory() as db:
        row = await auth_service.get_session_for_token(db, token)
        if row is None:
            return None
        session, user = row
        return user, session.id


@router.websocket("/ws/agent-runs/{run_id}")
async def agent_run_ws(websocket: WebSocket, run_id: UUID) -> None:
    settings = get_settings()
    request_id = normalize_request_id(websocket.headers.get("X-Request-ID"))
    bind_observability(request_id=request_id, agent_run_id=str(run_id))
    auth = await _user_from_websocket(websocket)
    if auth is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="Not authenticated")
        metrics.inc("websocket_rejected_total")
        logger.info("websocket.unauthorized", agent_run_id=str(run_id))
        clear_observability()
        return
    user, session_id = auth

    factory = get_session_factory()
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            await websocket.close(code=WS_CLOSE_NOT_FOUND, reason="Run not found")
            metrics.inc("websocket_rejected_total")
            clear_observability()
            return
        if run.user_id != user.id:
            await websocket.close(code=WS_CLOSE_FORBIDDEN, reason="Forbidden")
            metrics.inc("websocket_rejected_total")
            logger.info("websocket.unauthorized", agent_run_id=str(run_id), reason="idor")
            clear_observability()
            return
        bind_observability(
            agent_run_id=str(run.id),
            workflow_correlation_id=str(run.workflow_correlation_id),
            repository_connection_id=str(run.repository_connection_id),
            user_id=str(user.id),
        )
        snapshot = {
            "status": run.status.value,
            "steps_used": run.steps_used,
            "tool_calls_used": run.tool_calls_used,
            "cancel_requested": run.cancel_requested,
            "summary": run.summary,
            "result_status": run.result_status,
            "changed_files": run.changed_files or [],
            "error_type": run.error_type.value if run.error_type else None,
            "error_message": run.error_message,
            "validation": run.validation,
            "diff_stat": run.diff_stat,
            "diff_hash": run.diff_hash,
            "diff_truncated": run.diff_truncated,
            "publication_artifact_hash": run.publication_artifact_hash,
            "publication_artifact_size": run.publication_artifact_size,
            "publication_artifact_version": run.publication_artifact_version,
            "publication_artifact_status": run.publication_artifact_status,
            "publication_change_manifest": run.publication_change_manifest or [],
            "base_commit_sha": run.base_commit_sha,
            "approval_status": run.approval_status,
            "publication_status": run.publication_status,
            "branch_name": run.branch_name,
            "commit_sha": run.commit_sha,
            "github_pr_number": run.github_pr_number,
            "github_pr_url": run.github_pr_url,
            "approval_eligible": (
                run.status.value == "awaiting_approval"
                and run.approval_status == "pending"
                and run.publication_artifact_status == "ready"
            ),
        }

    slot_id = uuid4().hex
    got_slot = await acquire_ws_slot(
        user.id,
        run_id,
        max_user=settings.ws_max_connections_per_user,
        max_run=settings.ws_max_connections_per_run,
        ttl_seconds=settings.ws_slot_ttl_seconds,
        slot_id=slot_id,
    )
    if not got_slot:
        await websocket.close(code=WS_CLOSE_LIMIT, reason="Connection limit")
        metrics.inc("websocket_quota_rejections_total")
        clear_observability()
        return

    await websocket.accept()
    ws_connected()
    metrics.inc("websocket_connections_total")
    logger.info("websocket.connected", agent_run_id=str(run_id), user_id=str(user.id))

    outbound: asyncio.Queue[str | None] = asyncio.Queue(maxsize=MAX_CLIENT_QUEUE)
    dropped_ephemeral = 0

    async def enqueue(raw: str, *, ephemeral: bool = False) -> None:
        nonlocal dropped_ephemeral
        try:
            outbound.put_nowait(raw)
        except asyncio.QueueFull:
            if ephemeral:
                dropped_ephemeral += 1
                return
            try:
                outbound.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                outbound.put_nowait(raw)
            except asyncio.QueueFull:
                pass

    # Local snapshot — do not fan out via Redis (REST holds full history).
    await enqueue(
        serialize_event(build_event(event="agent.snapshot", run_id=run_id, sequence=0, data=snapshot)),
        ephemeral=False,
    )

    redis = get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_for_run(run_id))

    async def reader() -> None:
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    if not isinstance(data, str):
                        continue
                    ephemeral = False
                    try:
                        parsed = json.loads(data)
                        ephemeral = parsed.get("event") in EPHEMERAL_EVENTS
                    except Exception:
                        ephemeral = "agent.command.output" in data
                    await enqueue(data, ephemeral=ephemeral)
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("websocket.redis_reader_failed", agent_run_id=str(run_id), retryable=True)
            try:
                await websocket.close(code=1011, reason="Realtime stream unavailable")
            except Exception:
                pass

    async def writer() -> None:
        while True:
            item = await outbound.get()
            if item is None:
                return
            await websocket.send_text(item)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            ping = build_event(event="agent.ping", run_id=run_id, sequence=0, data={})
            ping["timestamp"] = datetime.now(UTC).isoformat()
            await enqueue(serialize_event(ping), ephemeral=True)

    async def renew_lease() -> None:
        while True:
            await asyncio.sleep(settings.ws_slot_renew_interval_seconds)
            try:
                renewed = await renew_ws_slot(
                    user.id,
                    run_id,
                    ttl_seconds=settings.ws_slot_ttl_seconds,
                    slot_id=slot_id,
                )
            except Exception:
                renewed = False
                logger.warning("websocket.lease_renew_failed", agent_run_id=str(run_id), retryable=True)
            if not renewed:
                try:
                    await websocket.close(code=1011, reason="Connection lease expired")
                except Exception:
                    pass
                return

    async def watch_session() -> None:
        while True:
            await asyncio.sleep(settings.ws_session_revalidate_seconds)
            try:
                async with factory() as session_db:
                    active = await auth_service.get_active_session_for_id(session_db, session_id)
            except Exception:
                logger.warning("websocket.session_check_failed", agent_run_id=str(run_id), retryable=True)
                continue
            if active is None:
                try:
                    await websocket.close(code=WS_CLOSE_SESSION_REVOKED, reason="Session revoked")
                except Exception:
                    pass
                return

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    heartbeat_task = asyncio.create_task(heartbeat())
    lease_task = asyncio.create_task(renew_lease())
    session_task = asyncio.create_task(watch_session())

    try:
        while True:
            msg = await websocket.receive_text()
            # Visibility only — ignore tool/control commands from the browser.
            if msg.strip() in {"ping", '{"type":"ping"}'}:
                pong = build_event(event="agent.pong", run_id=run_id, sequence=0, data={})
                await enqueue(serialize_event(pong), ephemeral=True)
    except WebSocketDisconnect:
        logger.info(
            "websocket.disconnected",
            agent_run_id=str(run_id),
            dropped_ephemeral=dropped_ephemeral,
        )
    except Exception:
        metrics.inc("websocket_errors_total")
        logger.warning("websocket.error", agent_run_id=str(run_id), retryable=False)
        try:
            await websocket.close(code=1011, reason="Server error")
        except Exception:
            pass
    finally:
        heartbeat_task.cancel()
        lease_task.cancel()
        session_task.cancel()
        reader_task.cancel()
        try:
            outbound.put_nowait(None)
        except asyncio.QueueFull:
            pass
        writer_task.cancel()
        try:
            await pubsub.unsubscribe(channel_for_run(run_id))
            await pubsub.aclose()
        except Exception:
            pass
        await release_ws_slot(user.id, run_id, slot_id=slot_id)
        ws_disconnected()
        clear_observability()
