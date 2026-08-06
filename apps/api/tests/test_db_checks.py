from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import redis as redis_mod
from app.db import session as session_mod


@pytest.mark.asyncio
async def test_check_db_true_on_success() -> None:
    session = AsyncMock()
    session.execute = AsyncMock(return_value=None)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(session_mod, "session_scope", return_value=cm):
        assert await session_mod.check_db() is True


@pytest.mark.asyncio
async def test_check_db_false_on_error() -> None:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=RuntimeError("down"))
    cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(session_mod, "session_scope", return_value=cm):
        assert await session_mod.check_db() is False


@pytest.mark.asyncio
async def test_check_redis_true_on_ping() -> None:
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    with patch.object(redis_mod, "get_redis", return_value=client):
        assert await redis_mod.check_redis() is True


@pytest.mark.asyncio
async def test_check_redis_false_on_error() -> None:
    client = AsyncMock()
    client.ping = AsyncMock(side_effect=ConnectionError("refused"))
    with patch.object(redis_mod, "get_redis", return_value=client):
        assert await redis_mod.check_redis() is False
