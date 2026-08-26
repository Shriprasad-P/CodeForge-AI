from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from sandbox_sdk.docker_provider import DockerSandboxProvider
from src.agent.paths import PathEscapeError, safe_rel_path, workspace_path
from src.runtime import run_blocking

ALLOWED_COMMAND_PREFIXES = {
    "python",
    "pytest",
    "npm",
    "npx",
    "node",
    "pnpm",
    "yarn",
    "git",
    "rg",
    "ls",
    "cat",
    "head",
    "wc",
    "make",
    "go",
    "cargo",
}


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] | None = None
    truncated: bool = False


class AgentTools:
    def __init__(self, provider: DockerSandboxProvider, sandbox_id: str, on_chunk=None, cancel_event=None) -> None:
        self.provider = provider
        self.sandbox_id = sandbox_id
        self.settings = get_settings()
        self.on_chunk = on_chunk
        self.cancel_event = cancel_event

    def _exec(
        self,
        command: list[str],
        *,
        workdir: str = "/workspace",
        timeout: float | None = None,
        cancel_event=None,
    ) -> ToolResult:
        cancel_event = cancel_event if cancel_event is not None else self.cancel_event
        result = self.provider.exec(
            self.sandbox_id,
            command,
            workdir=workdir,
            timeout_seconds=timeout or float(self.settings.sandbox_timeout_seconds),
            max_output_bytes=self.settings.sandbox_max_output_bytes,
            on_chunk=self.on_chunk,
            cancel_event=cancel_event,
        )
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        text = (out + ("\n" + err if err else "")).strip()
        limit = self.settings.agent_max_tool_output_chars
        truncated = len(text) > limit or result.truncated
        if len(text) > limit:
            text = text[:limit]
        return ToolResult(
            ok=result.exit_code == 0 and not result.timed_out,
            summary=text or f"exit {result.exit_code}",
            data={
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "cancelled": getattr(result, "cancelled", False),
                "stdout": out[:limit],
                "stderr": err[:limit],
            },
            truncated=truncated,
        )

    def list_files(self, path: str = ".", max_entries: int = 200, *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        try:
            rel = safe_rel_path(path)
            target = workspace_path(path)
        except PathEscapeError as exc:
            return ToolResult(ok=False, summary=str(exc))
        max_entries = max(1, min(int(max_entries or 200), 500))
        py = f"""
import os, json
root = {target!r}
max_entries = {max_entries}
out = []
for dp, dns, fns in os.walk(root):
    dns[:] = [d for d in dns if d not in {{'.git', 'node_modules', '__pycache__', '.venv'}}]
    for fn in fns:
        p = os.path.join(dp, fn)
        out.append(os.path.relpath(p, '/workspace'))
        if len(out) >= max_entries:
            break
    if len(out) >= max_entries:
        break
print(json.dumps({{"path": {rel!r}, "files": out, "truncated": len(out) >= max_entries}}))
"""
        return self._exec(["python", "-c", py], timeout=timeout, cancel_event=cancel_event)

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None, *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        try:
            target = workspace_path(path)
            safe_rel_path(path)
        except PathEscapeError as exc:
            return ToolResult(ok=False, summary=str(exc))
        max_bytes = self.settings.agent_max_file_read_bytes
        py = (
            "from pathlib import Path\n"
            f"p = Path({target!r})\n"
            "if not p.is_file():\n"
            "    raise SystemExit('file not found')\n"
            "data = p.read_bytes()\n"
            "if b'\\x00' in data[:4096]:\n"
            "    raise SystemExit('binary file')\n"
            f"raw = data[:{max_bytes}].decode('utf-8', errors='replace')\n"
            "lines = raw.splitlines()\n"
            f"start = {int(start_line) if start_line else 1}\n"
            f"end = {int(end_line) if end_line else 10**9}\n"
            "start = max(1, start)\n"
            "chunk = lines[start-1:end]\n"
            "print('\\n'.join(f'{i+start}|{line}' for i, line in enumerate(chunk)))\n"
            f"if len(data) > {max_bytes}:\n"
            "    print('... truncated')\n"
        )
        return self._exec(["python", "-c", py], timeout=timeout, cancel_event=cancel_event)

    def search_code(self, query: str, path: str = ".", *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        if not query or len(query) > 200:
            return ToolResult(ok=False, summary="invalid query")
        try:
            target = workspace_path(path)
        except PathEscapeError as exc:
            return ToolResult(ok=False, summary=str(exc))
        max_results = self.settings.agent_max_search_results
        # Prefer rg if present, else python fallback
        result = self._exec(
            ["rg", "-n", "--hidden", "--glob", "!.git", "-m", str(max_results), query, target],
            timeout=min(timeout, 30) if timeout is not None else 30,
            cancel_event=cancel_event,
        )
        if "No such file" in result.summary or result.data and result.data.get("exit_code") == 127:
            py = f"""
import os,re
root={target!r}; q=re.compile({query!r})
n=0
for dp,dns,fns in os.walk(root):
  dns[:]=[d for d in dns if d!='.git']
  for fn in fns:
    p=os.path.join(dp,fn)
    try:
      text=open(p,'r',encoding='utf-8',errors='ignore').read().splitlines()
    except Exception:
      continue
    for i,line in enumerate(text,1):
      if q.search(line):
        print(f'{{os.path.relpath(p,\"/workspace\")}}:{{i}}:{{line[:200]}}'); n+=1
        if n>={max_results}: raise SystemExit
"""
            return self._exec(["python", "-c", py], timeout=min(timeout, 30) if timeout is not None else 30, cancel_event=cancel_event)
        return result

    def write_file(self, path: str, content: str, *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        try:
            rel = safe_rel_path(path)
            if rel == ".":
                return ToolResult(ok=False, summary="path must be a file")
            target = workspace_path(path)
        except PathEscapeError as exc:
            return ToolResult(ok=False, summary=str(exc))
        if len(content.encode("utf-8")) > self.settings.agent_max_file_read_bytes * 2:
            return ToolResult(ok=False, summary="content too large")
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        py = (
            "import base64, pathlib\n"
            f"p = pathlib.Path({target!r})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"p.write_bytes(base64.b64decode({b64!r}))\n"
            "print('wrote', p)\n"
        )
        return self._exec(["python", "-c", py], timeout=timeout, cancel_event=cancel_event)

    def apply_patch(self, patch: str, *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        if not patch or len(patch) > self.settings.agent_max_diff_chars:
            return ToolResult(ok=False, summary="invalid patch")
        if "/.git/" in patch or "\n.git/" in patch:
            return ToolResult(ok=False, summary="patch must not touch .git")
        b64 = base64.b64encode(patch.encode("utf-8")).decode("ascii")
        py = (
            "import base64, pathlib, subprocess, tempfile\n"
            f"data = base64.b64decode({b64!r})\n"
            "path = pathlib.Path('/tmp/agentdock.patch')\n"
            "path.write_bytes(data)\n"
            "r = subprocess.run(['git','apply','--whitespace=nowarn',str(path)], cwd='/workspace', capture_output=True, text=True)\n"
            "print(r.stdout)\n"
            "print(r.stderr)\n"
            "raise SystemExit(r.returncode)\n"
        )
        return self._exec(["python", "-c", py], timeout=timeout, cancel_event=cancel_event)

    def run_command(
        self,
        command: list[str],
        working_directory: str | None = None,
        *,
        timeout: float | None = None,
        cancel_event=None,
    ) -> ToolResult:
        if not command or not isinstance(command, list):
            return ToolResult(ok=False, summary="command must be argv list")
        if len(command) > self.settings.execution_max_command_args:
            return ToolResult(ok=False, summary="too many args")
        for arg in command:
            if not isinstance(arg, str) or not arg or len(arg) > self.settings.execution_max_arg_length:
                return ToolResult(ok=False, summary="invalid arg")
            if "\x00" in arg:
                return ToolResult(ok=False, summary="invalid arg")
        exe = command[0]
        base = exe.rsplit("/", 1)[-1]
        if base not in ALLOWED_COMMAND_PREFIXES and exe not in ALLOWED_COMMAND_PREFIXES:
            return ToolResult(ok=False, summary=f"command not allowed: {base}")
        # Block dangerous git mutations
        if base == "git" and len(command) > 1 and command[1] in {"push", "remote", "config", "credential"}:
            return ToolResult(ok=False, summary="git mutation not allowed")
        workdir = "/workspace"
        if working_directory:
            try:
                workdir = workspace_path(working_directory)
            except PathEscapeError as exc:
                return ToolResult(ok=False, summary=str(exc))
        return self._exec(list(command), workdir=workdir, timeout=timeout, cancel_event=cancel_event)

    def git_status(self, *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        return self._exec(["git", "status", "--short"], timeout=timeout, cancel_event=cancel_event)

    def git_diff(self, stat: bool = False, *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        cmd = ["git", "diff", "--stat"] if stat else ["git", "diff"]
        return self._exec(cmd, timeout=timeout, cancel_event=cancel_event)

    def dispatch(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        try:
            if name == "list_files":
                return self.list_files(arguments.get("path", "."), arguments.get("max_entries", 200), timeout=timeout, cancel_event=cancel_event)
            if name == "read_file":
                return self.read_file(arguments["path"], arguments.get("start_line"), arguments.get("end_line"), timeout=timeout, cancel_event=cancel_event)
            if name == "search_code":
                return self.search_code(arguments["query"], arguments.get("path", "."), timeout=timeout, cancel_event=cancel_event)
            if name == "write_file":
                return self.write_file(arguments["path"], arguments.get("content", ""), timeout=timeout, cancel_event=cancel_event)
            if name == "apply_patch":
                return self.apply_patch(arguments.get("patch", ""), timeout=timeout, cancel_event=cancel_event)
            if name == "run_command":
                return self.run_command(
                    arguments.get("command") or [],
                    arguments.get("working_directory"),
                    timeout=timeout,
                    cancel_event=cancel_event,
                )
            if name == "git_status":
                return self.git_status(timeout=timeout, cancel_event=cancel_event)
            if name == "git_diff":
                return self.git_diff(bool(arguments.get("stat")), timeout=timeout, cancel_event=cancel_event)
            return ToolResult(ok=False, summary=f"unknown tool: {name}")
        except KeyError as exc:
            return ToolResult(ok=False, summary=f"missing argument: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, summary=f"tool error: {exc}")

    async def run_command_async(
        self,
        command: list[str],
        working_directory: str | None = None,
        *,
        timeout: float | None = None,
        cancel_event=None,
    ) -> ToolResult:
        """Run the blocking Docker SDK call off the worker event loop."""
        return await run_blocking(
            self.run_command,
            command,
            working_directory,
            timeout=timeout,
            cancel_event=cancel_event,
        )

    async def dispatch_async(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None, cancel_event=None) -> ToolResult:
        """Dispatch a tool without blocking the asyncio worker loop."""
        return await run_blocking(self.dispatch, name, arguments, timeout=timeout, cancel_event=cancel_event)
