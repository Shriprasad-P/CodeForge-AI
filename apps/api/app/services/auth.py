from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    normalize_email,
    verify_password,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.models.agent_session import AgentSession, AgentSessionStatus
from app.models.auth_session import AuthSession
from app.models.user import User
from app.schemas.auth import UserResponse

logger = get_logger(__name__)

COOKIE_PATH = "/"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure_flag,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        max_age=settings.session_ttl_seconds,
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        path=COOKIE_PATH,
        domain=settings.cookie_domain,
    )


def user_to_response(user: User) -> UserResponse:
    return UserResponse(id=user.id, email=user.email, display_name=user.display_name)


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
) -> User:
    normalized = normalize_email(email)
    existing = await db.scalar(select(User).where(User.email == normalized))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )
    user = User(
        email=normalized,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        auth_provider="password",
    )
    db.add(user)
    await db.flush()
    logger.info("user.registered", user_id=str(user.id))
    return user


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    normalized = normalize_email(email)
    user = await db.scalar(select(User).where(User.email == normalized))
    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        logger.info("user.login_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    logger.info("user.login_succeeded", user_id=str(user.id))
    return user


async def create_auth_session(db: AsyncSession, user: User) -> str:
    token = generate_session_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds),
    )
    db.add(session)
    await db.flush()
    return token


async def get_user_for_token(db: AsyncSession, token: str | None) -> User | None:
    if not token:
        return None
    token_hash = hash_session_token(token)
    result = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == token_hash)
    )
    row = result.first()
    if row is None:
        return None
    auth_session, user = row
    now = datetime.now(UTC)
    if auth_session.revoked_at is not None or auth_session.expires_at <= now or not user.is_active:
        return None
    return user


async def revoke_session(db: AsyncSession, token: str | None) -> None:
    if not token:
        return
    token_hash = hash_session_token(token)
    auth_session = await db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))
    if auth_session is None or auth_session.revoked_at is not None:
        return
    auth_session.revoked_at = datetime.now(UTC)
    logger.info("user.logged_out", user_id=str(auth_session.user_id))


async def create_agent_session(db: AsyncSession, *, user_id: UUID, title: str) -> AgentSession:
    session = AgentSession(
        user_id=user_id,
        title=title,
        status=AgentSessionStatus.created,
    )
    db.add(session)
    await db.flush()
    return session


async def get_owned_agent_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    user_id: UUID,
) -> AgentSession:
    session = await db.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.user_id == user_id,
        )
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session
