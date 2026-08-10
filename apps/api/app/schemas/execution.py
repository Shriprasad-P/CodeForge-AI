from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateExecutionRequest(BaseModel):
    repository_connection_id: UUID
    command: list[str] = Field(min_length=1, max_length=32)
    working_directory: str | None = None
    agent_session_id: UUID | None = None

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value or not value[0].strip():
            raise ValueError("command must be a non-empty argv list")
        for arg in value:
            if not isinstance(arg, str) or not arg:
                raise ValueError("command args must be non-empty strings")
            if len(arg) > 256:
                raise ValueError("command arg too long")
            if "\x00" in arg:
                raise ValueError("command arg contains NUL")
        # Reject shell metacharacter wrappers — argv is executed directly, but still block obvious shells.
        blocked = {"/bin/sh", "/bin/bash", "sh", "bash", "zsh", "fish", "csh"}
        if value[0] in blocked or value[0].endswith("/sh") or value[0].endswith("/bash"):
            raise ValueError("shell interpreters are not allowed as the command executable")
        return value

    @field_validator("working_directory")
    @classmethod
    def validate_workdir(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("/") or normalized.startswith("~"):
            raise ValueError("working_directory must be relative to the workspace")
        parts = [p for p in normalized.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError("working_directory must not contain '..'")
        return "/".join(parts) or None


class ExecutionJobResponse(BaseModel):
    id: UUID
    repository_connection_id: UUID
    agent_session_id: UUID | None
    status: str
    command: list[str]
    working_directory: str | None
    exit_code: int | None
    error_type: str | None
    error_message: str | None
    output_truncated: bool
    cancel_requested: bool
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExecutionLogsResponse(BaseModel):
    id: UUID
    status: str
    stdout: str
    stderr: str
    output_truncated: bool
    exit_code: int | None
