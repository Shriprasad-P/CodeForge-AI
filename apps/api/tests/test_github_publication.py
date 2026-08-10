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
