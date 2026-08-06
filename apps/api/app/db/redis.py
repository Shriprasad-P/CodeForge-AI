from __future__ import annotations

import redis.asyncio as redis

from app.core.config import settings

_client: redis.Redis | None = None


async def init_redis() -> None:
    global _client
    _client = redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=settings.ready_timeout_seconds,
    )


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def get_redis() -> redis.Redis:
    if _client is None:
        raise RuntimeError("Redis client is not initialized")
    return _client


async def check_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False
