from __future__ import annotations

from asyncio import gather
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import github as github_service


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_installation_state_is_server_side_atomic_and_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(github_service, "get_redis", lambda: redis)
    state = await github_service.create_installation_setup_state(
        uuid4(),
        github_user_id=42,
        github_login="octocat",
        github_user_token="oauth-token",
    )
    key = f"{github_service.INSTALLATION_STATE_PREFIX}{state}"
    assert key in redis.values
    assert "oauth-token" in redis.values[key]

    async def consume() -> dict[str, object] | HTTPException:
        try:
            return await github_service.consume_installation_setup_state(state)
        except HTTPException as exc:
            return exc

    results = await gather(consume(), consume())
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, HTTPException) for result in results) == 1


@pytest.mark.asyncio
async def test_failed_installation_state_can_be_retried_without_extending_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = _FakeRedis()
    monkeypatch.setattr(github_service, "get_redis", lambda: redis)
    state = await github_service.create_installation_setup_state(
        uuid4(),
        github_user_id=42,
        github_login="octocat",
        github_user_token="oauth-token",
    )
    data = await github_service.consume_installation_setup_state(state)
    await github_service.restore_installation_setup_state(state, data)
    retry = await github_service.consume_installation_setup_state(state)
    assert retry["github_user_id"] == "42"


def test_installation_authorization_requires_matching_authoritative_views() -> None:
    installation = {"id": 7001, "account": {"id": 42, "type": "User"}}
    user_view = {"installation": {"id": 7001, "account": {"id": 42, "type": "User"}}}
    github_service.verify_installation_authorization(
        installation_id=7001,
        installation_payload=installation,
        user_installation_payload=user_view,
        authenticated_github_user={"id": 42},
        linked_github_user_id=42,
    )

    with pytest.raises(HTTPException):
        github_service.verify_installation_authorization(
            installation_id=7001,
            installation_payload=installation,
            user_installation_payload=user_view,
            authenticated_github_user={"id": 99},
            linked_github_user_id=42,
        )


def test_organization_installation_requires_active_admin_membership() -> None:
    installation = {"id": 7002, "account": {"id": 9001, "type": "Organization"}}
    user_view = {"installation": {"id": 7002, "account": {"id": 9001, "type": "Organization"}}}
    kwargs = {
        "installation_id": 7002,
        "installation_payload": installation,
        "user_installation_payload": user_view,
        "authenticated_github_user": {"id": 42},
        "linked_github_user_id": 42,
    }
    github_service.verify_installation_authorization(
        **kwargs,
        organization_membership_payload={"state": "active", "role": "admin"},
    )
    with pytest.raises(HTTPException):
        github_service.verify_installation_authorization(
            **kwargs,
            organization_membership_payload={"state": "active", "role": "member"},
        )
