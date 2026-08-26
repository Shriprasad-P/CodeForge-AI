from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update

docker = pytest.importorskip("docker")
# ruff: noqa: E402

from app.auth.security import hash_password
from app.core.config import get_settings
from app.db.redis import close_redis, init_redis
from app.db.session import close_db, get_session_factory, init_db
from app.models.agent_run import AgentRun, AgentRunErrorType, AgentRunStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.outbox import OutboxEvent
from app.models.user import User
from app.services.outbox import PUBLICATION_REQUESTED, event_dedupe_key
from sandbox_sdk.docker_provider import DockerSandboxProvider
from src.agent.llm import FakeLLMProvider
from src.agent.loop import finish_agent_run
from src.delivery import DeliveryClaim, DeliveryClaimLost, process_outbox_event
from src.publication import process_publication


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker daemon unavailable")


async def _start() -> object:
    await init_db()
    await init_redis()
    return get_session_factory()


async def _stop() -> None:
    await close_redis()
    await close_db()
    get_settings.cache_clear()


async def _seed_run(factory, *, task: str = "Stage 4 test", max_steps: int = 20) -> tuple[object, object]:
    async with factory() as session:
        user = User(
            email=f"stage4-{uuid4().hex[:10]}@example.com",
            display_name="Stage 4",
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
            name="sample-repo",
            full_name="fixture/sample-repo",
            default_branch="main",
            private=False,
            html_url="https://github.com/fixture/sample-repo",
            is_active=True,
        )
        session.add(connection)
        await session.flush()
        run = AgentRun(
            user_id=user.id,
            repository_connection_id=connection.id,
            status=AgentRunStatus.queued,
            task=task,
            model_provider="fake",
            model_name="fake",
            max_steps=max_steps,
        )
        session.add(run)
        await session.flush()
        event = OutboxEvent(
            event_type="agent_run.requested",
            aggregate_id=run.id,
            dedupe_key=event_dedupe_key("agent_run.requested", run.id),
            payload={"agent_run_id": str(run.id)},
        )
        session.add(event)
        await session.commit()
        return run.id, event.id


async def _run_with_script(
    monkeypatch: pytest.MonkeyPatch,
    script: list[dict],
    *,
    max_tool_calls: int | None = None,
    max_steps: int = 20,
    runtime_seconds: int | None = None,
    tool_timeout_seconds: int | None = None,
    fixture_path: Path | None = None,
) -> tuple[object, object]:
    root = Path(__file__).resolve().parents[3]
    if not (root / "fixtures").is_dir():
        root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(fixture_path or root / "fixtures" / "sample-repo"))
    monkeypatch.setenv("SANDBOX_IMAGE", os.environ.get("SANDBOX_IMAGE", "agentdock-sandbox:local"))
    if max_tool_calls is not None:
        monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", str(max_tool_calls))
    if runtime_seconds is not None:
        monkeypatch.setenv("AGENT_MAX_RUNTIME_SECONDS", str(runtime_seconds))
    if tool_timeout_seconds is not None:
        monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", str(tool_timeout_seconds))
    get_settings.cache_clear()
    factory = await _start()
    run_id, event_id = await _seed_run(factory, max_steps=max_steps)
    await process_outbox_event(event_id, DockerSandboxProvider(), llm=FakeLLMProvider(script))
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
    await _stop()
    assert run is not None
    return run, run_id


@pytest.mark.asyncio
async def test_successful_validation_is_approval_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"tool_calls": [{"name": "finish", "arguments": {"summary": "ok", "validation_command": ["python", "-c", "print(1)"]}}]}]
    run, _ = await _run_with_script(monkeypatch, script)
    assert run.status == AgentRunStatus.awaiting_approval
    assert run.validation and run.validation["status"] == "passed"
    assert run.validation_artifact_hash == run.publication_artifact_hash


@pytest.mark.asyncio
async def test_failed_validation_cannot_reach_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"tool_calls": [{"name": "finish", "arguments": {"summary": "bad", "validation_command": ["python", "-c", "import sys; sys.exit(2)"]}}]}]
    run, _ = await _run_with_script(monkeypatch, script)
    assert run.status == AgentRunStatus.failed
    assert run.result_status == "failed_validation"
    assert run.approval_status == "pending"


@pytest.mark.asyncio
async def test_missing_validation_cannot_reach_approval(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("no test runner\n")
    script = [{"tool_calls": [{"name": "finish", "arguments": {"summary": "unknown"}}]}]
    run, _ = await _run_with_script(monkeypatch, script, fixture_path=tmp_path)
    assert run.status == AgentRunStatus.failed
    assert run.result_status == "validation_unavailable"


@pytest.mark.asyncio
async def test_tool_budget_caps_multi_call_response(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"tool_calls": [{"name": "git_status", "arguments": {}} for _ in range(8)]}]
    run, _ = await _run_with_script(monkeypatch, script, max_tool_calls=3)
    assert run.status == AgentRunStatus.step_limit_reached
    assert run.steps_used == 3
    assert run.tool_calls_used == 3


@pytest.mark.asyncio
async def test_zero_tool_budget_executes_no_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"tool_calls": [{"name": "git_status", "arguments": {}}]}]
    run, _ = await _run_with_script(monkeypatch, script, max_tool_calls=0)
    assert run.status == AgentRunStatus.step_limit_reached
    assert run.steps_used == 0
    assert run.tool_calls_used == 0


