from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_db, require_current_user
from app.core.config import get_settings
from app.models.agent_run import AgentRun
from app.models.user import User
from app.schemas.agent_run import (
    AgentDiffResponse,
    AgentRunResponse,
    AgentStatusResponse,
    AgentStepResponse,
    ApproveAgentRunRequest,
    CreateAgentRunRequest,
)
from app.services import agent_run as agent_run_service

router = APIRouter(prefix="/api/agent-runs", tags=["agent-runs"])


def _run_response(run: AgentRun) -> AgentRunResponse:
    return AgentRunResponse(
        id=run.id,
        repository_connection_id=run.repository_connection_id,
        agent_session_id=run.agent_session_id,
        status=run.status.value,
        task=run.task,
        model_provider=run.model_provider,
        model_name=run.model_name,
        max_steps=run.max_steps,
        steps_used=run.steps_used,
        tool_calls_used=run.tool_calls_used,
        cancel_requested=run.cancel_requested,
        summary=run.summary,
        result_status=run.result_status,
        changed_files=run.changed_files,
        validation=run.validation,
        diff_truncated=run.diff_truncated,
        error_type=run.error_type.value if run.error_type else None,
        error_message=run.error_message,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        approval_status=run.approval_status,
        approved_at=run.approved_at,
        rejected_at=run.rejected_at,
        rejection_reason=run.rejection_reason,
        base_commit_sha=run.base_commit_sha,
        diff_hash=run.diff_hash,
        artifact_hash=run.publication_artifact_hash,
        artifact_size=run.publication_artifact_size,
        artifact_version=run.publication_artifact_version,
        artifact_status=run.publication_artifact_status,
        preview_truncated=run.diff_truncated,
        publication_status=run.publication_status,
        branch_name=run.branch_name,
        commit_sha=run.commit_sha,
        github_pr_number=run.github_pr_number,
        github_pr_id=run.github_pr_id,
        github_pr_url=run.github_pr_url,
    )


@router.get("/status", response_model=AgentStatusResponse)
async def agent_status(_: User = Depends(require_current_user)) -> AgentStatusResponse:
    settings = get_settings()
    return AgentStatusResponse(
        configured=settings.agent_configured,
        provider=settings.llm_provider,
        model=settings.llm_model,
    )


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_run(
    payload: CreateAgentRunRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run = await agent_run_service.create_agent_run(
        db,
        user=user,
        repository_connection_id=payload.repository_connection_id,
        task=payload.task,
        agent_session_id=payload.agent_session_id,
    )
    return _run_response(run)


@router.get("", response_model=list[AgentRunResponse])
async def list_agent_runs(
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
) -> list[AgentRunResponse]:
    rows = await agent_run_service.list_user_runs(db, user_id=user.id, limit=limit)
    return [_run_response(row) for row in rows]


@router.get("/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run = await agent_run_service.get_user_run(db, user_id=user.id, run_id=run_id)
    return _run_response(run)


@router.get("/{run_id}/steps", response_model=list[AgentStepResponse])
async def get_agent_steps(
    run_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentStepResponse]:
    steps = await agent_run_service.list_run_steps(db, user_id=user.id, run_id=run_id)
    return [
        AgentStepResponse(
            id=step.id,
            step_number=step.step_number,
            kind=step.kind,
            tool_name=step.tool_name,
            tool_input=step.tool_input,
            tool_result_summary=step.tool_result_summary,
            duration_ms=step.duration_ms,
            created_at=step.created_at,
        )
        for step in steps
    ]


@router.get("/{run_id}/diff", response_model=AgentDiffResponse)
async def get_agent_diff(
    run_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentDiffResponse:
    run = await agent_run_service.get_user_run(db, user_id=user.id, run_id=run_id)
    return AgentDiffResponse(
        id=run.id,
        status=run.status.value,
        diff_stat=run.diff_stat or "",
        diff_text=run.diff_text or "",
        diff_truncated=run.diff_truncated,
        changed_files=run.changed_files or [],
        diff_hash=run.diff_hash,
        base_commit_sha=run.base_commit_sha,
        artifact_hash=run.publication_artifact_hash,
        artifact_size=run.publication_artifact_size,
        artifact_version=run.publication_artifact_version,
        artifact_status=run.publication_artifact_status,
        preview_truncated=run.diff_truncated,
    )


@router.post("/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_agent_run(
    run_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run = await agent_run_service.request_cancel(db, user_id=user.id, run_id=run_id)
    return _run_response(run)


@router.post("/{run_id}/approve", response_model=AgentRunResponse)
async def approve_agent_run(
    run_id: UUID,
    payload: ApproveAgentRunRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run = await agent_run_service.approve_agent_run(
        db,
        user_id=user.id,
        run_id=run_id,
        artifact_hash=payload.artifact_hash,
        artifact_version=payload.artifact_version,
        base_commit_sha=payload.base_commit_sha,
    )
    return _run_response(run)


@router.post("/{run_id}/reject", response_model=AgentRunResponse)
async def reject_agent_run(
    run_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> AgentRunResponse:
    run = await agent_run_service.reject_agent_run(db, user_id=user.id, run_id=run_id)
    return _run_response(run)
