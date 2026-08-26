from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.observability import metrics as metric_registry, remote_metrics
from app.db.redis import check_redis, get_redis
from app.db.session import check_db, get_session_factory
from app.models.agent_run import AGENT_ACTIVE, AgentRun
from app.models.execution import ACTIVE_STATUSES, ExecutionJob
from app.models.outbox import OutboxEvent
from sqlalchemy import func, select, text

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = Field(default_factory=lambda: settings.app_name)
    version: str = "0.1.0"
    timestamp: str


class ReadyCheck(BaseModel):
    postgres: bool
    redis: bool
    migrations: bool
    worker: bool


class ReadyResponse(BaseModel):
    status: str
    checks: ReadyCheck
    capabilities: dict[str, bool]
    timestamp: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: process is up. Does not check dependencies."""
    return HealthResponse(timestamp=_now())


@router.get("/api/ready", response_model=ReadyResponse)
async def ready() -> JSONResponse:
    """Readiness for normal work; optional providers are capabilities."""
    postgres_ok = await check_db()
    redis_ok = await check_redis()
    migrations_ok = await _check_migrations() if postgres_ok else False
    worker_ok = await _check_worker() if redis_ok else False
    checks = ReadyCheck(
        postgres=postgres_ok,
        redis=redis_ok,
        migrations=migrations_ok,
        worker=worker_ok,
    )
    ok = checks.postgres and checks.redis and checks.migrations and checks.worker
    body = ReadyResponse(
        status="ready" if ok else "not_ready",
        checks=checks,
        capabilities={"github": settings.github_configured, "agent": settings.agent_configured},
        timestamp=_now(),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body.model_dump(),
    )


async def _check_migrations() -> bool:
    """Check the revision table without running migrations in a request."""
    try:
        factory = get_session_factory()
        async with factory() as db:
            revision = await db.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        return str(revision or "") == settings.expected_alembic_revision
    except Exception:
        return False


async def _check_worker() -> bool:
    if not settings.worker_readiness_required:
        return True
    try:
        redis = get_redis()
        now = datetime.now(UTC)
        async for key in redis.scan_iter(match="agentdock:worker:heartbeat:*", count=100):
            raw = await redis.get(key)
            if not raw:
                continue
            payload = raw if isinstance(raw, dict) else json.loads(raw)
            timestamp = datetime.fromisoformat(str(payload["last_heartbeat"]))
            if (now - timestamp).total_seconds() <= settings.worker_heartbeat_ttl_seconds:
                return True
    except Exception:
        return False
    return False


async def _durable_gauges() -> dict[str, float]:
    """Read durable queue/workflow gauges from PostgreSQL, never Redis."""
    values: dict[str, float] = {}
    try:
        factory = get_session_factory()
        async with factory() as db:
            values["agentdock_outbox_pending"] = float(
                await db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "pending")) or 0
            )
            values["agentdock_agent_runs_active"] = float(
                await db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.status.in_(tuple(AGENT_ACTIVE)))) or 0
            )
            values["agentdock_execution_jobs_active"] = float(
                await db.scalar(select(func.count()).select_from(ExecutionJob).where(ExecutionJob.status.in_(tuple(ACTIVE_STATUSES)))) or 0
            )
            oldest = await db.scalar(
                select(func.min(OutboxEvent.created_at)).where(OutboxEvent.status == "pending")
            )
            values["agentdock_outbox_oldest_pending_age_seconds"] = max(
                0.0, (datetime.now(UTC) - oldest).total_seconds()
            ) if oldest else 0.0
    except Exception:
        values.update(
            {
                "agentdock_outbox_pending": 0.0,
                "agentdock_agent_runs_active": 0.0,
                "agentdock_execution_jobs_active": 0.0,
                "agentdock_outbox_oldest_pending_age_seconds": 0.0,
            }
        )
    return values


@router.get("/api/metrics")
async def metrics() -> Response:
    """Prometheus-compatible metrics (telemetry; PostgreSQL is authoritative)."""
    from app.services.agent_events import metrics_snapshot

    pg_ok = 1 if await check_db() else 0
    redis_ok = 1 if await check_redis() else 0
    rt = metrics_snapshot()
    lines = [
        "# HELP agentdock_up API process up",
        "# TYPE agentdock_up gauge",
        "agentdock_up 1",
        "# HELP agentdock_postgres_up Postgres connectivity",
        "# TYPE agentdock_postgres_up gauge",
        f"agentdock_postgres_up {pg_ok}",
        "# HELP agentdock_redis_up Redis connectivity",
        "# TYPE agentdock_redis_up gauge",
        f"agentdock_redis_up {redis_ok}",
        "# HELP websocket_active_connections Live agent-run WebSocket clients",
        "# TYPE websocket_active_connections gauge",
        f"websocket_active_connections {rt['websocket_active_connections']}",
        "# HELP agent_events_published_total Agent realtime events published",
        "# TYPE agent_events_published_total counter",
        f"agent_events_published_total {rt['agent_events_published_total']}",
        "# HELP agent_event_publish_failures_total Failed agent event publishes",
        "# TYPE agent_event_publish_failures_total counter",
        f"agent_event_publish_failures_total {rt['agent_event_publish_failures_total']}",
        "# HELP websocket_disconnects_total WebSocket disconnects",
        "# TYPE websocket_disconnects_total counter",
        f"websocket_disconnects_total {rt['websocket_disconnects_total']}",
        "",
    ]
    legacy_metric_names = {
        "websocket_active_connections",
        "agent_events_published_total",
        "agent_event_publish_failures_total",
        "websocket_disconnects_total",
    }
    for name, value in {**metric_registry.snapshot(), **await remote_metrics(), **await _durable_gauges()}.items():
        if name in legacy_metric_names:
            continue
        metric_type = "counter" if name.endswith("_total") else "gauge"
        lines.extend([f"# TYPE {name} {metric_type}", f"{name} {value}"])
    return PlainTextResponse("\n".join(lines), media_type="text/plain; version=0.0.4")


# Root alias for container probes that expect /health
@router.get("/health")
async def health_alias() -> dict[str, Any]:
    return (await health()).model_dump()
