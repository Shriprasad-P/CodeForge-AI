from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import get_settings
from app.services.github_client import GitHubClient


@pytest.mark.asyncio
async def test_github_publication_client_create_and_find_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://api.github.test")
    get_settings.cache_clear()
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 42, "number": 7, "html_url": "https://github.test/pr/7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GitHubClient(http)
        found = await client.find_pull_request("installation-token", owner="o", repo="r", head="branch", base="main")
        created = await client.create_pull_request(
            "installation-token",
            owner="o",
            repo="r",
            title="AgentDock change",
            body="validated",
            head="branch",
            base="main",
        )

    assert found is None
    assert created["number"] == 7
    assert calls[0].url.params["head"] == "o:branch"
    assert calls[1].headers["authorization"] == "Bearer installation-token"
    assert json.loads(calls[1].content) == {"title": "AgentDock change", "body": "validated", "head": "branch", "base": "main"}
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_github_user_installation_authorization_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://api.github.test")
    get_settings.cache_clear()
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "total_count": 101,
                    "installations": [
                        {"id": index, "account": {"id": index, "type": "Organization"}}
                        for index in range(1, 101)
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "total_count": 101,
                "installations": [{"id": 101, "account": {"id": 9001, "type": "Organization"}}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GitHubClient(http)
        result = await client.get_user_installation(101, "user-token")

    assert result["installation"]["id"] == 101
    assert [request.url.params["page"] for request in calls] == ["1", "2"]
    assert all(request.headers["authorization"] == "Bearer user-token" for request in calls)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_github_organization_membership_request_uses_user_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_API_BASE_URL", "https://api.github.test")
    get_settings.cache_clear()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/memberships/orgs/acme"
        assert request.headers["authorization"] == "Bearer user-token"
        return httpx.Response(200, json={"state": "active", "role": "admin"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = GitHubClient(http)
        result = await client.get_organization_membership("acme", "user-token")

    assert result == {"state": "active", "role": "admin"}
    get_settings.cache_clear()
