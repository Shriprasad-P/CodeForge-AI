from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class GitHubAccount(Base, TimestampMixin):
    """GitHub user identity linked to an AgentDock user (password auth remains primary)."""

    __tablename__ = "github_accounts"
    __table_args__ = (UniqueConstraint("github_user_id", name="uq_github_accounts_github_user_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    github_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False, default="User")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user = relationship("User", back_populates="github_account")
    installations = relationship(
        "GitHubInstallation",
        back_populates="github_account",
        cascade="all, delete-orphan",
    )


class GitHubInstallation(Base, TimestampMixin):
    __tablename__ = "github_installations"
    __table_args__ = (
        UniqueConstraint("github_installation_id", name="uq_github_installations_github_installation_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    github_account_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("github_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    github_installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository_selection: Mapped[str] = mapped_column(String(32), nullable=False, default="selected")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    github_account = relationship("GitHubAccount", back_populates="installations")
    connections = relationship(
        "RepositoryConnection",
        back_populates="installation",
        cascade="all, delete-orphan",
    )


class RepositoryConnection(Base, TimestampMixin):
    __tablename__ = "repository_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "github_repository_id",
            name="uq_repository_connections_user_repo",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    installation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    github_repository_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(511), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False, default="main")
    private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    html_url: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    installation = relationship("GitHubInstallation", back_populates="connections")


class GitHubWebhookDelivery(Base, TimestampMixin):
    """Minimal idempotency store for GitHub webhook delivery IDs."""

    __tablename__ = "github_webhook_deliveries"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
