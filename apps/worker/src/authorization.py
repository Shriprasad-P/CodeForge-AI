"""Fresh repository authorization checks for trusted worker boundaries."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.github import GitHubInstallation, RepositoryConnection


class RepositoryRevokedError(RuntimeError):
    """The repository is no longer authorized for this user."""


async def require_repository_authorized(
    db: AsyncSession,
    *,
    user_id: UUID,
    connection_id: UUID,
) -> tuple[RepositoryConnection, GitHubInstallation]:
    """Load the connection and installation with all ownership/fencing checks.

    This query is deliberately repeated immediately before each privileged
    materialization or publication boundary.  A stale ORM object must never
    authorize a token mint, checkout, push, or pull-request operation.
    """
    result = await db.execute(
        select(RepositoryConnection, GitHubInstallation)
        .join(
            GitHubInstallation,
            GitHubInstallation.id == RepositoryConnection.installation_id,
        )
        .where(
            RepositoryConnection.id == connection_id,
            RepositoryConnection.user_id == user_id,
            RepositoryConnection.is_active.is_(True),
            GitHubInstallation.id == RepositoryConnection.installation_id,
            GitHubInstallation.user_id == user_id,
            GitHubInstallation.suspended_at.is_(None),
        )
    )
    row = result.first()
    if row is None:
        raise RepositoryRevokedError("repository authorization revoked")
    return row[0], row[1]
