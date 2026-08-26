"""Small, shared observability primitives for API and worker processes.

This module deliberately has no external metrics dependency.  Metrics are
telemetry only; PostgreSQL remains the source of truth for workflow state.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import Any, Iterator
from uuid import UUID, uuid4

import structlog

CANONICAL_OBSERVABILITY_FIELDS = frozenset(
    {
        "request_id",
        "workflow_correlation_id",
        "agent_run_id",
        "execution_job_id",
        "repository_connection_id",
        "outbox_event_id",
        "delivery_attempt",
        "claim_ref",
        "worker_id",
        "publication_attempt_id",
        "event",
        "duration_ms",
        "error_class",
        "retryable",
        "state_from",
        "state_to",
        "user_id",
    }
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_request_id: ContextVar[str | None] = ContextVar("agentdock_request_id", default=None)
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+|token[=:\s]+|password[=:\s]+|secret[=:\s]+|api[_-]?key[=:\s]+)[^\s,;]+"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL)


def new_request_id() -> str:
    return uuid4().hex


def normalize_request_id(value: str | None) -> str:
    """Accept a bounded caller ID, otherwise generate one locally."""
    value = (value or "").strip()
    return value if _REQUEST_ID_RE.fullmatch(value) else new_request_id()


def new_workflow_correlation_id() -> UUID:
    return uuid4()


def claim_ref(token: str | None) -> str | None:
    """Return an observability-only, non-reversible claim reference."""
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def classify_error(error: BaseException | str) -> str:
    text = str(error).lower()
    if "redis" in text:
        return "redis_unavailable"
    if "database" in text or "postgres" in text or "asyncpg" in text:
        return "database_unavailable"
    if "timeout" in text:
        return "sandbox_timeout"
    if "cancel" in text:
        return "sandbox_cancelled"
    if "revok" in text:
        return "repository_revoked"
    if "artifact" in text and ("hash" in text or "integr" in text or "tamper" in text):
        return "artifact_integrity_failed"
    if "stale" in text and "base" in text:
        return "stale_base"
    if "claim" in text and "lost" in text:
        return "delivery_claim_lost"
    if "validation" in text:
        return "validation_failed"
    if "github" in text or "pull request" in text or "pull_request" in text:
        return "github_authorization_failed"
    return type(error).__name__ if isinstance(error, BaseException) else "workflow_error"


def safe_error(error: BaseException | str, limit: int = 256) -> str:
    """Bound and redact exception text before it reaches logs or metrics."""
    text = str(error).replace("\x00", " ")
    text = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    text = _SECRET_RE.sub(r"\1[REDACTED]", text)
    return text[:limit]


def bind_observability(**fields: Any) -> None:
    allowed = {key: value for key, value in fields.items() if value is not None}
    if "request_id" in allowed:
        _request_id.set(str(allowed["request_id"]))
    structlog.contextvars.bind_contextvars(**allowed)


def clear_observability() -> None:
    _request_id.set(None)
    structlog.contextvars.clear_contextvars()


def current_request_id() -> str | None:
    return _request_id.get()


def current_observability() -> dict[str, Any]:
    """Return a copy suitable for passing into an asynchronous worker task."""
    return dict(copy_context().items())


@contextmanager
def observability_scope(**fields: Any) -> Iterator[None]:
    bind_observability(**fields)
    try:
        yield
    finally:
        # Context values are request/task scoped; callers normally clear the
        # full context at the boundary.  This helper is for nested scopes.
        for key in fields:
            structlog.contextvars.unbind_contextvars(key)


class MetricsRegistry:
    """Thread-safe process-local counters/gauges with bounded names.

    Worker and API processes each maintain their own registry.  Durable
    workflow gauges are queried from PostgreSQL in the health endpoint.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: defaultdict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._durations: defaultdict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe_duration(self, name: str, duration_ms: float) -> None:
        with self._lock:
            values = self._durations[name]
            values[0] += duration_ms
            values[1] += 1

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            values: dict[str, float] = {**self._counters, **self._gauges}
            for name, (total, count) in self._durations.items():
                values[f"{name}_sum"] = total
                values[f"{name}_count"] = count
            return values


metrics = MetricsRegistry()


async def persist_metric(name: str, value: int = 1) -> None:
    """Best-effort cross-process telemetry; never workflow truth."""
    try:
        from app.db.redis import get_redis

        await get_redis().incrby(f"agentdock:telemetry:{name}", value)
        await get_redis().expire(f"agentdock:telemetry:{name}", 86_400)
    except Exception:
        return


async def remote_metrics() -> dict[str, float]:
    """Read best-effort worker telemetry without treating it as state."""
    values: dict[str, float] = {}
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        async for key in redis.scan_iter(match="agentdock:telemetry:*", count=100):
            name = str(key).removeprefix("agentdock:telemetry:")
            raw = await redis.get(key)
            if raw is not None:
                values[name] = float(raw)
    except Exception:
        return values
    return values


@contextmanager
def timed_metric(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics.observe_duration(name, (time.perf_counter() - started) * 1000)


def validate_observability_fields(fields: dict[str, Any]) -> None:
    """Guard the canonical vocabulary in tests and development builds."""
    unknown = set(fields) - CANONICAL_OBSERVABILITY_FIELDS
    if unknown:
        raise ValueError(f"unknown observability fields: {sorted(unknown)}")
