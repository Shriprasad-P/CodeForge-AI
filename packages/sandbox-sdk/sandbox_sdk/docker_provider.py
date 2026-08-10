from __future__ import annotations

import io
import select
import socket
import tarfile
import time
from pathlib import Path

from sandbox_sdk import ExecResult, SandboxSpec

LABEL_SANDBOX = "agentdock.sandbox"
LABEL_EXECUTION = "agentdock.execution_id"

DEFAULT_SANDBOX_ENV = {
    "CI": "true",
    "AGENTDOCK_SANDBOX": "true",
    "HOME": "/workspace",
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
}

FORBIDDEN_ENV = frozenset(
    {
        "DATABASE_URL",
        "REDIS_URL",
        "SESSION_SECRET",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_CLIENT_SECRET",
        "GITHUB_WEBHOOK_SECRET",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DOCKER_HOST",
    }
)


def _demux_header(header: bytes) -> tuple[int, int]:
    """Docker stream header: 1 byte stream type + 3 pad + 4 byte size."""
    if len(header) < 8:
        return 1, 0
    stream = header[0]
    size = int.from_bytes(header[4:8], "big")
    return stream, size


class DockerSandboxProvider:
    """Ephemeral Docker sandboxes. Never mounts docker.sock into sandboxes."""

    def __init__(self, client=None) -> None:
        if client is None:
            import docker

            client = docker.from_env()
        self._client = client

    def create(self, spec: SandboxSpec) -> str:
        labels = {
            LABEL_SANDBOX: "true",
            LABEL_EXECUTION: spec.execution_id,
            **spec.labels,
        }
        env = {**DEFAULT_SANDBOX_ENV, **spec.env}
        for key in list(env):
            if key in FORBIDDEN_ENV or key.upper() in FORBIDDEN_ENV:
                env.pop(key, None)

        container = self._client.containers.create(
            image=spec.image,
            command=["sleep", "infinity"],
            user="10001:10001",
            working_dir=spec.workspace,
            environment=env,
            labels=labels,
            network_disabled=spec.network_disabled,
            mem_limit=spec.memory_limit,
            nano_cpus=spec.nano_cpus,
            pids_limit=spec.pids_limit,
            privileged=False,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            # ponytail: Docker put_archive fails on read-only root even with tmpfs /workspace
            # on Desktop; root stays root-owned so uid 10001 still cannot overwrite system paths.
            read_only=False,
            tmpfs={
                "/tmp": "rw,noexec,nosuid,size=64m",
            },
            detach=True,
        )
        container.start()
        return container.id

    def put_directory(self, sandbox_id: str, host_dir: str, container_path: str) -> None:
        """Copy host_dir into container_path (must be under writable tmpfs, e.g. /workspace)."""
        container = self._client.containers.get(sandbox_id)
        root = Path(host_dir)
        dest = container_path.rstrip("/") or "/workspace"
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root)
                arcname = str(rel)
                if path.is_dir() and not path.is_symlink():
                    info = tarfile.TarInfo(arcname)
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.uid = 10001
                    info.gid = 10001
                    tar.addfile(info)
                elif path.is_file():
                    info = tar.gettarinfo(str(path), arcname=arcname)
                    info.uid = 10001
                    info.gid = 10001
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
        buf.seek(0)
        ok = container.put_archive(dest, buf.getvalue())
        if not ok:
            raise RuntimeError("Failed to copy files into sandbox")

    def exec(
        self,
        sandbox_id: str,
        command: list[str],
        *,
        workdir: str | None = None,
        timeout_seconds: float,
        max_output_bytes: int,
        on_chunk=None,
    ) -> ExecResult:
        """Run command. Optional on_chunk(stream: 'stdout'|'stderr', text: str, truncated: bool)."""
        container = self._client.containers.get(sandbox_id)
        api = self._client.api
        exec_id = api.exec_create(
            container.id,
            cmd=command,
            workdir=workdir,
            user="10001:10001",
        )["Id"]
        sock_resp = api.exec_start(exec_id, tty=False, socket=True, demux=False)
        raw: socket.socket = sock_resp._sock  # noqa: SLF001

        stdout = bytearray()
        stderr = bytearray()
        truncated = False
        timed_out = False
        deadline = time.monotonic() + timeout_seconds
        pending = b""
        # Decode buffers so multi-byte UTF-8 across demux frames stays intact.
        stdout_decoder = __import__("codecs").getincrementaldecoder("utf-8")(errors="replace")
        stderr_decoder = __import__("codecs").getincrementaldecoder("utf-8")(errors="replace")
        chunk_limit = 512

        def _emit(stream_name: str, decoder, payload: bytes, force_flush: bool = False) -> None:
            if on_chunk is None:
                return
            text = decoder.decode(payload, final=force_flush)
            if not text and not force_flush:
                return
            # Bound browser-facing chunk size without dropping chars permanently —
            # caller already applied max_output_bytes; we only slice for WS.
            offset = 0
            while offset < len(text):
                piece = text[offset : offset + chunk_limit]
                offset += chunk_limit
                try:
                    on_chunk(stream_name, piece, False)
                except Exception:
                    pass

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                ready, _, _ = select.select([raw], [], [], min(remaining, 0.5))
                if not ready:
                    inspect = api.exec_inspect(exec_id)
                    if not inspect.get("Running", True):
                        break
                    continue
                chunk = raw.recv(4096)
                if not chunk:
                    break
                pending += chunk
                while len(pending) >= 8:
                    stream, size = _demux_header(pending[:8])
                    if len(pending) < 8 + size:
                        break
                    payload = pending[8 : 8 + size]
                    pending = pending[8 + size :]
                    target = stdout if stream == 1 else stderr
                    stream_name = "stdout" if stream == 1 else "stderr"
                    decoder = stdout_decoder if stream == 1 else stderr_decoder
                    room = max_output_bytes - (len(stdout) + len(stderr))
                    if room <= 0:
                        truncated = True
                        if on_chunk is not None:
                            try:
                                on_chunk(stream_name, "", True)
                            except Exception:
                                pass
                        break
                    if len(payload) > room:
                        keep = payload[:room]
                        target.extend(keep)
                        _emit(stream_name, decoder, keep)
                        truncated = True
                        if on_chunk is not None:
                            try:
                                on_chunk(stream_name, "", True)
                            except Exception:
                                pass
                        break
                    target.extend(payload)
                    _emit(stream_name, decoder, payload)
                if truncated:
                    break
            if timed_out:
                try:
                    container.kill()
                except Exception:
                    pass
        finally:
            try:
                raw.close()
            except Exception:
                pass

        inspect = api.exec_inspect(exec_id)
        if timed_out:
            exit_code = 124
        else:
            exit_code = int(inspect.get("ExitCode") if inspect.get("ExitCode") is not None else 1)

        return ExecResult(
            exit_code=exit_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            timed_out=timed_out,
            truncated=truncated,
        )

    def destroy(self, sandbox_id: str) -> None:
        try:
            container = self._client.containers.get(sandbox_id)
            container.remove(force=True)
        except Exception:
            pass

    def destroy_labeled(self, *, execution_id: str | None = None) -> int:
        label_filters = [f"{LABEL_SANDBOX}=true"]
        if execution_id:
            label_filters.append(f"{LABEL_EXECUTION}={execution_id}")
        count = 0
        for container in self._client.containers.list(all=True, filters={"label": label_filters}):
            try:
                container.remove(force=True)
                count += 1
            except Exception:
                pass
        return count
