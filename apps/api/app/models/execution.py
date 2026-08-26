from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ExecutionJobStatus(str, enum.Enum):
    queued = "queued"
    starting = "starting"
    cloning = "cloning"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"


class ExecutionErrorType(str, enum.Enum):
    repository_clone_failed = "repository_clone_failed"
    sandbox_start_failed = "sandbox_start_failed"
    command_failed = "command_failed"
    execution_timeout = "execution_timeout"
    cancelled = "cancelled"
    invalid_request = "invalid_request"
    internal_error = "internal_error"


TERMINAL_STATUSES = frozenset(
    {
        ExecutionJobStatus.succeeded,
        ExecutionJobStatus.failed,
        ExecutionJobStatus.cancelled,
        ExecutionJobStatus.timed_out,
    }
)

ACTIVE_STATUSES = frozenset(
    {
        ExecutionJobStatus.queued,
        ExecutionJobStatus.starting,
        ExecutionJobStatus.cloning,
        ExecutionJobStatus.running,
    }
)


class ExecutionJob(Base, TimestampMixin):
    __tablename__ = "execution_jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_session_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    repository_connection_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repository_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ExecutionJobStatus] = mapped_column(
        Enum(ExecutionJobStatus, name="execution_job_status", native_enum=False, length=32),
        default=ExecutionJobStatus.queued,
        nullable=False,
        index=True,
    )
    command: Mapped[list] = mapped_column(JSONB, nullable=False)
    working_directory: Mapped[str | None] = mapped_column(String(512), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[ExecutionErrorType | None] = mapped_column(
        Enum(ExecutionErrorType, name="execution_error_type", native_enum=False, length=64),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
