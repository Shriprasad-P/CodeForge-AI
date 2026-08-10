from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.github import GitHubInstallation, RepositoryConnection
from app.services.github_app import create_app_jwt, decode_app_jwt_unsafe_for_tests


def _generate_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


@pytest.fixture
def github_settings(monkeypatch: pytest.MonkeyPatch) -> str:
    pem = _generate_pem()
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_SLUG", "agentdock-test")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "whsec-test")
    monkeypatch.setenv("GITHUB_CALLBACK_URL", "http://localhost:8000/api/github/callback")
    get_settings.cache_clear()
    import app.core.config as config_mod

    config_mod.settings = get_settings()
    yield pem
    get_settings.cache_clear()
    config_mod.settings = get_settings()


async def _register(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": email.split("@")[0]},
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_github_status_unconfigured(app_client: AsyncClient) -> None:
    await _register(app_client, "plain@example.com")
    response = await app_client.get("/api/github/status")
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["linked"] is False


@pytest.mark.asyncio
async def test_create_app_jwt(github_settings: str) -> None:
    token = create_app_jwt(now=datetime.now(UTC))
    payload = decode_app_jwt_unsafe_for_tests(token)
    assert payload["iss"] == "123456"
    assert payload["exp"] - payload["iat"] <= 10 * 60


@pytest.mark.asyncio
async def test_connect_requires_config(app_client: AsyncClient) -> None:
    await _register(app_client, "noconfig@example.com")
    response = await app_client.get("/api/github/connect")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_oauth_state_and_callback(app_client: AsyncClient, github_settings: str) -> None:
    await _register(app_client, "link@example.com")
    connect = await app_client.get("/api/github/connect")
    assert connect.status_code == 200
    url = connect.json()["authorize_url"]
    assert "client_id=client-id" in url
    state = url.split("state=")[1].split("&")[0]

    with patch("app.api.github.GitHubClient") as client_cls:
        instance = client_cls.return_value
        instance.exchange_oauth_code = AsyncMock(return_value="user-token")
        instance.get_authenticated_user = AsyncMock(
            return_value={
                "id": 42,
                "login": "octocat",
                "type": "User",
                "avatar_url": "https://example.com/a.png",
            }
        )
        response = await app_client.get(
            "/api/github/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert "/apps/agentdock-test/installations/new" in response.headers["location"]

    # Replay state fails
    replay = await app_client.get(
        "/api/github/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert replay.status_code == 302
    assert "invalid_state" in replay.headers["location"]

    status = await app_client.get("/api/github/status")
    assert status.json()["linked"] is True
    assert status.json()["github_login"] == "octocat"


@pytest.mark.asyncio
async def test_invalid_oauth_state(app_client: AsyncClient, github_settings: str) -> None:
    response = await app_client.get(
        "/api/github/callback",
        params={"code": "abc", "state": "nope"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "invalid_state" in response.headers["location"]


@pytest.mark.asyncio
async def test_installations_and_ownership(app_client: AsyncClient, github_settings: str) -> None:
    await _register(app_client, "owner@example.com")
    factory = get_session_factory()
    async with factory() as session:
        from app.models.user import User

        user = await session.scalar(select(User).where(User.email == "owner@example.com"))
        assert user is not None
        session.add(
            GitHubInstallation(
                user_id=user.id,
                github_installation_id=999,
                account_login="acme",
                account_type="Organization",
                account_id=7,
                repository_selection="selected",
            )
        )
        await session.commit()

    mine = await app_client.get("/api/github/installations")
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    installation_id = mine.json()[0]["id"]

    app_client.cookies.clear()
    await _register(app_client, "other@example.com")
    other = await app_client.get("/api/github/installations")
    assert other.status_code == 200
    assert other.json() == []

    # Other user cannot list repos for owner's installation
    repos = await app_client.get(
        "/api/github/repositories",
        params={"installation_id": installation_id},
    )
    assert repos.status_code == 404


@pytest.mark.asyncio
async def test_repositories_and_connect(app_client: AsyncClient, github_settings: str) -> None:
    await _register(app_client, "repos@example.com")
    factory = get_session_factory()
    async with factory() as session:
        from app.models.user import User

        user = await session.scalar(select(User).where(User.email == "repos@example.com"))
        assert user is not None
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=1001,
            account_login="repos-user",
            account_type="User",
            account_id=1,
            repository_selection="all",
        )
        session.add(installation)
        await session.commit()
        installation_id = installation.id

    repo_payload = {
        "total_count": 1,
        "repositories": [
            {
                "id": 555,
                "name": "demo",
                "full_name": "repos-user/demo",
                "private": True,
                "default_branch": "main",
                "html_url": "https://github.com/repos-user/demo",
                "owner": {"login": "repos-user"},
            }
        ],
    }
    repo_detail = repo_payload["repositories"][0]

    with patch("app.services.github.GitHubClient") as client_cls:
        instance = client_cls.return_value
        instance.create_installation_token = AsyncMock(return_value="inst-token")
        instance.list_installation_repositories = AsyncMock(return_value=repo_payload)
        instance.get_repository_by_id = AsyncMock(return_value=repo_detail)

        listed = await app_client.get(
            "/api/github/repositories",
            params={"installation_id": str(installation_id)},
        )
        assert listed.status_code == 200
        assert listed.json()["repositories"][0]["id"] == 555
        assert "token" not in listed.text

        connected = await app_client.post(
            "/api/github/repositories/555/connect",
            json={"installation_id": str(installation_id), "github_repository_id": 555},
        )
        assert connected.status_code == 201
        connection_id = connected.json()["id"]

        # Duplicate connect upserts
        again = await app_client.post(
            "/api/github/repositories/555/connect",
            json={"installation_id": str(installation_id), "github_repository_id": 555},
        )
        assert again.status_code == 201
        assert again.json()["id"] == connection_id

    connections = await app_client.get("/api/github/connections")
    assert connections.status_code == 200
    assert len(connections.json()) == 1

    deleted = await app_client.delete(f"/api/github/connections/{connection_id}")
    assert deleted.status_code == 204
    assert (await app_client.get("/api/github/connections")).json() == []


@pytest.mark.asyncio
async def test_connection_idor(app_client: AsyncClient, github_settings: str) -> None:
    await _register(app_client, "a@example.com")
    factory = get_session_factory()
    async with factory() as session:
        from app.models.user import User

        user = await session.scalar(select(User).where(User.email == "a@example.com"))
        assert user is not None
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=2002,
            account_login="a",
            account_type="User",
            account_id=2,
            repository_selection="selected",
        )
        session.add(installation)
        await session.flush()
        connection = RepositoryConnection(
            user_id=user.id,
            installation_id=installation.id,
            github_repository_id=777,
            owner="a",
            name="secret",
            full_name="a/secret",
            default_branch="main",
            private=True,
            html_url="https://github.com/a/secret",
            is_active=True,
        )
        session.add(connection)
        await session.commit()
        connection_id = connection.id

    app_client.cookies.clear()
    await _register(app_client, "b@example.com")
    forbidden = await app_client.delete(f"/api/github/connections/{connection_id}")
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_webhook_signature_and_idempotency(app_client: AsyncClient, github_settings: str) -> None:
    body = b'{"action":"deleted","installation":{"id":321,"account":{"login":"x","id":1,"type":"User"}}}'
    secret = b"whsec-test"
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": "installation",
        "X-GitHub-Delivery": "delivery-1",
        "Content-Type": "application/json",
    }
    bad = await app_client.post("/api/github/webhooks", content=body, headers={**headers, "X-Hub-Signature-256": "sha256=dead"})
    assert bad.status_code == 401

    factory = get_session_factory()
    async with factory() as session:
        from app.models.user import User

        # Need a user row for FK if we insert installation; for deleted with no row, ok.
        await session.commit()

    ok = await app_client.post("/api/github/webhooks", content=body, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"
    dup = await app_client.post("/api/github/webhooks", content=body, headers=headers)
    assert dup.json()["status"] == "duplicate"


@pytest.mark.asyncio
async def test_webhook_removes_repos(app_client: AsyncClient, github_settings: str) -> None:
    await _register(app_client, "hook@example.com")
    factory = get_session_factory()
    async with factory() as session:
        from app.models.user import User

        user = await session.scalar(select(User).where(User.email == "hook@example.com"))
        assert user is not None
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=4444,
            account_login="hook",
            account_type="User",
            account_id=4,
            repository_selection="selected",
        )
        session.add(installation)
        await session.flush()
        session.add(
            RepositoryConnection(
                user_id=user.id,
                installation_id=installation.id,
                github_repository_id=88,
                owner="hook",
                name="gone",
                full_name="hook/gone",
                default_branch="main",
                private=False,
                html_url="https://github.com/hook/gone",
                is_active=True,
            )
        )
        await session.commit()

    body = (
        b'{"action":"removed","installation":{"id":4444,"repository_selection":"selected"},'
        b'"repositories_removed":[{"id":88,"name":"gone","full_name":"hook/gone"}]}'
    )
    digest = hmac.new(b"whsec-test", body, hashlib.sha256).hexdigest()
    response = await app_client.post(
        "/api/github/webhooks",
        content=body,
        headers={
            "X-Hub-Signature-256": f"sha256={digest}",
            "X-GitHub-Event": "installation_repositories",
            "X-GitHub-Delivery": str(uuid4()),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200
    connections = await app_client.get("/api/github/connections")
    assert connections.json() == []
