from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.mark.asyncio
async def test_check_db_live_against_postgres(app_client: AsyncClient) -> None:
    from app.db.session import check_db

    assert await check_db() is True


@pytest.mark.asyncio
async def test_register_success(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/auth/register",
        json={
            "email": "Ada@Example.com",
            "password": "password123",
            "display_name": "Ada",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["display_name"] == "Ada"
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert settings.session_cookie_name in response.cookies


@pytest.mark.asyncio
async def test_register_duplicate_email(app_client: AsyncClient) -> None:
    payload = {
        "email": "dup@example.com",
        "password": "password123",
        "display_name": "One",
    }
    assert (await app_client.post("/api/auth/register", json=payload)).status_code == 201
    response = await app_client.post(
        "/api/auth/register",
        json={**payload, "display_name": "Two"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_invalid_email(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "password123", "display_name": "X"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_weak_password(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "password": "short", "display_name": "X"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_success_and_me(app_client: AsyncClient) -> None:
    await app_client.post(
        "/api/auth/register",
        json={"email": "login@example.com", "password": "password123", "display_name": "Lee"},
    )
    # Clear cookies to force login path
    app_client.cookies.clear()
    login = await app_client.post(
        "/api/auth/login",
        json={"email": "Login@Example.com", "password": "password123"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "login@example.com"

    me = await app_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "login@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password(app_client: AsyncClient) -> None:
    await app_client.post(
        "/api/auth/register",
        json={"email": "wrong@example.com", "password": "password123", "display_name": "W"},
    )
    app_client.cookies.clear()
    response = await app_client.post(
        "/api/auth/login",
        json={"email": "wrong@example.com", "password": "nope-nope"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_login_unknown_email(app_client: AsyncClient) -> None:
    response = await app_client.post(
        "/api/auth/login",
        json={"email": "missing@example.com", "password": "password123"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


@pytest.mark.asyncio
async def test_me_unauthenticated(app_client: AsyncClient) -> None:
    response = await app_client.get("/api/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_invalidates_session(app_client: AsyncClient) -> None:
    await app_client.post(
        "/api/auth/register",
        json={"email": "out@example.com", "password": "password123", "display_name": "Out"},
    )
    assert (await app_client.get("/api/auth/me")).status_code == 200
    logout = await app_client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert (await app_client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_ownership_isolation(app_client: AsyncClient) -> None:
    a = await app_client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "password123", "display_name": "A"},
    )
    assert a.status_code == 201
    created = await app_client.post("/api/agent-sessions", json={"title": "A's task"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    app_client.cookies.clear()
    await app_client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "password": "password123", "display_name": "B"},
    )
    forbidden = await app_client.get(f"/api/agent-sessions/{session_id}")
    assert forbidden.status_code == 404
