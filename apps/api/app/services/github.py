from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.redis import get_redis
from app.models.github import (
    GitHubAccount,
    GitHubInstallation,
    GitHubWebhookDelivery,
    RepositoryConnection,
)
from app.models.user import User
from app.services.github_app import require_github_configured
from app.services.github_client import GitHubAPIError, GitHubClient, raise_http_for_github

logger = get_logger(__name__)

OAUTH_STATE_PREFIX = "github:oauth:"
INSTALLATION_STATE_PREFIX = "github:installation:"
INSTALLATION_STATE_PURPOSE = "installation_setup"


def build_oauth_authorize_url(state: str) -> str:
    require_github_configured()
    from urllib.parse import urlencode

    cfg = get_settings()
    query = urlencode(
        {
            "client_id": cfg.github_app_client_id,
            "redirect_uri": cfg.github_callback_url,
            "state": state,
            # read:org is required to verify organization administrator
            # membership during installation setup.
            "scope": "read:user read:org",
            "allow_signup": "false",
        }
    )
    return f"{cfg.github_oauth_base_url}/login/oauth/authorize?{query}"


def build_install_url(state: str | None = None) -> str:
    require_github_configured()
    cfg = get_settings()
    base = f"{cfg.github_oauth_base_url}/apps/{cfg.github_app_slug}/installations/new"
    if state:
        return f"{base}?state={state}"
    return base


async def create_oauth_state(user_id: UUID, *, purpose: str = "link") -> str:
    cfg = get_settings()
    state = secrets.token_urlsafe(32)
    redis = get_redis()
    payload = json.dumps({"user_id": str(user_id), "purpose": purpose})
    await redis.setex(f"{OAUTH_STATE_PREFIX}{state}", cfg.github_oauth_state_ttl_seconds, payload)
    return state


async def create_installation_setup_state(
    user_id: UUID,
    *,
    github_user_id: int,
    github_login: str,
    github_user_token: str,
) -> str:
    """Create a one-use installation state bound to the linked GitHub identity.

    The OAuth token is held only in Redis for the short state TTL. It is never
    persisted in PostgreSQL or returned to the browser. It is required to ask
    GitHub whether the linked user is authorized for the installation.
    """
    if not github_user_token:
        raise ValueError("A GitHub user token is required for installation setup")
    cfg = get_settings()
    state = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + cfg.github_oauth_state_ttl_seconds
    payload = json.dumps(
        {
            "user_id": str(user_id),
            "purpose": INSTALLATION_STATE_PURPOSE,
            "github_user_id": str(github_user_id),
            "github_login": github_login,
            "github_user_token": github_user_token,
            "expires_at": expires_at,
        }
    )
    await get_redis().setex(
        f"{INSTALLATION_STATE_PREFIX}{state}",
        cfg.github_oauth_state_ttl_seconds,
        payload,
    )
    return state


async def _consume_state(prefix: str, state: str) -> dict[str, Any]:
    redis = get_redis()
    # GETDEL is atomic on the Redis version used by Compose. A separate GET
    # followed by DELETE permits two concurrent callbacks to consume a state.
    raw = await redis.getdel(f"{prefix}{state}")
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OAuth state")
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state") from exc
    if not isinstance(data, dict) or not data.get("user_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state")
    return data


async def consume_oauth_state(state: str, *, expected_purpose: str = "link") -> dict[str, Any]:
    data = await _consume_state(OAUTH_STATE_PREFIX, state)
    if data.get("purpose", "link") != expected_purpose:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OAuth state purpose")
    return data


async def consume_installation_setup_state(state: str) -> dict[str, Any]:
    data = await _consume_state(INSTALLATION_STATE_PREFIX, state)
    if data.get("purpose") != INSTALLATION_STATE_PURPOSE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid installation state")
    required = ("github_user_id", "github_user_token")
    if any(not data.get(key) for key in required):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid installation state")
    return data