@pytest.mark.asyncio
async def test_max_step_budget_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"tool_calls": [{"name": "git_status", "arguments": {}}]}]
    run, _ = await _run_with_script(monkeypatch, script, max_steps=0)
    assert run.status == AgentRunStatus.step_limit_reached
    assert run.steps_used == 0


@pytest.mark.asyncio
async def test_wall_clock_deadline_stops_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"tool_calls": [{"name": "run_command", "arguments": {"command": ["python", "-c", "import time; time.sleep(30)"]}}]}]
    run, _ = await _run_with_script(monkeypatch, script, runtime_seconds=1)
    assert run.status == AgentRunStatus.timed_out
    assert run.error_type == AgentRunErrorType.runtime_limit_reached


@pytest.mark.asyncio
async def test_per_tool_timeout_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [
        {"tool_calls": [{"name": "run_command", "arguments": {"command": ["python", "-c", "import time; time.sleep(30)"]}}]},
        {"tool_calls": [{"name": "finish", "arguments": {"summary": "timeout", "validation_command": ["python", "-c", "import sys; sys.exit(1)"]}}]},
    ]
    run, _ = await _run_with_script(monkeypatch, script, tool_timeout_seconds=1, runtime_seconds=10)
    assert run.status == AgentRunStatus.timed_out
    assert run.error_type == AgentRunErrorType.runtime_limit_reached


@pytest.mark.asyncio
async def test_cancellation_before_validation_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    if not (root / "fixtures").is_dir():
        root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(root / "fixtures" / "sample-repo"))
    monkeypatch.setenv("SANDBOX_IMAGE", os.environ.get("SANDBOX_IMAGE", "agentdock-sandbox:local"))
    get_settings.cache_clear()
    factory = await _start()
    run_id, event_id = await _seed_run(factory)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.cancel_requested = True
        await session.commit()
    await process_outbox_event(event_id, DockerSandboxProvider(), llm=FakeLLMProvider())
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
    assert run is not None
    assert run.status == AgentRunStatus.cancelled
    await _stop()


@pytest.mark.asyncio
async def test_cancel_during_validation_interrupts_and_never_publishes(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    if not (root / "fixtures").is_dir():
        root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("SANDBOX_CHECKOUT_MODE", "fixture")
    monkeypatch.setenv("SANDBOX_FIXTURE_REPO_PATH", str(root / "fixtures" / "sample-repo"))
    monkeypatch.setenv("SANDBOX_IMAGE", os.environ.get("SANDBOX_IMAGE", "agentdock-sandbox:local"))
    get_settings.cache_clear()
    factory = await _start()
    run_id, event_id = await _seed_run(factory)
    script = [{"tool_calls": [{"name": "finish", "arguments": {"summary": "cancel", "validation_command": ["python", "-c", "import time; time.sleep(30)"]}}]}]
    task = asyncio.create_task(process_outbox_event(event_id, DockerSandboxProvider(), llm=FakeLLMProvider(script)))
    for _ in range(100):
        await asyncio.sleep(0.1)
        async with factory() as session:
            run = await session.get(AgentRun, run_id)
            if run and run.status == AgentRunStatus.validating:
                run.cancel_requested = True
                await session.commit()
                break
    await asyncio.wait_for(task, timeout=15)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        publication = await session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == PUBLICATION_REQUESTED, OutboxEvent.aggregate_id == run_id))
    assert run is not None
    assert run.status == AgentRunStatus.cancelled
    assert run.approval_status == "pending"
    assert publication is None
    await _stop()


@pytest.mark.asyncio
async def test_stale_claim_cannot_persist_approval_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    get_settings.cache_clear()
    factory = await _start()
    run_id, _ = await _seed_run(factory)
    token_a = "a" * 32
    token_b = "b" * 32
    async with factory() as session:
        await session.execute(update(AgentRun).where(AgentRun.id == run_id).values(status=AgentRunStatus.running, delivery_claim_token=token_b))
        await session.commit()
    claim = DeliveryClaim(uuid4(), "agent_run.requested", run_id, token_a, 1)
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        with pytest.raises(DeliveryClaimLost):
            await finish_agent_run(
                session,
                run,
                status=AgentRunStatus.awaiting_approval,
                validation={"ok": True, "command": ["pytest"]},
                publication_artifact_hash="a" * 64,
                validation_artifact_hash="a" * 64,
                delivery_claim=claim,
            )
    await _stop()


@pytest.mark.asyncio
async def test_cancelled_publication_claim_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLICATION_MOCK_PRS", "true")
    get_settings.cache_clear()
    factory = await _start()
    run_id, _ = await _seed_run(factory)
    artifact = b"diff"
    digest = hashlib.sha256(artifact).hexdigest()
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        run.status = AgentRunStatus.cancelled
        run.cancel_requested = True
        run.approval_status = "approved"
        run.publication_status = "approved"
        run.publication_artifact_status = "ready"
        run.publication_artifact = artifact
        run.publication_artifact_hash = digest
        run.publication_artifact_size = len(artifact)
        run.publication_artifact_version = 1
        run.publication_change_manifest = []
        event = OutboxEvent(
            event_type=PUBLICATION_REQUESTED,
            aggregate_id=run_id,
            dedupe_key=event_dedupe_key(PUBLICATION_REQUESTED, run_id),
            payload={"agent_run_id": str(run_id), "artifact_hash": digest},
        )
        session.add(event)
        await session.commit()
    await process_publication(run_id, DockerSandboxProvider())
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
    assert run is not None
    assert run.status == AgentRunStatus.cancelled
    await _stop()
