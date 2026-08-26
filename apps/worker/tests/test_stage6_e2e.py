"""Deterministic Stage 6 golden-path proof.

Run explicitly with ``AGENTDOCK_STAGE6_E2E=1``.  The test uses real
PostgreSQL, Redis, and Docker; GitHub is represented by a recording stub.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

# ruff: noqa: E402
docker = pytest.importorskip("docker")

from app import create_app
from app.core import config as config_module
from app.core.config import get_settings
from app.core.observability import metrics
from app.db.redis import close_redis, init_redis
from app.db.session import close_db, get_session_factory, init_db
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.services.outbox import PUBLICATION_REQUESTED
from sandbox_sdk.docker_provider import DockerSandboxProvider
from src.agent.llm import FakeLLMProvider
from src.delivery import process_outbox_event


pytestmark = pytest.mark.skipif(
    os.getenv("AGENTDOCK_STAGE6_E2E") != "1", reason="set AGENTDOCK_STAGE6_E2E=1 for real-dependency E2E"
)


def _git(path: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], check=check, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = [pytestmark, pytest.mark.skipif(not _docker_available(), reason="Docker daemon unavailable")]


@pytest.mark.asyncio
async def test_stage6_golden_path_real_dependencies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / "fixture"
    shutil.copytree(root / "fixtures" / "sample-repo", source)
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "AgentDock Fixture")
    _git(source, "config", "user.email", "fixture@example.com")
    _git(source, "add", "-A")
    _git(source, "commit", "-m", "base")
    base_sha = _git(source, "rev-parse", "HEAD")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(source), str(bare)], check=True, capture_output=True, text=True)

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(source))
    monkeypatch.setenv("PUBLICATION_TEST_REMOTE_URL", str(bare))
    monkeypatch.setenv("PUBLICATION_MOCK_PRS", "false")
    monkeypatch.setenv("AUTH_RATE_LIMIT_ATTEMPTS", "1000")
    monkeypatch.setenv("SESSION_SECRET", "stage6-session-marker-not-a-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "stage6-llm-marker")
    get_settings.cache_clear()
    config_module.settings = get_settings()

    await init_db()
    await init_redis()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE outbox_events, agent_steps, agent_runs, execution_jobs, "
                "github_webhook_deliveries, repository_connections, github_installations, "
                "github_accounts, auth_sessions, agent_sessions, users RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()

    app = create_app()
    pr_requests: list[dict] = []

    class GitHubStub:
        async def find_pull_request(self, *_args, **_kwargs):
            return None

        async def create_pull_request(self, *_args, **kwargs):
            pr_requests.append(kwargs)
            return {"id": 101, "number": 7, "html_url": "https://github.test/pr/7"}

    monkeypatch.setattr("src.publication.GitHubClient", GitHubStub)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            register = await client.post(
                "/api/auth/register",
                json={"email": f"stage6-{uuid4().hex[:8]}@example.com", "password": "password123", "display_name": "Stage 6"},
                headers={"X-Request-ID": "stage6-register"},
            )
            assert register.status_code == 201
            user_id = register.json()["user"]["id"]
            await client.post("/api/auth/logout")
            login = await client.post(
                "/api/auth/login",
                json={"email": register.json()["user"]["email"], "password": "password123"},
                headers={"X-Request-ID": "stage6-login"},
            )
            assert login.status_code == 200

            async with factory() as session:
                user = await session.get(User, user_id)
                assert user is not None
                installation = GitHubInstallation(
                    user_id=user.id,
                    github_installation_id=7001,
                    account_login="fixture",
                    account_type="User",
                    account_id=7001,
                    repository_selection="all",
                )
                session.add(installation)
                await session.flush()
                connection = RepositoryConnection(
                    user_id=user.id,
                    installation_id=installation.id,
                    github_repository_id=7001,
                    owner="fixture",
                    name="sample-repo",
                    full_name="fixture/sample-repo",
                    default_branch="main",
                    private=False,
                    html_url="https://github.test/fixture/sample-repo",
                    is_active=True,
                )
                session.add(connection)
                await session.commit()
                connection_id = str(connection.id)

            create = await client.post(
                "/api/agent-runs",
                json={"repository_connection_id": connection_id, "task": "Update the sample repository."},
                headers={"X-Request-ID": "stage6-create-run"},
            )
            assert create.status_code == 201
            run_id = create.json()["id"]
            # Preserve the authenticated session when the API client is
            # recreated after the worker-side processing steps.
            auth_cookies = dict(client.cookies)

        async with factory() as session:
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.event_type == "agent_run.requested", OutboxEvent.aggregate_id == run_id)
            )
            assert event is not None
            correlation = event.payload["workflow_correlation_id"]
            assert correlation
            assert event.payload["request_id"] == "stage6-create-run"

        provider = DockerSandboxProvider()
        await process_outbox_event(event.id, provider, llm=FakeLLMProvider())
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.status == AgentRunStatus.awaiting_approval
            assert run.validation and run.validation["ok"] is True
            assert run.publication_artifact_hash
            assert str(run.workflow_correlation_id) == correlation
            approval = {
                "artifact_hash": run.publication_artifact_hash,
                "artifact_version": run.publication_artifact_version,
                "base_commit_sha": run.base_commit_sha,
            }

        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies
        ) as client:
            approved = await client.post(f"/api/agent-runs/{run_id}/approve", json=approval)
            assert approved.status_code == 200

        async with factory() as session:
            publication_event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == PUBLICATION_REQUESTED,
                    OutboxEvent.aggregate_id == run_id,
                )
            )
            assert publication_event is not None
            assert publication_event.payload["workflow_correlation_id"] == correlation
            assert publication_event.payload.get("request_id")
        await process_outbox_event(publication_event.id, provider)

        async with factory() as session:
            published = await session.get(AgentRun, run_id)
            delivery = await session.get(OutboxEvent, publication_event.id)
            assert published is not None and delivery is not None
            assert published.status == AgentRunStatus.succeeded
            assert published.publication_artifact_hash == published.approval_artifact_hash
            assert delivery.status == "processed"
            assert published.commit_sha and published.branch_name
            branch_ref = _git(bare, "rev-parse", f"refs/heads/{published.branch_name}")
            assert branch_ref == published.commit_sha
            commit_count = int(_git(bare, "rev-list", "--count", f"{base_sha}..{published.branch_name}"))
            assert commit_count == 1
            assert len(pr_requests) == 1

        assert metrics.snapshot().get("agentdock_publication_success_total", 0) >= 1
        assert "stage6-session-marker-not-a-secret" not in caplog.text
        assert "stage6-llm-marker" not in caplog.text
    finally:
        await close_redis()
        await close_db()
        get_settings.cache_clear()
