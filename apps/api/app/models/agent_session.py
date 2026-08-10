from __future__ import annotations

import enum
from uuid import UUID, uuid4

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class AgentSessionStatus(str, enum.Enum):
    created = "created"


class AgentSession(Base, TimestampMixin):
    """Minimal coding-agent session foundation (execution lands in later phases)."""

    __tablename__ = "agent_sessions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[AgentSessionStatus] = mapped_column(
        Enum(AgentSessionStatus, name="agent_session_status", native_enum=False, length=32),
        default=AgentSessionStatus.created,
        nullable=False,
    )

    user = relationship("User", back_populates="agent_sessions")