async def restore_installation_setup_state(state: str, data: dict[str, Any]) -> None:
    """Return a failed setup state for a bounded retry, without extending it."""
    try:
        expires_at = int(data.get("expires_at") or 0)
    except (TypeError, ValueError):
        return
    remaining = expires_at - int(time.time())
    if remaining <= 0:
        return
    await get_redis().setex(
        f"{INSTALLATION_STATE_PREFIX}{state}",
        remaining,
        json.dumps(data),
    )


def verify_installation_authorization(
    *,
    installation_id: int,
    installation_payload: dict[str, Any],
    user_installation_payload: dict[str, Any],
    authenticated_github_user: dict[str, Any],
    linked_github_user_id: int,
    organization_membership_payload: dict[str, Any] | None = None,
) -> None:
    """Verify App and linked-user views describe the same authorized install."""
    authenticated_id = int(authenticated_github_user.get("id") or 0)
    if authenticated_id != linked_github_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="GitHub identity mismatch")

    app_installation_id = int(installation_payload.get("id") or 0)
    user_view = user_installation_payload.get("installation") or user_installation_payload
    if "installations" in user_installation_payload:
        matches = [
            item
            for item in user_installation_payload.get("installations") or []
            if int(item.get("id") or 0) == installation_id
        ]
        if len(matches) != 1:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="GitHub installation is not authorized")
        user_view = matches[0]
    user_installation_id = int(user_view.get("id") or 0)
    if app_installation_id != installation_id or user_installation_id != installation_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="GitHub installation is not authorized")

    app_account = installation_payload.get("account") or {}
    user_account = user_view.get("account") or {}
    app_account_id = int(app_account.get("id") or 0)
    user_account_id = int(user_account.get("id") or 0)
    if not app_account_id or app_account_id != user_account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="GitHub installation account mismatch")

    account_type = str(app_account.get("type") or "").lower()
    if account_type == "user":
        if app_account_id != linked_github_user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="GitHub personal installation mismatch")
    elif account_type == "organization":
        membership = organization_membership_payload or {}
        if membership.get("state") != "active" or membership.get("role") != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="GitHub organization administrator authorization required",
            )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unsupported GitHub installation account")


def verify_webhook_signature(body: bytes, signature_header: str | None) -> None:
    cfg = get_settings()
    if not cfg.github_webhook_configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GitHub webhooks are not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature")
    digest = hmac.new(
        cfg.github_webhook_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    expected = f"sha256={digest}"
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")


async def link_github_account(
    db: AsyncSession,
    *,
    user: User,
    github_user: dict[str, Any],
) -> GitHubAccount:
    github_user_id = int(github_user["id"])
    login = str(github_user["login"])
    account_type = str(github_user.get("type") or "User")
    avatar_url = github_user.get("avatar_url")

    existing_other = await db.scalar(
        select(GitHubAccount).where(
            GitHubAccount.github_user_id == github_user_id,
            GitHubAccount.user_id != user.id,
        )
    )
    if existing_other is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This GitHub account is already linked to another AgentDock user",
        )

    account = await db.scalar(select(GitHubAccount).where(GitHubAccount.user_id == user.id))
    if account is None:
        account = GitHubAccount(
            user_id=user.id,
            github_user_id=github_user_id,
            github_login=login,
            account_type=account_type,
            avatar_url=avatar_url,
        )
        db.add(account)
    else:
        account.github_user_id = github_user_id
        account.github_login = login
        account.account_type = account_type
        account.avatar_url = avatar_url

    # Keep optional denormalized pointer for later provider auth without changing password login.
    user.provider_user_id = str(github_user_id)
    await db.flush()
    logger.info("github.account_linked", user_id=str(user.id))
    return account


