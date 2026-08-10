from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.security import hash_password
from app.core.config import get_settings
from app.db.redis import close_redis, init_redis
from app.db.session import close_db, get_session_factory, init_db
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.user import User
from sandbox_sdk.docker_provider import DockerSandboxProvider
from src.publication import process_publication


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_publication_commits_pushes_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "Fixture")
    _git(source, "config", "user.email", "fixture@example.com")
    (source / "README.md").write_text("before\n")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "base")
    base_sha = _git(source, "rev-parse", "HEAD")
    (source / "README.md").write_text("before\nafter\n")
    diff = _git(source, "diff")
    _git(source, "reset", "--hard", "HEAD")

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "--bare", str(source), str(bare)], check=True, capture_output=True, text=True)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLICATION_TEST_REMOTE_URL", str(bare))
    monkeypatch.setenv("PUBLICATION_MOCK_PRS", "true")
    get_settings.cache_clear()

    await init_db()
    await init_redis()
    factory = get_session_factory()
    async with factory() as session:
        user = User(
            email=f"publication-{uuid4().hex[:8]}@example.com",
            display_name="Publisher",
            password_hash=hash_password("password123"),
            auth_provider="password",
        )
        session.add(user)
        await session.flush()
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=int(uuid4().int % 10_000_000),
            account_login="fixture",
            account_type="User",
            account_id=int(uuid4().int % 10_000_000),
            repository_selection="all",
        )
        session.add(installation)
        await session.flush()
        connection = RepositoryConnection(
            user_id=user.id,
            installation_id=installation.id,
            github_repository_id=int(uuid4().int % 10_000_000),
            owner="fixture",
            name="publication",
            full_name="fixture/publication",
            default_branch="main",
            private=False,
            html_url="https://github.com/fixture/publication",
            is_active=True,
        )
        session.add(connection)
        await session.flush()
        run = AgentRun(
            user_id=user.id,
            repository_connection_id=connection.id,
            status=AgentRunStatus.awaiting_approval,
            task="Update the README.",
            model_provider="fake",
            model_name="fake",
            max_steps=20,
            summary="Update README",
            changed_files=["README.md"],
            diff_text=diff,
            diff_hash=hashlib.sha256(diff.encode()).hexdigest(),
            base_commit_sha=base_sha,
            approval_status="approved",
            publication_status="approved",
            validation=None,
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    await process_publication(run_id, DockerSandboxProvider())
    async with factory() as session:
        published = await session.get(AgentRun, run_id)
        assert published is not None
        assert published.status == AgentRunStatus.succeeded
        assert published.publication_status == "published"
        assert published.commit_sha
        assert published.github_pr_number == 1
        branch = published.branch_name

    refs = subprocess.run(
        ["git", "--git-dir", str(bare), "show-ref", "--verify", f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert published.commit_sha in refs

    # A duplicate queue delivery is a no-op after the durable published marker.
    await process_publication(run_id, DockerSandboxProvider())
    async with factory() as session:
        duplicate = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
        assert duplicate is not None
        assert duplicate.commit_sha == published.commit_sha
        assert duplicate.github_pr_number == 1

    await close_redis()
    await close_db()
    get_settings.cache_clear()
