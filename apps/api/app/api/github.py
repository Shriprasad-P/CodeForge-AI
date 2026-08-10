from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_db, require_current_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.github import GitHubAccount, GitHubInstallation, RepositoryConnection
from app.models.user import User
from app.schemas.github import (
    ConnectRepositoryRequest,
    GitHubAccountResponse,
    GitHubConnectResponse,
    GitHubInstallationResponse,
    GitHubRepositoryListResponse,
    GitHubRepositoryResponse,
    GitHubStatusResponse,
    RepositoryConnectionResponse,
)
from app.services import github as github_service
from app.services.github_app import require_github_configured
from app.services.github_client import GitHubAPIError, GitHubClient

router = APIRouter(prefix="/api/github", tags=["github"])
logger = get_logger(__name__)


def _installation_response(row: GitHubInstallation) -> GitHubInstallationResponse:
    return GitHubInstallationResponse(
        id=row.id,
        github_installation_id=row.github_installation_id,
        account_login=row.account_login,
        account_type=row.account_type,
        repository_selection=row.repository_selection,
        suspended=row.suspended_at is not None,
        created_at=row.created_at,
    )


def _connection_response(row: RepositoryConnection) -> RepositoryConnectionResponse:
    return RepositoryConnectionResponse(
        id=row.id,
        github_repository_id=row.github_repository_id,
        installation_id=row.installation_id,
        owner=row.owner,
        name=row.name,
        full_name=row.full_name,
        default_branch=row.default_branch,
        private=row.private,
        html_url=row.html_url,
        is_active=row.is_active,
        created_at=row.created_at,
    )


