from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_db, require_current_user
from app.models.execution import ExecutionJob
from app.models.user import User
from app.schemas.execution import CreateExecutionRequest, ExecutionJobResponse, ExecutionLogsResponse
from app.services import execution as execution_service

router = APIRouter(prefix="/api/executions", tags=["executions"])


def _job_response(job: ExecutionJob) -> ExecutionJobResponse:
    return ExecutionJobResponse(
        id=job.id,
        repository_connection_id=job.repository_connection_id,
        agent_session_id=job.agent_session_id,
        status=job.status.value,
        command=list(job.command),
        working_directory=job.working_directory,
        exit_code=job.exit_code,
        error_type=job.error_type.value if job.error_type else None,
        error_message=job.error_message,
        output_truncated=job.output_truncated,
        cancel_requested=job.cancel_requested,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("", response_model=ExecutionJobResponse, status_code=status.HTTP_201_CREATED)
async def create_execution(
    payload: CreateExecutionRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExecutionJobResponse:
    job = await execution_service.create_execution_job(
        db,
        user=user,
        repository_connection_id=payload.repository_connection_id,
        command=payload.command,
        working_directory=payload.working_directory,
        agent_session_id=payload.agent_session_id,
    )
    return _job_response(job)


@router.get("", response_model=list[ExecutionJobResponse])
async def list_executions(
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=100),
) -> list[ExecutionJobResponse]:
    rows = await execution_service.list_user_jobs(db, user_id=user.id, limit=limit)
    return [_job_response(row) for row in rows]


@router.get("/{execution_id}", response_model=ExecutionJobResponse)
async def get_execution(
    execution_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExecutionJobResponse:
    job = await execution_service.get_user_job(db, user_id=user.id, job_id=execution_id)
    return _job_response(job)


@router.get("/{execution_id}/logs", response_model=ExecutionLogsResponse)
async def get_execution_logs(
    execution_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExecutionLogsResponse:
    job = await execution_service.get_user_job(db, user_id=user.id, job_id=execution_id)
    return ExecutionLogsResponse(
        id=job.id,
        status=job.status.value,
        stdout=job.stdout or "",
        stderr=job.stderr or "",
        output_truncated=job.output_truncated,
        exit_code=job.exit_code,
    )


@router.post("/{execution_id}/cancel", response_model=ExecutionJobResponse)
async def cancel_execution(
    execution_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExecutionJobResponse:
    job = await execution_service.request_cancel(db, user_id=user.id, job_id=execution_id)
    return _job_response(job)
