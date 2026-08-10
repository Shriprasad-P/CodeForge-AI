from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateAgentRunRequest(BaseModel):
    repository_connection_id: UUID
    task: str = Field(min_length=1, max_length=4000)
    agent_session_id: UUID | None = None

    @field_validator("task")
    @classmethod
    def strip_task(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("task must not be empty")
        return text


class AgentRunResponse(BaseModel):
    id: UUID
    repository_connection_id: UUID
    agent_session_id: UUID | None
    status: str
    task: str
    model_provider: str
    model_name: str
    max_steps: int
    steps_used: int
    tool_calls_used: int
    cancel_requested: bool
    summary: str | None
    result_status: str | None
    changed_files: list | None
    validation: dict | None
    diff_truncated: bool
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    approval_status: str
    approved_at: datetime | None
    rejected_at: datetime | None
    rejection_reason: str | None
    base_commit_sha: str | None
    diff_hash: str | None
    publication_status: str
    branch_name: str | None
    commit_sha: str | None
    github_pr_number: int | None
    github_pr_id: int | None
    github_pr_url: str | None


class AgentStepResponse(BaseModel):
    id: UUID
    step_number: int
    kind: str
    tool_name: str | None
    tool_input: dict | None
    tool_result_summary: str | None
    duration_ms: int | None
    created_at: datetime


class AgentDiffResponse(BaseModel):
    id: UUID
    status: str
    diff_stat: str
    diff_text: str
    diff_truncated: bool
    changed_files: list
    diff_hash: str | None
    base_commit_sha: str | None


class AgentStatusResponse(BaseModel):
    configured: bool
    provider: str
    model: str
