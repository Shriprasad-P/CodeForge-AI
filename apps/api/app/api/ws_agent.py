from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import get_settings
from app.core.logging import get_logger
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

MAX_CLIENT_QUEUE = 256
HEARTBEAT_SECONDS = 25


async def _user_from_websocket(websocket: WebSocket):
    settings = get_settings()
    token = websocket.cookies.get(settings.session_cookie_name)
    factory = get_session_factory()
    async with factory() as db:
        return await auth_service.get_user_for_token(db, token)


@router.websocket("/ws/agent-runs/{run_id}")
async def agent_run_ws(websocket: WebSocket, run_id: UUID) -> None:
    settings = get_settings()
    user = await _user_from_websocket(websocket)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="Not authenticated")
        logger.info("websocket.unauthorized", run_id=str(run_id))
        return

    factory = get_session_factory()
    async with factory() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            await websocket.close(code=WS_CLOSE_NOT_FOUND, reason="Run not found")
            return
        if run.user_id != user.id:
            await websocket.close(code=WS_CLOSE_FORBIDDEN, reason="Forbidden")
            logger.info("websocket.unauthorized", run_id=str(run_id), reason="idor")
            return
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
        }

    got_slot = await acquire_ws_slot(
        user.id,
        run_id,
        max_user=settings.ws_max_connections_per_user,
        max_run=settings.ws_max_connections_per_run,
    )
    if not got_slot:
        await websocket.close(code=WS_CLOSE_LIMIT, reason="Connection limit")
        return

    await websocket.accept()
    ws_connected()
    logger.info("websocket.connected", run_id=str(run_id), user_id=str(user.id))

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
            logger.warning("websocket.redis_reader_failed", run_id=str(run_id))

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

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    heartbeat_task = asyncio.create_task(heartbeat())

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
            run_id=str(run_id),
            dropped_ephemeral=dropped_ephemeral,
        )
    except Exception:
        logger.warning("websocket.error", run_id=str(run_id))
        try:
            await websocket.close(code=1011, reason="Server error")
        except Exception:
            pass
    finally:
        heartbeat_task.cancel()
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
        await release_ws_slot(user.id, run_id)
        ws_disconnected()
