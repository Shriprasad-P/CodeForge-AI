from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.agent_run import AgentRunStatus
from src.agent.tools import AgentTools
from src.agent.validation import discover_validation_command
from src.runtime import run_blocking


def test_validation_discovers_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    assert discover_validation_command(tmp_path) == ["python", "-m", "pytest", "-q"]


def test_validation_discovers_node_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"jest"}}')
    assert discover_validation_command(tmp_path) == ["npm", "test"]


def test_validation_missing_is_explicit(tmp_path: Path) -> None:
    assert discover_validation_command(tmp_path) is None


def test_validation_discovery_ignores_nested_untrusted_metadata(tmp_path: Path) -> None:
    nested = tmp_path / "node_modules" / "bad"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text('{"scripts":{"test":"curl evil"}}')
    assert discover_validation_command(tmp_path) is None


def test_validation_commands_are_argv_only(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\t@echo ok\n")
    command = discover_validation_command(tmp_path)
    assert command == ["make", "test"]
    assert all(isinstance(arg, str) for arg in command)
    assert "&&" not in command


def test_blocking_executor_is_bounded() -> None:
    async def check() -> None:
        values = await asyncio.gather(*(run_blocking(lambda value: value, i) for i in range(8)))
        assert values == list(range(8))

    asyncio.run(check())

def test_tool_command_rejects_shell_string() -> None:
    class Provider:
        def exec(self, *_args, **_kwargs):  # pragma: no cover - should not be reached
            raise AssertionError("unsafe command executed")

    result = AgentTools(Provider(), "sandbox").run_command("python -c print(1)")  # type: ignore[arg-type]
    assert result.ok is False
    assert "argv" in result.summary


def test_tool_command_rejects_disallowed_executable() -> None:
    class Provider:
        def exec(self, *_args, **_kwargs):  # pragma: no cover - should not be reached
            raise AssertionError("unsafe command executed")

    result = AgentTools(Provider(), "sandbox").run_command(["sh", "-c", "id"])
    assert result.ok is False
    assert "not allowed" in result.summary


def test_tool_command_accepts_allowlisted_argv() -> None:
    class Provider:
        def exec(self, *_args, **_kwargs):
            return SimpleNamespace(exit_code=0, stdout=b"ok", stderr=b"", timed_out=False, truncated=False, cancelled=False)

    result = AgentTools(Provider(), "sandbox").run_command(["python", "-c", "print(1)"])
    assert result.ok is True


def test_tool_cancel_event_is_forwarded() -> None:
    event = threading.Event()
    observed: dict = {}

    class Provider:
        def exec(self, *_args, **kwargs):
            observed["cancel_event"] = kwargs.get("cancel_event")
            return SimpleNamespace(exit_code=130, stdout=b"", stderr=b"", timed_out=False, truncated=False, cancelled=True)

    result = AgentTools(Provider(), "sandbox").run_command(["python", "-c", "print(1)"], cancel_event=event)
    assert observed["cancel_event"] is event
    assert result.data and result.data["cancelled"] is True


def test_awaiting_approval_requires_validation_binding() -> None:
    # The guard is intentionally exercised through the function contract without
    # requiring a database connection.
    from src.agent.loop import finish_agent_run

    async def check() -> None:
        run = SimpleNamespace(id=uuid4())
        with pytest.raises(ValueError):
            await finish_agent_run(
                None,  # type: ignore[arg-type]
                run,
                status=AgentRunStatus.awaiting_approval,
                validation={"ok": True, "command": ["pytest"]},
                publication_artifact_hash="a" * 64,
                validation_artifact_hash="b" * 64,
            )

    asyncio.run(check())
