"""Agent run realtime events — Redis Pub/Sub with per-run sequences.

Postgres remains authoritative. Pub/Sub is for live UI only; clients recover via REST.

Durable vs ephemeral:
- Durable: run status, steps, validation, changed files, diff metadata, errors
  (persisted in Postgres; events notify clients).
- Ephemeral: partial command stdout/stderr chunks (may be dropped under backpressure;
  final bounded logs still live in agent_steps / tool summaries).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.core.observability import metrics, persist_metric
from app.db.redis import get_redis

logger = get_logger(__name__)

EVENT_VERSION = 1
CHANNEL_PREFIX = "agentdock:run:"
SEQ_PREFIX = "agentdock:runseq:"
WS_USER_PREFIX = "agentdock:ws:user:"
WS_RUN_PREFIX = "agentdock:ws:run:"

KNOWN_EVENTS = frozenset(
    {
        "agent.snapshot",
        "agent.ping",
        "agent.pong",
        "agent.run.queued",
        "agent.run.started",
        "agent.run.status",
        "agent.step.started",
        "agent.step.completed",
        "agent.tool.started",
        "agent.tool.completed",
        "agent.command.output",
        "agent.validation.started",
        "agent.validation.completed",
        "agent.files.changed",
        "agent.diff.ready",
        "agent.run.cancelled",
        "agent.run.failed",
        "agent.run.completed",
        "agent.run.timed_out",
        "agent.run.step_limit_reached",
        "agent.run.repository_revoked",
        "agent.approval.required",
        "agent.approved",
        "agent.rejected",
        "publication.started",
        "publication.validation.started",
        "publication.validation.completed",
        "publication.commit.created",
        "publication.branch.pushed",
        "publication.pr.created",
        "publication.failed",
    }
)

EPHEMERAL_EVENTS = frozenset({"agent.command.output", "agent.ping", "agent.pong"})

_ws_active = 0
_events_published = 0
_event_publish_failures = 0
_ws_disconnects = 0

_ACQUIRE_WS_SLOT_LUA = """
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local slot = ARGV[3]
local max_user = tonumber(ARGV[4])
local max_run = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= max_user or redis.call('ZCARD', KEYS[2]) >= max_run then
  return 0
end
redis.call('ZADD', KEYS[1], expires, slot)
redis.call('ZADD', KEYS[2], expires, slot)
redis.call('EXPIRE', KEYS[1], math.ceil(expires - now))
redis.call('EXPIRE', KEYS[2], math.ceil(expires - now))
return 1
"""


def channel_for_run(run_id: UUID | str) -> str:
    return f"{CHANNEL_PREFIX}{run_id}"


def seq_key_for_run(run_id: UUID | str) -> str:
    return f"{SEQ_PREFIX}{run_id}"


def metrics_snapshot() -> dict[str, int]:
    return {
        "websocket_active_connections": _ws_active,
        "agent_events_published_total": _events_published,
        "agent_event_publish_failures_total": _event_publish_failures,
        "websocket_disconnects_total": _ws_disconnects,
    }


def ws_connected() -> None:
    global _ws_active
    _ws_active += 1
    metrics.set_gauge("websocket_active_connections", _ws_active)


def ws_disconnected() -> None:
    global _ws_active, _ws_disconnects
    _ws_active = max(0, _ws_active - 1)
    _ws_disconnects += 1
    metrics.set_gauge("websocket_active_connections", _ws_active)
    metrics.inc("websocket_disconnects_total")


def build_event(
    *,
    event: str,
    run_id: UUID | str,
    sequence: int,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not event or not isinstance(event, str):
        raise ValueError("event name required")
    return {
        "version": EVENT_VERSION,
        "event": event,
        "run_id": str(run_id),
        "sequence": sequence,
        "timestamp": datetime.now(UTC).isoformat(),
        "data": data or {},
    }


def serialize_event(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _validate_event(event: str, data: dict[str, Any] | None) -> None:
    if not event or not isinstance(event, str):
        raise ValueError("invalid event")
    if data is not None and not isinstance(data, dict):
        raise ValueError("data must be a dict")


async def publish_agent_event(
    run_id: UUID | str,
    event: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Publish a versioned event. Never raises into the agent loop."""
    global _events_published, _event_publish_failures
    try:
        _validate_event(event, data)
        redis = get_redis()
        sequence = int(await redis.incr(seq_key_for_run(run_id)))
        if sequence == 1:
            await redis.expire(seq_key_for_run(run_id), 86_400)
        payload = build_event(event=event, run_id=run_id, sequence=sequence, data=data)
        await redis.publish(channel_for_run(run_id), serialize_event(payload))
        _events_published += 1
        metrics.inc("agent_events_published_total")
        await persist_metric("agent_events_published_total")
        logger.info(
            "agent.event.published",
            event_name=event,
            agent_run_id=str(run_id),
            sequence=sequence,
        )
        return payload
    except Exception:
        _event_publish_failures += 1
        metrics.inc("agent_event_publish_failures_total")
        await persist_metric("agent_event_publish_failures_total")
        logger.warning(
            "agent.event.publish_failed",
            event_name=event,
            agent_run_id=str(run_id),
            retryable=True,
        )
        return None


_sync_redis = None


def _get_sync_redis():
    """Sync client for publishing from blocking sandbox exec callbacks."""
    global _sync_redis
    if _sync_redis is None:
        import redis as redis_sync

        from app.core.config import get_settings

        _sync_redis = redis_sync.from_url(get_settings().redis_url, decode_responses=True)
    return _sync_redis


