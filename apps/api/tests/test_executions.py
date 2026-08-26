from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.execution import ExecutionJob, ExecutionJobStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.outbox import OutboxEvent
from app.models.user import User


async def _register(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": "Exec"},
    )
    assert response.status_code == 201, response.text


async def _seed_connection(email: str) -> tuple[str, str]:
    factory = get_session_factory()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=9001,
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
            github_repository_id=4242,
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
        return str(user.id), str(connection.id)


@pytest.mark.asyncio
async def test_create_list_get_execution(app_client: AsyncClient) -> None:
    email = f"exec-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    _, connection_id = await _seed_connection(email)

    created = await app_client.post(
        "/api/executions",
        json={
            "repository_connection_id": connection_id,
            "command": ["python", "hello.py"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "queued"
    assert body["command"] == ["python", "hello.py"]
    job_id = body["id"]

    factory = get_session_factory()
    async with factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "execution.requested",
                OutboxEvent.aggregate_id == job_id,
            )
        )
        assert event is not None
        assert event.status == "pending"
        assert event.payload["execution_id"] == job_id
        assert event.payload["workflow_correlation_id"]
        assert event.payload["request_id"]

    listed = await app_client.get("/api/executions")
    assert listed.status_code == 200
    assert any(row["id"] == job_id for row in listed.json())

    got = await app_client.get(f"/api/executions/{job_id}")
    assert got.status_code == 200
    assert got.json()["id"] == job_id

    logs = await app_client.get(f"/api/executions/{job_id}/logs")
    assert logs.status_code == 200


@pytest.mark.asyncio
async def test_execution_idor_and_foreign_repo(app_client: AsyncClient) -> None:
    email_a = f"a-{uuid4().hex[:8]}@example.com"
    email_b = f"b-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email_a)
    _, connection_a = await _seed_connection(email_a)

    created = await app_client.post(
        "/api/executions",
        json={"repository_connection_id": connection_a, "command": ["python", "-c", "print(1)"]},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    await app_client.post("/api/auth/logout")
    await _register(app_client, email_b)

    forbidden = await app_client.get(f"/api/executions/{job_id}")
    assert forbidden.status_code == 404

    foreign = await app_client.post(
        "/api/executions",
        json={"repository_connection_id": connection_a, "command": ["python", "-c", "print(1)"]},
    )
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_reject_path_traversal_and_shell(app_client: AsyncClient) -> None:
    email = f"path-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    _, connection_id = await _seed_connection(email)

    bad_dir = await app_client.post(
        "/api/executions",
        json={
            "repository_connection_id": connection_id,
            "command": ["python", "-c", "print(1)"],
            "working_directory": "../etc",
        },
    )
    assert bad_dir.status_code == 422

    abs_dir = await app_client.post(
        "/api/executions",
        json={
            "repository_connection_id": connection_id,
            "command": ["python", "-c", "print(1)"],
            "working_directory": "/etc",
        },
    )
    assert abs_dir.status_code == 422

    shell = await app_client.post(
        "/api/executions",
        json={
            "repository_connection_id": connection_id,
            "command": ["bash", "-c", "echo hi"],
        },
    )
    assert shell.status_code == 422


@pytest.mark.asyncio
async def test_cancel_queued_execution(app_client: AsyncClient) -> None:
    email = f"cancel-{uuid4().hex[:8]}@example.com"
    await _register(app_client, email)
    _, connection_id = await _seed_connection(email)

    created = await app_client.post(
        "/api/executions",
        json={"repository_connection_id": connection_id, "command": ["python", "-c", "print(1)"]},
    )
    job_id = created.json()["id"]
    cancelled = await app_client.post(f"/api/executions/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    factory = get_session_factory()
    async with factory() as session:
        job = await session.get(ExecutionJob, created.json()["id"])
        # UUID from JSON
        from uuid import UUID

        job = await session.get(ExecutionJob, UUID(job_id))
        assert job is not None
        assert job.status == ExecutionJobStatus.cancelled
