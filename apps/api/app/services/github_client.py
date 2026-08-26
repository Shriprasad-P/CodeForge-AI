from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import classify_error, metrics, persist_metric
from app.services.github_app import create_app_jwt, require_github_configured

logger = get_logger(__name__)

USER_AGENT = "AgentDock-GitHubApp"


class GitHubAPIError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _headers(token: str, *, token_type: str = "Bearer") -> dict[str, str]:
    return {
        "Authorization": f"{token_type} {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }


def raise_http_for_github(exc: GitHubAPIError) -> None:
    if exc.status_code == 403 and exc.retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="GitHub API rate limit exceeded. Try again later.",
            headers={"Retry-After": exc.retry_after},
        ) from exc
    if exc.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="GitHub resource not found") from exc
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="GitHub API request failed",
    ) from exc


class GitHubClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        settings = get_settings()
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=settings.github_http_timeout_seconds)
        started = time.perf_counter()
        try:
            response = await client.request(method, url, headers=headers, params=params, json=json)
        except httpx.HTTPError as exc:
            metrics.inc("github_request_failures_total")
            await persist_metric("github_request_failures_total")
            logger.warning("github.http_error", error_class=classify_error(exc), retryable=True)
            raise GitHubAPIError("GitHub unreachable") from exc
        finally:
            if owns:
                await client.aclose()

        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubAPIError(
                "rate limited",
                status_code=403,
                retry_after=response.headers.get("retry-after") or response.headers.get("x-ratelimit-reset"),
            )
        if response.status_code >= 400:
            metrics.inc("github_request_failures_total")
            await persist_metric("github_request_failures_total")
            logger.info("github.api_error", status=response.status_code, retryable=response.status_code >= 500)
            raise GitHubAPIError("GitHub API error", status_code=response.status_code)
        metrics.observe_duration("github_request_duration_ms", (time.perf_counter() - started) * 1000)
        if response.status_code == 204 or not expect_json:
            return None
        return response.json()

    async def exchange_oauth_code(self, code: str) -> str:
        require_github_configured()
        settings = get_settings()
        url = f"{settings.github_oauth_base_url}/login/oauth/access_token"
        data = await self._request(
            "POST",
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            json={
                "client_id": settings.github_app_client_id,
                "client_secret": settings.github_app_client_secret,
                "code": code,
                "redirect_uri": settings.github_callback_url,
            },
        )
        token = (data or {}).get("access_token")
        if not token:
            raise GitHubAPIError("OAuth exchange failed", status_code=400)
        return str(token)

    async def get_authenticated_user(self, user_token: str) -> dict[str, Any]:
        settings = get_settings()
        return await self._request(
            "GET",
            f"{settings.github_api_base_url}/user",
            headers=_headers(user_token, token_type="Bearer"),
        )

    async def create_installation_token(self, installation_id: int) -> str:
        settings = get_settings()
        app_jwt = create_app_jwt()
        data = await self._request(
            "POST",
            f"{settings.github_api_base_url}/app/installations/{installation_id}/access_tokens",
            headers=_headers(app_jwt, token_type="Bearer"),
            json={},
        )
        token = (data or {}).get("token")
        if not token:
            raise GitHubAPIError("Installation token missing", status_code=502)
        return str(token)

    async def get_installation(self, installation_id: int) -> dict[str, Any]:
        settings = get_settings()
        app_jwt = create_app_jwt()
        return await self._request(
            "GET",
            f"{settings.github_api_base_url}/app/installations/{installation_id}",
            headers=_headers(app_jwt, token_type="Bearer"),
        )

    async def get_user_installation(self, installation_id: int, user_token: str) -> dict[str, Any]:
        """Find an installation the linked user can explicitly access.

        GitHub exposes this authorization view through the paginated
        ``/user/installations`` endpoint. The returned wrapper is retained so
        the caller can compare the installation account with the App JWT view.
        """
        settings = get_settings()
        page = 1
        while True:
            payload = await self._request(
                "GET",
                f"{settings.github_api_base_url}/user/installations",
                headers=_headers(user_token, token_type="Bearer"),
                params={"page": page, "per_page": 100},
            )
            installations = payload.get("installations") or []
            for installation in installations:
                if int(installation.get("id") or 0) == installation_id:
                    return {"installation": installation}
            total_count = int(payload.get("total_count") or len(installations))
            if page * 100 >= total_count or not installations:
                break
            page += 1
        raise GitHubAPIError("GitHub installation is not authorized", status_code=404)

    async def get_organization_membership(self, organization: str, user_token: str) -> dict[str, Any]:
        """Return the linked user's organization membership and role."""
        settings = get_settings()
        return await self._request(
            "GET",
            f"{settings.github_api_base_url}/user/memberships/orgs/{organization}",
            headers=_headers(user_token, token_type="Bearer"),
        )

    async def list_installation_repositories(
        self,
        installation_token: str,
        *,
        page: int = 1,
        per_page: int = 30,
    ) -> dict[str, Any]:
        settings = get_settings()
        return await self._request(
            "GET",
            f"{settings.github_api_base_url}/installation/repositories",
            headers=_headers(installation_token, token_type="Bearer"),
            params={"page": page, "per_page": per_page},
        )

    async def get_repository(self, installation_token: str, owner: str, repo: str) -> dict[str, Any]:
        settings = get_settings()
        return await self._request(
            "GET",
            f"{settings.github_api_base_url}/repos/{owner}/{repo}",
            headers=_headers(installation_token, token_type="Bearer"),
        )

    async def get_repository_by_id(self, installation_token: str, repository_id: int) -> dict[str, Any]:
        settings = get_settings()
        return await self._request(
            "GET",
            f"{settings.github_api_base_url}/repositories/{repository_id}",
            headers=_headers(installation_token, token_type="Bearer"),
        )

    async def create_pull_request(
        self,
        installation_token: str,
        *,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        settings = get_settings()
        return await self._request(
            "POST",
            f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls",
            headers=_headers(installation_token, token_type="Bearer"),
            json={"title": title, "body": body, "head": head, "base": base},
        )

    async def find_pull_request(
        self,
        installation_token: str,
        *,
        owner: str,
        repo: str,
        head: str,
        base: str,
    ) -> dict[str, Any] | None:
        settings = get_settings()
        rows = await self._request(
            "GET",
            f"{settings.github_api_base_url}/repos/{owner}/{repo}/pulls",
            headers=_headers(installation_token, token_type="Bearer"),
            params={"state": "open", "head": f"{owner}:{head}", "base": base, "per_page": 100},
        )
        return next((row for row in rows if row.get("head", {}).get("ref") == head), None)
