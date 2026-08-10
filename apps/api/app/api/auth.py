from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_db, require_current_user
from app.auth.rate_limit import enforce_auth_rate_limit
from app.models.user import User
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    await enforce_auth_rate_limit(request, "register")
    user = await auth_service.register_user(
        db,
        email=str(payload.email),
        password=payload.password,
        display_name=payload.display_name,
    )
    token = await auth_service.create_auth_session(db, user)
    auth_service.set_session_cookie(response, token)
    return AuthResponse(user=auth_service.user_to_response(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    await enforce_auth_rate_limit(request, "login")
    user = await auth_service.authenticate_user(
        db,
        email=str(payload.email),
        password=payload.password,
    )
    token = await auth_service.create_auth_session(db, user)
    auth_service.set_session_cookie(response, token)
    return AuthResponse(user=auth_service.user_to_response(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Response:
    from app.core.config import settings

    token = request.cookies.get(settings.session_cookie_name)
    await auth_service.revoke_session(db, token)
    auth_service.clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(require_current_user)) -> UserResponse:
    return auth_service.user_to_response(user)
