from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class GitHubStatusResponse(BaseModel):
    configured: bool
    linked: bool
    github_login: str | None = None
    installation_count: int = 0
    connection_count: int = 0


class GitHubConnectResponse(BaseModel):
    authorize_url: str


class GitHubAccountResponse(BaseModel):
    id: UUID
    github_user_id: int
    github_login: str
    account_type: str
    avatar_url: str | None = None


class GitHubInstallationResponse(BaseModel):
    id: UUID
    github_installation_id: int
    account_login: str
    account_type: str
    repository_selection: str
    suspended: bool
    created_at: datetime


class GitHubRepositoryResponse(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str
    owner: str


class GitHubRepositoryListResponse(BaseModel):
    total_count: int
    page: int
    per_page: int
    repositories: list[GitHubRepositoryResponse]


class ConnectRepositoryRequest(BaseModel):
    installation_id: UUID
    github_repository_id: int = Field(gt=0)


class RepositoryConnectionResponse(BaseModel):
    id: UUID
    github_repository_id: int
    installation_id: UUID
    owner: str
    name: str
    full_name: str
    default_branch: str
    private: bool
    html_url: str
    is_active: bool
    created_at: datetime
