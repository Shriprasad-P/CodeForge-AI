from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_db, require_current_user
from app.models.user import User
from app.schemas.auth import AgentSessionCreateRequest, AgentSessionResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/api/agent-sessions", tags=["agent-sessions"])


@router.post("", response_model=AgentSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_session(
    payload: AgentSessionCreateRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentSessionResponse:
    session = await auth_service.create_agent_session(
        db,
        user_id=user.id,
        title=payload.title,
    )
    return AgentSessionResponse(
        id=session.id,
        title=session.title,
        status=session.status.value,
        user_id=session.user_id,
    )


@router.get("/{session_id}", response_model=AgentSessionResponse)
async def get_agent_session(
    session_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentSessionResponse:
    session = await auth_service.get_owned_agent_session(
        db,
        session_id=session_id,
        user_id=user.id,
    )
    return AgentSessionResponse(
        id=session.id,
        title=session.title,
        status=session.status.value,
        user_id=session.user_id,
    )
