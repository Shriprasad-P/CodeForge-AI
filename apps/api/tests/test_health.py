from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import create_app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    application = create_app()

    async def _noop() -> None:
        return None

    with (
        patch("app.init_db", new=AsyncMock(side_effect=_noop)),
        patch("app.close_db", new=AsyncMock(side_effect=_noop)),
        patch("app.init_redis", new=AsyncMock(side_effect=_noop)),
        patch("app.close_redis", new=AsyncMock(side_effect=_noop)),
    ):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "AgentDock"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_ready_when_deps_up(client: AsyncClient) -> None:
    with (
        patch("app.api.health.check_db", new=AsyncMock(return_value=True)),
        patch("app.api.health.check_redis", new=AsyncMock(return_value=True)),
    ):
        response = await client.get("/api/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"postgres": True, "redis": True}


@pytest.mark.asyncio
async def test_ready_503_when_postgres_down(client: AsyncClient) -> None:
    with (
        patch("app.api.health.check_db", new=AsyncMock(return_value=False)),
        patch("app.api.health.check_redis", new=AsyncMock(return_value=True)),
    ):
        response = await client.get("/api/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"] is False


@pytest.mark.asyncio
async def test_metrics_prometheus_format(client: AsyncClient) -> None:
    with (
        patch("app.api.health.check_db", new=AsyncMock(return_value=True)),
        patch("app.api.health.check_redis", new=AsyncMock(return_value=False)),
    ):
        response = await client.get("/api/metrics")
    assert response.status_code == 200
    text = response.text
    assert "agentdock_up 1" in text
    assert "agentdock_postgres_up 1" in text
    assert "agentdock_redis_up 0" in text
    assert "text/plain" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_health_alias(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
