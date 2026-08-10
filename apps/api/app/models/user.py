from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Reserved for Phase 3 GitHub identity linking without rewriting users.
    auth_provider: Mapped[str] = mapped_column(String(32), default="password", nullable=False)
    provider_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    auth_sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")
    agent_sessions = relationship(
        "AgentSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    github_account = relationship(
        "GitHubAccount",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
