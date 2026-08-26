from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.user import User


async def _register(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": "Agent"},
    )
    assert response.status_code == 201, response.text


async def _seed_connection(email: str) -> str:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=8001,
            account_login="fixture",
            account_type="User",
            account_id=1,
            repository_selection="all",
        )
        session.add(installation)
        await session.flush()
        connection = RepositoryConnection(
            user_id=user.id,
            installation_id=installation.id,
            github_repository_id=8001,
            owner="fixture",
            name="sample-repo",
            full_name="fixture/sample-repo",
            default_branch="main",
            private=False,
            html_url="https://github.com/fixture/sample-repo",
            is_active=True,
        )
        session.add(connection)
        await session.commit()
        return str(connection.id)


@pytest.mark.asyncio
async def test_agent_status_and_create(app_client: AsyncClient) -> None:
    email = f"agent-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    connection_id = await _seed_connection(email)

    status = await app_client.get("/api/agent-runs/status")
    assert status.status_code == 200
    assert status.json()["configured"] is True
    assert status.json()["provider"] == "fake"

    created = await app_client.post(
        "/api/agent-runs",
        json={
            "repository_connection_id": connection_id,
            "task": "Add a function that returns the sum of two integers and add tests.",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "queued"
    run_id = body["id"]

    got = await app_client.get(f"/api/agent-runs/{run_id}")
    assert got.status_code == 200
    steps = await app_client.get(f"/api/agent-runs/{run_id}/steps")
    assert steps.status_code == 200
    assert steps.json() == []


@pytest.mark.asyncio
async def test_agent_idor_and_cancel_queued(app_client: AsyncClient) -> None:
    email_a = f"aa-{uuid4().hex[:8]}@example.com"
    email_b = f"bb-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email_a)
    connection_id = await _seed_connection(email_a)
    created = await app_client.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "Investigate validators."},
    )
    run_id = created.json()["id"]

    cancelled = await app_client.post(f"/api/agent-runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    await app_client.post("/api/auth/logout")
    await _register(app_client, email_b)
    forbidden = await app_client.get(f"/api/agent-runs/{run_id}")
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_agent_rejects_empty_task(app_client: AsyncClient) -> None:
    email = f"empty-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    connection_id = await _seed_connection(email)
    bad = await app_client.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "   "},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_agent_approval_is_owner_bound_and_idempotent(app_client: AsyncClient) -> None:
    email_a = f"approve-a-{uuid4().hex[:8]}@example.com"
    email_b = f"approve-b-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email_a)
    connection_id = await _seed_connection(email_a)
    created = await app_client.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "Prepare a safe patch."},
    )
    run_id = created.json()["id"]
    diff = "diff --git a/README.md b/README.md\n"
    artifact_hash = hashlib.sha256(diff.encode()).hexdigest()
    factory = get_session_factory()
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = AgentRunStatus.awaiting_approval
        run.diff_text = diff
        run.diff_hash = artifact_hash
        run.publication_artifact = diff.encode()
        run.publication_artifact_hash = artifact_hash
        run.publication_artifact_size = len(diff.encode())
        run.publication_artifact_version = 1
        run.publication_change_manifest = [{"path": "README.md", "change_type": "modified"}]
        run.publication_artifact_status = "ready"
        run.validation = {"command": ["pytest"], "ok": True, "output": "ok"}
        run.validation_artifact_hash = artifact_hash
        run.base_commit_sha = "a" * 40
        await session.commit()

    await app_client.post("/api/auth/logout")
    await _register(app_client, email_b)
    assert (
        await app_client.post(
            f"/api/agent-runs/{run_id}/approve",
            json={"artifact_hash": artifact_hash, "artifact_version": 1, "base_commit_sha": "a" * 40},
        )
    ).status_code == 404

    await app_client.post("/api/auth/logout")
    await app_client.post("/api/auth/login", json={"email": email_a, "password": "password123"})
    stale = await app_client.post(
        f"/api/agent-runs/{run_id}/approve",
        json={"artifact_hash": "b" * 64, "artifact_version": 1, "base_commit_sha": "a" * 40},
    )
    assert stale.status_code == 409
    approved = await app_client.post(
        f"/api/agent-runs/{run_id}/approve",
        json={"artifact_hash": artifact_hash, "artifact_version": 1, "base_commit_sha": "a" * 40},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"
    assert approved.json()["publication_status"] == "approved"
    duplicate = await app_client.post(
        f"/api/agent-runs/{run_id}/approve",
        json={"artifact_hash": artifact_hash, "artifact_version": 1, "base_commit_sha": "a" * 40},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_agent_reject_is_terminal_and_cannot_cancel(app_client: AsyncClient) -> None:
    email = f"reject-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    connection_id = await _seed_connection(email)
    created = await app_client.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "Prepare a patch."},
    )
    run_id = created.json()["id"]
    factory = get_session_factory()
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = AgentRunStatus.awaiting_approval
        run.diff_text = "patch"
        run.diff_hash = hashlib.sha256(b"patch").hexdigest()
        await session.commit()

    rejected = await app_client.post(f"/api/agent-runs/{run_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["publication_status"] == "rejected"
    assert (await app_client.post(f"/api/agent-runs/{run_id}/cancel")).status_code == 409


@pytest.mark.asyncio
async def test_legacy_diff_cannot_be_approved(app_client: AsyncClient) -> None:
    email = f"legacy-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    connection_id = await _seed_connection(email)
    created = await app_client.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "Legacy patch."},
    )
    run_id = created.json()["id"]
    factory = get_session_factory()
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = AgentRunStatus.awaiting_approval
        run.diff_text = "legacy diff"
        run.diff_hash = hashlib.sha256(b"legacy diff").hexdigest()
        run.base_commit_sha = "c" * 40
        await session.commit()
    response = await app_client.post(
        f"/api/agent-runs/{run_id}/approve",
        json={"artifact_hash": "d" * 64, "artifact_version": 1, "base_commit_sha": "c" * 40},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_malformed_manifest_cannot_be_approved(app_client: AsyncClient) -> None:
    email = f"manifest-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    connection_id = await _seed_connection(email)
    created = await app_client.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "Malformed manifest."},
    )
    run_id = created.json()["id"]
    artifact = b"safe artifact"
    artifact_hash = hashlib.sha256(artifact).hexdigest()
    factory = get_session_factory()
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = AgentRunStatus.awaiting_approval
        run.base_commit_sha = "e" * 40
        run.publication_artifact = artifact
        run.publication_artifact_hash = artifact_hash
        run.publication_artifact_size = len(artifact)
        run.publication_artifact_version = 1
        run.publication_artifact_status = "ready"
        run.publication_change_manifest = {"path": "README.md"}
        run.diff_hash = artifact_hash
        run.validation = {"command": ["pytest"], "ok": True}
        run.validation_artifact_hash = artifact_hash
        await session.commit()
    response = await app_client.post(
        f"/api/agent-runs/{run_id}/approve",
        json={"artifact_hash": artifact_hash, "artifact_version": 1, "base_commit_sha": "e" * 40},
    )
    assert response.status_code == 409