@router.get("/status", response_model=GitHubStatusResponse)
async def github_status(
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> GitHubStatusResponse:
    account = await db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    installation_count = await db.scalar(
        select(func.count()).select_from(GitHubInstallation).where(GitHubInstallation.user_id == user.id)
    )
    connection_count = await db.scalar(
        select(func.count())
        .select_from(RepositoryConnection)
        .where(RepositoryConnection.user_id == user.id, RepositoryConnection.is_active.is_(True))
    )
    return GitHubStatusResponse(
        configured=get_settings().github_configured,
        linked=account is not None,
        github_login=account.github_login if account else None,
        installation_count=int(installation_count or 0),
        connection_count=int(connection_count or 0),
    )


@router.get("/connect", response_model=GitHubConnectResponse)
async def github_connect(user: User = Depends(require_current_user)) -> GitHubConnectResponse:
    require_github_configured()
    state = await github_service.create_oauth_state(user.id, purpose="link")
    return GitHubConnectResponse(authorize_url=github_service.build_oauth_authorize_url(state))


@router.get("/callback")
async def github_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    require_github_configured()
    cfg = get_settings()
    frontend = cfg.github_frontend_success_url
    if error:
        return RedirectResponse(url=f"{frontend}?error=github_denied", status_code=status.HTTP_302_FOUND)
    if not code or not state:
        return RedirectResponse(url=f"{frontend}?error=missing_params", status_code=status.HTTP_302_FOUND)

    try:
        state_data = await github_service.consume_oauth_state(state)
    except HTTPException:
        return RedirectResponse(url=f"{frontend}?error=invalid_state", status_code=status.HTTP_302_FOUND)

    user = await db.get(User, UUID(state_data["user_id"]))
    if user is None or not user.is_active:
        return RedirectResponse(url=f"{frontend}?error=user_not_found", status_code=status.HTTP_302_FOUND)

    client = GitHubClient()
    try:
        token = await client.exchange_oauth_code(code)
        github_user = await client.get_authenticated_user(token)
        account = await github_service.link_github_account(db, user=user, github_user=github_user)
    except (GitHubAPIError, HTTPException):
        return RedirectResponse(url=f"{frontend}?error=oauth_failed", status_code=status.HTTP_302_FOUND)

    # After linking, send user to install the app (state binds install claim).
    install_state = await github_service.create_oauth_state(user.id, purpose="install")
    install_url = github_service.build_install_url(install_state)
    logger.info("github.oauth_linked", user_id=str(user.id), github_login=account.github_login)
    return RedirectResponse(url=install_url, status_code=status.HTTP_302_FOUND)


@router.get("/setup")
async def github_setup(
    installation_id: int | None = None,
    setup_action: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """GitHub App setup URL target after installation."""
    require_github_configured()
    frontend = get_settings().github_frontend_success_url
    if not installation_id:
        return RedirectResponse(url=f"{frontend}?error=missing_installation", status_code=status.HTTP_302_FOUND)

    user_id: UUID | None = None
    if state:
        try:
            state_data = await github_service.consume_oauth_state(state)
            user_id = UUID(state_data["user_id"])
        except HTTPException:
            return RedirectResponse(url=f"{frontend}?error=invalid_state", status_code=status.HTTP_302_FOUND)

    if user_id is None:
        return RedirectResponse(url=f"{frontend}?error=unauthenticated_setup", status_code=status.HTTP_302_FOUND)

    user = await db.get(User, user_id)
    if user is None:
        return RedirectResponse(url=f"{frontend}?error=user_not_found", status_code=status.HTTP_302_FOUND)

    account = await db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    client = GitHubClient()
    try:
        payload = await client.get_installation(installation_id)
        await github_service.upsert_installation(
            db,
            user_id=user.id,
            installation_payload=payload,
            github_account_id=account.id if account else None,
        )
    except (GitHubAPIError, HTTPException):
        return RedirectResponse(url=f"{frontend}?error=setup_failed", status_code=status.HTTP_302_FOUND)

    action = setup_action or "install"
    return RedirectResponse(url=f"{frontend}?installed=1&setup_action={action}", status_code=status.HTTP_302_FOUND)


@router.get("/account", response_model=GitHubAccountResponse | None)
async def get_account(
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> GitHubAccountResponse | None:
    account = await db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    if account is None:
        return None
    return GitHubAccountResponse(
        id=account.id,
        github_user_id=account.github_user_id,
        github_login=account.github_login,
        account_type=account.account_type,
        avatar_url=account.avatar_url,
    )


@router.get("/installations", response_model=list[GitHubInstallationResponse])
async def list_installations(
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[GitHubInstallationResponse]:
    rows = await github_service.list_user_installations(db, user.id)
    return [_installation_response(row) for row in rows]


@router.get("/repositories", response_model=GitHubRepositoryListResponse)
async def list_repositories(
    installation_id: UUID = Query(...),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> GitHubRepositoryListResponse:
    require_github_configured()
    data = await github_service.list_accessible_repositories(
        db,
        user_id=user.id,
        installation_id=installation_id,
        page=page,
        per_page=per_page,
    )
    return GitHubRepositoryListResponse(
        total_count=data["total_count"],
        page=data["page"],
        per_page=data["per_page"],
        repositories=[GitHubRepositoryResponse(**repo) for repo in data["repositories"]],
    )


@router.post(
    "/repositories/{repository_id}/connect",
    response_model=RepositoryConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def connect_repository(
    repository_id: int,
    payload: ConnectRepositoryRequest,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> RepositoryConnectionResponse:
    require_github_configured()
    if repository_id != payload.github_repository_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Repository ID mismatch")
    connection = await github_service.connect_repository(
        db,
        user_id=user.id,
        installation_id=payload.installation_id,
        github_repository_id=payload.github_repository_id,
    )
    return _connection_response(connection)


@router.get("/connections", response_model=list[RepositoryConnectionResponse])
async def list_connections(
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RepositoryConnectionResponse]:
    rows = await github_service.list_connections(db, user.id)
    return [_connection_response(row) for row in rows]


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID,
    user: User = Depends(require_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await github_service.disconnect_connection(db, user_id=user.id, connection_id=connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/webhooks")
async def github_webhooks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
) -> dict[str, str]:
    body = await request.body()
    github_service.verify_webhook_signature(body, x_hub_signature_256)
    if not x_github_event or not x_github_delivery:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing GitHub webhook headers")

    try:
        payload: dict[str, Any] = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    action = payload.get("action")
    is_new = await github_service.record_webhook_delivery(db, x_github_delivery, x_github_event, action)
    if not is_new:
        return {"status": "duplicate"}

    if x_github_event == "installation":
        await github_service.handle_installation_webhook(db, str(action or ""), payload)
    elif x_github_event == "installation_repositories":
        await github_service.handle_installation_repositories_webhook(db, str(action or ""), payload)
    else:
        logger.info("github.webhook_ignored", event=x_github_event)

    return {"status": "ok"}
