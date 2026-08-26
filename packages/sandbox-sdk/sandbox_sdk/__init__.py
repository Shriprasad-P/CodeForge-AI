"""Minimal sandbox provider interface + Docker implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    truncated: bool = False
    cancelled: bool = False


@dataclass
class SandboxSpec:
    image: str
    execution_id: str
    memory_limit: str = "512m"
    nano_cpus: int = 1_000_000_000  # 1 CPU
    pids_limit: int = 256
    network_disabled: bool = True
    workspace: str = "/workspace"
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


class SandboxProvider(Protocol):
    def create(self, spec: SandboxSpec) -> str:
        """Create a sandbox; return provider-native id."""

    def put_directory(self, sandbox_id: str, host_dir: str, container_path: str) -> None:
        """Copy a host directory into the sandbox."""

    def get_directory(self, sandbox_id: str, container_path: str, host_dir: str) -> None:
        """Copy a sandbox directory into a trusted worker-owned temporary directory."""

    def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        workdir: str | None = None,
        timeout_seconds: float,
        max_output_bytes: int,
        on_chunk=None,
        cancel_event=None,
    ) -> ExecResult:
        """Run argv (no shell) inside the sandbox."""

    def destroy(self, sandbox_id: str) -> None:
        """Force-remove the sandbox container."""

    def destroy_labeled(self, *, execution_id: str | None = None) -> int:
        """Remove AgentDock-labelled sandboxes; return count destroyed."""
