from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class GitHubNotConfiguredError(RuntimeError):
    pass


def require_github_configured() -> None:
    settings = get_settings()
    if not settings.github_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub integration is not configured",
        )


def create_app_jwt(*, now: datetime | None = None) -> str:
    """Create a short-lived GitHub App JWT (RS256). Never log the token."""
    require_github_configured()
    settings = get_settings()
    issued = now or datetime.now(UTC)
    # GitHub recommends iat slightly in the past to allow clock skew.
    payload = {
        "iat": int(issued.timestamp()) - 60,
        "exp": int((issued + timedelta(minutes=9)).timestamp()),
        "iss": settings.github_app_id,
    }
    try:
        pem = settings.github_private_key_pem()
    except (OSError, ValueError) as exc:
        logger.error("github.private_key_invalid")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub App private key is invalid",
        ) from exc
    try:
        return jwt.encode(payload, pem, algorithm="RS256")
    except Exception as exc:
        logger.error("github.jwt_encode_failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to create GitHub App credentials",
        ) from exc


def decode_app_jwt_unsafe_for_tests(token: str) -> dict[str, Any]:
    """Decode without verifying — tests only."""
    return jwt.decode(token, options={"verify_signature": False})
