from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AgentRunStatus(str, enum.Enum):
    queued = "queued"
    planning = "planning"
    running = "running"
    validating = "validating"
    awaiting_approval = "awaiting_approval"
    publishing = "publishing"
    rejected = "rejected"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"
    step_limit_reached = "step_limit_reached"
    repository_revoked = "repository_revoked"


class AgentRunErrorType(str, enum.Enum):
    model_error = "model_error"
    tool_validation_error = "tool_validation_error"
    sandbox_error = "sandbox_error"
    command_failed = "command_failed"
    step_limit_reached = "step_limit_reached"
    runtime_limit_reached = "runtime_limit_reached"
    repository_error = "repository_error"
    internal_error = "internal_error"
    cancelled = "cancelled"
    failed_validation = "failed_validation"
    not_configured = "not_configured"
    publication_failed = "publication_failed"
    repository_changed = "repository_changed"
    approval_invalidated = "approval_invalidated"
    artifact_too_large = "artifact_too_large"
    unsupported_artifact = "unsupported_artifact"
    repository_revoked = "repository_revoked"


AGENT_TERMINAL = frozenset(
    {
        AgentRunStatus.succeeded,
        AgentRunStatus.failed,
        AgentRunStatus.cancelled,
        AgentRunStatus.timed_out,
        AgentRunStatus.step_limit_reached,
        AgentRunStatus.rejected,
        AgentRunStatus.repository_revoked,
    }
)

AGENT_ACTIVE = frozenset(
    {
        AgentRunStatus.queued,
        AgentRunStatus.planning,
        AgentRunStatus.running,
        AgentRunStatus.validating,
    }
)


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_session_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    repository_connection_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("repository_connections.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    workflow_correlation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), default=uuid4, nullable=False, index=True
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status", native_enum=False, length=32),
        default=AgentRunStatus.queued,
        nullable=False,
        index=True,
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sandbox_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_files: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    validation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    diff_stat: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_type: Mapped[AgentRunErrorType | None] = mapped_column(
        Enum(AgentRunErrorType, name="agent_run_error_type", native_enum=False, length=64),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diff_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publication_artifact: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    publication_artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publication_artifact_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_artifact_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_change_manifest: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    publication_artifact_status: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    publication_artifact_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    validation_artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_artifact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_artifact_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approval_base_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    delivery_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    publication_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    github_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_pr_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_pr_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class AgentStep(Base, TimestampMixin):
    __tablename__ = "agent_steps"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # tool | finish | error
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