async def upsert_installation(
    db: AsyncSession,
    *,
    user_id: UUID,
    installation_payload: dict[str, Any],
    github_account_id: UUID | None = None,
) -> GitHubInstallation:
    installation_id = int(installation_payload["id"])
    account = installation_payload.get("account") or {}
    # Let PostgreSQL serialize concurrent claims on the unique installation ID.
    # The loser observes the committed owner and fails closed below.
    await db.execute(
        pg_insert(GitHubInstallation)
        .values(
            user_id=user_id,
            github_installation_id=installation_id,
            github_account_id=github_account_id,
            account_login=str(account.get("login") or "unknown"),
            account_type=str(account.get("type") or "User"),
            account_id=int(account.get("id") or 0),
            repository_selection=str(installation_payload.get("repository_selection") or "selected"),
        )
        .on_conflict_do_nothing(index_elements=["github_installation_id"])
    )
    row = await db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.github_installation_id == installation_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Installation claim could not be completed")
    if row.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Installation is linked to another AgentDock user",
        )
    row.github_account_id = github_account_id or row.github_account_id
    row.account_login = str(account.get("login") or row.account_login)
    row.account_type = str(account.get("type") or row.account_type)
    row.account_id = int(account.get("id") or row.account_id)
    row.repository_selection = str(installation_payload.get("repository_selection") or row.repository_selection)
    row.suspended_at = datetime.now(UTC) if installation_payload.get("suspended_at") else None
    await db.flush()
    return row


async def list_user_installations(db: AsyncSession, user_id: UUID) -> list[GitHubInstallation]:
    result = await db.scalars(
        select(GitHubInstallation).where(GitHubInstallation.user_id == user_id).order_by(GitHubInstallation.created_at)
    )
    return list(result)


async def get_owned_installation(
    db: AsyncSession,
    *,
    user_id: UUID,
    installation_id: UUID,
) -> GitHubInstallation:
    row = await db.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.id == installation_id,
            GitHubInstallation.user_id == user_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Installation not found")
    if row.suspended_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="GitHub installation is suspended")
    return row


async def list_accessible_repositories(
    db: AsyncSession,
    *,
    user_id: UUID,
    installation_id: UUID,
    page: int,
    per_page: int,
    client: GitHubClient | None = None,
) -> dict[str, Any]:
    installation = await get_owned_installation(db, user_id=user_id, installation_id=installation_id)
    gh = client or GitHubClient()
    try:
        token = await gh.create_installation_token(installation.github_installation_id)
        payload = await gh.list_installation_repositories(token, page=page, per_page=per_page)
    except GitHubAPIError as exc:
        raise_http_for_github(exc)
    repos = []
    for repo in payload.get("repositories") or []:
        repos.append(
            {
                "id": int(repo["id"]),
                "name": repo["name"],
                "full_name": repo["full_name"],
                "private": bool(repo.get("private")),
                "default_branch": repo.get("default_branch") or "main",
                "html_url": repo.get("html_url") or "",
                "owner": (repo.get("owner") or {}).get("login") or repo["full_name"].split("/")[0],
            }
        )
    return {
        "total_count": int(payload.get("total_count") or len(repos)),
        "page": page,
        "per_page": per_page,
        "repositories": repos,
    }


async def connect_repository(
    db: AsyncSession,
    *,
    user_id: UUID,
    installation_id: UUID,
    github_repository_id: int,
    client: GitHubClient | None = None,
) -> RepositoryConnection:
    installation = await get_owned_installation(db, user_id=user_id, installation_id=installation_id)
    gh = client or GitHubClient()
    try:
        token = await gh.create_installation_token(installation.github_installation_id)
        # Confirm the repo is visible to this installation, then fetch by id.
        listing = await gh.list_installation_repositories(token, page=1, per_page=100)
        allowed_ids = {int(r["id"]) for r in listing.get("repositories") or []}
        # Scan a few pages for large orgs (bounded).
        total = int(listing.get("total_count") or 0)
        page = 2
        while github_repository_id not in allowed_ids and (page - 1) * 100 < min(total, 500):
            more = await gh.list_installation_repositories(token, page=page, per_page=100)
            allowed_ids.update(int(r["id"]) for r in more.get("repositories") or [])
            page += 1
        if github_repository_id not in allowed_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not accessible")
        repo = await gh.get_repository_by_id(token, github_repository_id)
    except GitHubAPIError as exc:
        raise_http_for_github(exc)

    if int(repo["id"]) != github_repository_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Repository ID mismatch")

    existing = await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.user_id == user_id,
            RepositoryConnection.github_repository_id == github_repository_id,
        )
    )
    owner_login = (repo.get("owner") or {}).get("login") or str(repo["full_name"]).split("/")[0]
    if existing is not None:
        existing.installation_id = installation.id
        existing.owner = owner_login
        existing.name = str(repo["name"])
        existing.full_name = str(repo["full_name"])
        existing.default_branch = str(repo.get("default_branch") or "main")
        existing.private = bool(repo.get("private"))
        existing.html_url = str(repo.get("html_url") or "")
        existing.is_active = True
        await db.flush()
        return existing

    connection = RepositoryConnection(
        user_id=user_id,
        installation_id=installation.id,
        github_repository_id=github_repository_id,
        owner=owner_login,
        name=str(repo["name"]),
        full_name=str(repo["full_name"]),
        default_branch=str(repo.get("default_branch") or "main"),
        private=bool(repo.get("private")),
        html_url=str(repo.get("html_url") or ""),
        is_active=True,
    )
    db.add(connection)
    await db.flush()
    return connection


