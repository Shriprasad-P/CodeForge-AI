from __future__ import annotations

import os
import tempfile
import asyncio
import threading
from pathlib import Path

import pytest

docker = pytest.importorskip("docker")
# ruff: noqa: E402

from sandbox_sdk import SandboxSpec
from sandbox_sdk.docker_provider import DEFAULT_SANDBOX_ENV, DockerSandboxProvider, LABEL_SANDBOX
from src.agent.tools import AgentTools


def _docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker daemon unavailable")

IMAGE = os.environ.get("SANDBOX_IMAGE", "agentdock-sandbox:local")


@pytest.fixture(scope="module")
def image_ready() -> str:
    client = docker.from_env()
    try:
        client.images.get(IMAGE)
    except docker.errors.ImageNotFound:
        root = Path(__file__).resolve().parents[3]
        dockerfile = root / "infrastructure" / "sandbox" / "Dockerfile"
        client.images.build(path=str(dockerfile.parent), tag=IMAGE)
    return IMAGE


@pytest.fixture
def provider(image_ready: str) -> DockerSandboxProvider:
    return DockerSandboxProvider()


def test_sandbox_isolation_and_cleanup(provider: DockerSandboxProvider, image_ready: str) -> None:
    execution_id = "test-isolation"
    sandbox_id = provider.create(
        SandboxSpec(
            image=image_ready,
            execution_id=execution_id,
            memory_limit="256m",
            nano_cpus=500_000_000,
            pids_limit=64,
            network_disabled=True,
        )
    )
    try:
        who = provider.exec(
            sandbox_id,
            ["whoami"],
            timeout_seconds=30,
            max_output_bytes=4096,
        )
        assert who.exit_code == 0
        assert who.stdout.decode().strip() != "root"

        pwd = provider.exec(sandbox_id, ["pwd"], timeout_seconds=30, max_output_bytes=4096)
        assert pwd.stdout.decode().strip() == "/workspace"

        for cmd in (["python", "--version"], ["node", "--version"], ["git", "--version"]):
            result = provider.exec(sandbox_id, cmd, timeout_seconds=30, max_output_bytes=4096)
            assert result.exit_code == 0, cmd

        env = provider.exec(sandbox_id, ["env"], timeout_seconds=30, max_output_bytes=65536)
        text = env.stdout.decode()
        for secret in (
            "DATABASE_URL",
            "REDIS_URL",
            "SESSION_SECRET",
            "GITHUB_APP_PRIVATE_KEY",
            "GITHUB_WEBHOOK_SECRET",
        ):
            assert secret not in text
        assert "AGENTDOCK_SANDBOX=true" in text

        sock = provider.exec(
            sandbox_id,
            ["python", "-c", "import os; print(os.path.exists('/var/run/docker.sock'))"],
            timeout_seconds=30,
            max_output_bytes=4096,
        )
        assert sock.stdout.decode().strip() == "False"
    finally:
        provider.destroy(sandbox_id)
        provider.destroy_labeled(execution_id=execution_id)

    client = docker.from_env()
    leftover = client.containers.list(all=True, filters={"label": [f"{LABEL_SANDBOX}=true", f"agentdock.execution_id={execution_id}"]})
    assert leftover == []


def test_sandbox_timeout(provider: DockerSandboxProvider, image_ready: str) -> None:
    execution_id = "test-timeout"
    sandbox_id = provider.create(
        SandboxSpec(image=image_ready, execution_id=execution_id, memory_limit="256m", pids_limit=64)
    )
    try:
        result = provider.exec(
            sandbox_id,
            ["python", "-c", "import time; time.sleep(30)"],
            timeout_seconds=2,
            max_output_bytes=1024,
        )
        assert result.timed_out is True
        assert result.exit_code == 124
    finally:
        provider.destroy(sandbox_id)
        provider.destroy_labeled(execution_id=execution_id)


def test_sandbox_cancellation_interrupts_command(provider: DockerSandboxProvider, image_ready: str) -> None:
    execution_id = "test-cancellation"
    sandbox_id = provider.create(SandboxSpec(image=image_ready, execution_id=execution_id, memory_limit="256m", pids_limit=64))
    cancel = threading.Event()
    timer = threading.Timer(0.4, cancel.set)
    timer.start()
    try:
        result = provider.exec(
            sandbox_id,
            ["python", "-c", "import time; time.sleep(30)"],
            timeout_seconds=30,
            max_output_bytes=1024,
            cancel_event=cancel,
        )
        assert result.cancelled is True
        assert result.exit_code == 130
        assert result.timed_out is False
    finally:
        timer.cancel()
        provider.destroy(sandbox_id)
        provider.destroy_labeled(execution_id=execution_id)


