from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.redis import check_redis
from app.db.session import check_db

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = Field(default_factory=lambda: settings.app_name)
    version: str = "0.1.0"
    timestamp: str


class ReadyCheck(BaseModel):
    postgres: bool
    redis: bool


class ReadyResponse(BaseModel):
    status: str
    checks: ReadyCheck
    timestamp: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness: process is up. Does not check dependencies."""
    return HealthResponse(timestamp=_now())


@router.get("/api/ready", response_model=ReadyResponse)
async def ready() -> JSONResponse:
    """Readiness: Postgres and Redis must respond."""
    checks = ReadyCheck(postgres=await check_db(), redis=await check_redis())
    ok = checks.postgres and checks.redis
    body = ReadyResponse(
        status="ready" if ok else "not_ready",
        checks=checks,
        timestamp=_now(),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body.model_dump(),
    )


@router.get("/api/metrics")
async def metrics() -> Response:
    """Prometheus-compatible metrics (Phase 1: process + dependency gauges)."""
    pg_ok = 1 if await check_db() else 0
    redis_ok = 1 if await check_redis() else 0
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
        "",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain; version=0.0.4")


# Root alias for container probes that expect /health
@router.get("/health")
async def health_alias() -> dict[str, Any]:
    return (await health()).model_dump()