def publish_agent_event_sync(
    run_id: UUID | str,
    event: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Sync publish for blocking worker paths. Never raises."""
    global _events_published, _event_publish_failures
    try:
        _validate_event(event, data)
        r = _get_sync_redis()
        sequence = int(r.incr(seq_key_for_run(run_id)))
        if sequence == 1:
            r.expire(seq_key_for_run(run_id), 86_400)
        payload = build_event(event=event, run_id=run_id, sequence=sequence, data=data)
        r.publish(channel_for_run(run_id), serialize_event(payload))
        _events_published += 1
        metrics.inc("agent_events_published_total")
        logger.info(
            "agent.event.published",
            event_name=event,
            agent_run_id=str(run_id),
            sequence=sequence,
        )
        return payload
    except Exception:
        _event_publish_failures += 1
        metrics.inc("agent_event_publish_failures_total")
        logger.warning(
            "agent.event.publish_failed",
            event_name=event,
            agent_run_id=str(run_id),
            retryable=True,
        )
        return None


async def acquire_ws_slot(
    user_id: UUID | str,
    run_id: UUID | str,
    *,
    max_user: int,
    max_run: int,
    ttl_seconds: int = 3600,
    slot_id: str | None = None,
) -> bool:
    redis = get_redis()
    user_key = f"{WS_USER_PREFIX}{user_id}"
    run_key = f"{WS_RUN_PREFIX}{run_id}"
    if slot_id is not None:
        now = time.time()
        expires = now + ttl_seconds
        result = await redis.eval(
            _ACQUIRE_WS_SLOT_LUA,
            2,
            user_key,
            run_key,
            now,
            expires,
            slot_id,
            max_user,
            max_run,
        )
        return bool(result)

    # Compatibility path for callers that do not provide a per-socket lease.
    # WebSocket connections always use the sorted-set path above.
    user_n = int(await redis.incr(user_key))
    await redis.expire(user_key, ttl_seconds)
    if user_n > max_user:
        await redis.decr(user_key)
        return False
    run_n = int(await redis.incr(run_key))
    await redis.expire(run_key, ttl_seconds)
    if run_n > max_run:
        await redis.decr(run_key)
        await redis.decr(user_key)
        return False
    return True


async def renew_ws_slot(
    user_id: UUID | str,
    run_id: UUID | str,
    *,
    ttl_seconds: int = 3600,
    slot_id: str | None = None,
) -> bool:
    """Renew both quota leases; false means a lease disappeared unexpectedly."""
    redis = get_redis()
    user_key = f"{WS_USER_PREFIX}{user_id}"
    run_key = f"{WS_RUN_PREFIX}{run_id}"
    if slot_id is not None:
        now = time.time()
        for key in (user_key, run_key):
            await redis.zremrangebyscore(key, 0, now)
        user_score = await redis.zscore(user_key, slot_id)
        run_score = await redis.zscore(run_key, slot_id)
        if user_score is None or run_score is None:
            return False
        expires = now + ttl_seconds
        await redis.zadd(user_key, {slot_id: expires})
        await redis.zadd(run_key, {slot_id: expires})
        await redis.expire(user_key, ttl_seconds)
        await redis.expire(run_key, ttl_seconds)
        return True
    user_exists = bool(await redis.exists(user_key))
    run_exists = bool(await redis.exists(run_key))
    if not user_exists or not run_exists:
        return False
    return bool(await redis.expire(user_key, ttl_seconds)) and bool(await redis.expire(run_key, ttl_seconds))


async def release_ws_slot(user_id: UUID | str, run_id: UUID | str, *, slot_id: str | None = None) -> None:
    redis = get_redis()
    if slot_id is not None:
        try:
            await redis.zrem(f"{WS_USER_PREFIX}{user_id}", slot_id)
            await redis.zrem(f"{WS_RUN_PREFIX}{run_id}", slot_id)
        except Exception:
            pass
        return
    for key in (f"{WS_USER_PREFIX}{user_id}", f"{WS_RUN_PREFIX}{run_id}"):
        try:
            n = int(await redis.decr(key))
            if n <= 0:
                await redis.delete(key)
        except Exception:
            pass


class AgentEventPublisher:
    """Thin wrapper used by the worker — no WebSocket knowledge."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id

    async def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        await publish_agent_event(self.run_id, event, data)

    def publish_sync(self, event: str, data: dict[str, Any] | None = None) -> None:
        publish_agent_event_sync(self.run_id, event, data)


def safe_tool_summary(name: str, arguments: dict[str, Any] | None) -> str:
    args = arguments or {}
    if name == "list_files":
        return f"Listing files in {args.get('path', '.')}"
    if name == "read_file":
        return f"Reading {args.get('path', '?')}"
    if name == "search_code":
        q = str(args.get("query", ""))[:80]
        return f'Searching for "{q}"'
    if name == "write_file":
        return f"Editing {args.get('path', '?')}"
    if name == "apply_patch":
        return "Applying patch"
    if name == "run_command":
        cmd = args.get("command") or []
        if isinstance(cmd, list):
            return "Running " + " ".join(str(c) for c in cmd[:8])
        return "Running command"
    if name == "git_status":
        return "Inspecting git status"
    if name == "git_diff":
        return "Capturing git diff"
    if name == "finish":
        return "Finishing run"
    return f"Tool {name}"