def test_async_tools_do_not_block_other_sandbox_work(provider: DockerSandboxProvider, image_ready: str) -> None:
    first = provider.create(SandboxSpec(image=image_ready, execution_id="test-async-a", memory_limit="256m", pids_limit=64))
    second = provider.create(SandboxSpec(image=image_ready, execution_id="test-async-b", memory_limit="256m", pids_limit=64))

    async def run() -> tuple[object, object, float]:
        import time

        start = time.monotonic()
        a = AgentTools(provider, first)
        b = AgentTools(provider, second)
        long_task = asyncio.create_task(a.run_command_async(["python", "-c", "import time; time.sleep(1)"], timeout=5))
        await asyncio.sleep(0.05)
        short = await b.run_command_async(["python", "-c", "print('ready')"], timeout=5)
        elapsed = time.monotonic() - start
        long_result = await long_task
        return long_result, short, elapsed

    try:
        long_result, short_result, elapsed = asyncio.run(run())
        assert long_result.ok is True
        assert short_result.ok is True
        assert "ready" in short_result.summary
        # The short command completed while the first command was still running.
        assert elapsed < 0.8
    finally:
        provider.destroy(first)
        provider.destroy(second)
        provider.destroy_labeled(execution_id="test-async-a")
        provider.destroy_labeled(execution_id="test-async-b")


def test_fixture_checkout_and_exec(provider: DockerSandboxProvider, image_ready: str) -> None:
    from src.checkout import assert_remote_sanitized, prepare_fixture_checkout

    root = Path(__file__).resolve().parents[3]
    fixture = root / "fixtures" / "sample-repo"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "repo"
        prepare_fixture_checkout(fixture, dest)
        # Initialize git remote to verify sanitizer path
        os.system(f"git -C {dest} init >/dev/null 2>&1")
        os.system(f"git -C {dest} remote add origin https://x-access-token:sekret@github.com/o/r.git >/dev/null 2>&1")
        from src.checkout import _rewrite_remotes

        _rewrite_remotes(dest, "https://github.com/o/r.git")
        assert_remote_sanitized(dest)

        execution_id = "test-fixture-exec"
        sandbox_id = provider.create(
            SandboxSpec(image=image_ready, execution_id=execution_id, memory_limit="256m", pids_limit=64)
        )
        try:
            provider.put_directory(sandbox_id, str(dest), "/workspace")
            result = provider.exec(
                sandbox_id,
                ["python", "hello.py"],
                timeout_seconds=30,
                max_output_bytes=4096,
            )
            assert result.exit_code == 0
            assert "hello from fixture" in result.stdout.decode()
        finally:
            provider.destroy(sandbox_id)
            provider.destroy_labeled(execution_id=execution_id)


def test_incremental_command_output_streaming(provider: DockerSandboxProvider, image_ready: str) -> None:
    execution_id = "test-stream-chunks"
    sandbox_id = provider.create(
        SandboxSpec(image=image_ready, execution_id=execution_id, memory_limit="256m", pids_limit=64)
    )
    chunks: list[tuple[str, str, bool]] = []
    try:
        result = provider.exec(
            sandbox_id,
            [
                "python",
                "-c",
                "import sys,time\n"
                "for i in range(3):\n"
                "  print(f'line-{i}', flush=True)\n"
                "  sys.stderr.write(f'err-{i}\\n'); sys.stderr.flush()\n"
                "  time.sleep(0.2)\n",
            ],
            timeout_seconds=30,
            max_output_bytes=65536,
            on_chunk=lambda stream, text, truncated: chunks.append((stream, text, truncated)),
        )
        assert result.exit_code == 0
        assert result.timed_out is False
        assert len(chunks) >= 3
        joined = "".join(text for _, text, _ in chunks)
        assert "line-0" in joined
        assert "line-2" in joined
        assert "err-0" in joined
        # Order: first line-0 before line-2
        assert joined.index("line-0") < joined.index("line-2")
    finally:
        provider.destroy(sandbox_id)
        provider.destroy_labeled(execution_id=execution_id)
        leftover = docker.from_env().containers.list(
            all=True, filters={"label": [f"{LABEL_SANDBOX}=true", f"agentdock.execution_id={execution_id}"]}
        )
        assert leftover == []


def test_default_env_allowlist() -> None:
    assert "DATABASE_URL" not in DEFAULT_SANDBOX_ENV
    assert DEFAULT_SANDBOX_ENV["AGENTDOCK_SANDBOX"] == "true"
