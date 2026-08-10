from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app import create_app
from app.core.config import get_settings, settings
from app.db.redis import close_redis, get_redis, init_redis
from app.db.session import close_db, get_session_factory, init_db


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def app_client() -> AsyncIterator[AsyncClient]:
    """Full app with real Postgres + Redis (Compose infra)."""
    import os

    os.environ["AUTH_RATE_LIMIT_ATTEMPTS"] = "1000"
    os.environ["EXECUTION_RATE_LIMIT_ATTEMPTS"] = "1000"
    os.environ["EXECUTION_QUEUE_KEY"] = "agentdock:executions:test"
    os.environ["AGENT_QUEUE_KEY"] = "agentdock:agent_runs:test"
    os.environ["LLM_PROVIDER"] = "fake"
    get_settings.cache_clear()
    # Re-bind module-level settings after cache clear for tests that import settings.
    import app.core.config as config_mod

    config_mod.settings = get_settings()

    application = create_app()
    await init_db()
    await init_redis()

    # Clean tables between tests for isolation.
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE agent_steps, agent_runs, execution_jobs, github_webhook_deliveries, repository_connections, "
                "github_installations, github_accounts, auth_sessions, agent_sessions, users "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()

    # Clear rate-limit counters so suite order does not flake on shared Redis.
    try:
        redis = get_redis()
        await redis.flushdb()
    except Exception:
        pass

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await close_redis()
    await close_db()
    get_settings.cache_clear()