async def list_connections(db: AsyncSession, user_id: UUID) -> list[RepositoryConnection]:
    result = await db.scalars(
        select(RepositoryConnection)
        .where(
            RepositoryConnection.user_id == user_id,
            RepositoryConnection.is_active.is_(True),
        )
        .order_by(RepositoryConnection.created_at.desc())
    )
    return list(result)


async def disconnect_connection(db: AsyncSession, *, user_id: UUID, connection_id: UUID) -> None:
    row = await db.scalar(
        select(RepositoryConnection).where(
            RepositoryConnection.id == connection_id,
            RepositoryConnection.user_id == user_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
    row.is_active = False
    await db.flush()


async def record_webhook_delivery(db: AsyncSession, delivery_id: str, event: str, action: str | None) -> bool:
    """Return False if delivery already processed."""
    existing = await db.scalar(
        select(GitHubWebhookDelivery).where(GitHubWebhookDelivery.delivery_id == delivery_id)
    )
    if existing is not None:
        return False
    db.add(GitHubWebhookDelivery(delivery_id=delivery_id, event=event, action=action))
    await db.flush()
    return True


async def handle_installation_webhook(db: AsyncSession, action: str, payload: dict[str, Any]) -> None:
    installation = payload.get("installation") or {}
    installation_id = int(installation["id"])
    account = installation.get("account") or {}

    row = await db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.github_installation_id == installation_id)
    )

    if action in {"deleted"}:
        if row is not None:
            # Cascade removes connections via FK.
            await db.delete(row)
            await db.flush()
            logger.info("github.installation_deleted", installation_id=installation_id)
        return

    if action in {"suspend"}:
        if row is not None:
            row.suspended_at = datetime.now(UTC)
            await db.flush()
        return

    if action in {"unsuspend", "new_permissions_accepted", "created"}:
        if row is None:
            # Orphan installation: store under no user until setup callback claims it.
            # We require user binding via setup URL; skip creating orphan rows without user.
            if action == "created":
                logger.info("github.installation_created_unclaimed", installation_id=installation_id)
            return
        row.account_login = str(account.get("login") or row.account_login)
        row.account_type = str(account.get("type") or row.account_type)
        row.account_id = int(account.get("id") or row.account_id)
        row.repository_selection = str(installation.get("repository_selection") or row.repository_selection)
        if action == "unsuspend":
            row.suspended_at = None
        await db.flush()


async def handle_installation_repositories_webhook(db: AsyncSession, action: str, payload: dict[str, Any]) -> None:
    installation = payload.get("installation") or {}
    installation_id = int(installation["id"])
    row = await db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.github_installation_id == installation_id)
    )
    if row is None:
        return
    row.repository_selection = str(installation.get("repository_selection") or row.repository_selection)
    if action == "removed":
        removed = payload.get("repositories_removed") or []
        removed_ids = {int(r["id"]) for r in removed if "id" in r}
        if removed_ids:
            connections = await db.scalars(
                select(RepositoryConnection).where(
                    RepositoryConnection.installation_id == row.id,
                    RepositoryConnection.github_repository_id.in_(removed_ids),
                )
            )
            for connection in connections:
                connection.is_active = False
    await db.flush()
