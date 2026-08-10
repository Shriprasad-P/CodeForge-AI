from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)


def _client_key(request: Request, action: str) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    ip = (forwarded.split(",")[0].strip() if forwarded else None) or (
        request.client.host if request.client else "unknown"
    )
    return f"auth:rl:{action}:{ip}"


async def enforce_auth_rate_limit(request: Request, action: str) -> None:
    """Simple Redis fixed-window limiter for login/register."""
    settings = get_settings()
    try:
        redis = get_redis()
        key = _client_key(request, action)
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.auth_rate_limit_window_seconds)
        if count > settings.auth_rate_limit_attempts:
            logger.info("auth.rate_limited", action=action)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Try again shortly.",
            )
    except HTTPException:
        raise
    except Exception:
        # Fail open if Redis blips — readiness already surfaces Redis health.
        logger.warning("auth.rate_limit_unavailable", action=action)
