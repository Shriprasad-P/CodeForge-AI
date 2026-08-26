from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


PUBLICATION_ARTIFACT_VERSION = 1


class ArtifactCaptureError(RuntimeError):
    """The repository could not be represented as a safe publication artifact."""


class ArtifactTooLarge(ArtifactCaptureError):
    def __init__(self, size: int, manifest: list[dict[str, Any]]) -> None:
        super().__init__("publication artifact exceeds the configured size limit")
        self.size = size
        self.manifest = manifest


@dataclass(frozen=True)
class PublicationArtifact:
    patch: bytes
    artifact_hash: str
    artifact_size: int
    manifest: list[dict[str, Any]]
    preview: str
    preview_truncated: bool
    diff_stat: str


def _git(repo: Path, args: list[str], *, env: dict[str, str] | None = None) -> bytes:
    command_env = os.environ.copy()
    command_env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    for key in list(command_env):
        if key == "GIT_EXTERNAL_DIFF" or key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            command_env.pop(key, None)
    command_env.pop("GIT_CONFIG_COUNT", None)
    if env:
        command_env.update(env)
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=command_env,
        check=False,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"git operation failed").decode("utf-8", errors="replace")
        raise ArtifactCaptureError(detail.strip()[:500])
    return result.stdout


def _decode_path(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactCaptureError("repository path is not valid UTF-8") from exc


def _safe_config_skeleton(raw: bytes) -> str:
    """Keep Git object-format/core metadata while dropping executable policy."""
    section = ""
    core: dict[str, str] = {}
    extensions: dict[str, str] = {}
    for raw_line in raw.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if section == "core" and key in {"repositoryformatversion", "filemode", "bare", "logallrefupdates"}:
            core[key] = value
        elif section == "extensions" and key.lower() == "objectformat":
            extensions[key] = value
    core.setdefault("repositoryformatversion", "0")
    core.setdefault("filemode", "true")
    core.setdefault("bare", "false")
    core.setdefault("logallrefupdates", "true")
    lines = ["[core]"] + [f"\t{key} = {value}" for key, value in core.items()]
    if extensions:
        lines.extend(["[extensions]", *[f"\t{key} = {value}" for key, value in extensions.items()]])
    return "\n".join(lines) + "\n"


def _parse_manifest(raw: bytes, binary_paths: set[str]) -> list[dict[str, Any]]:
    records = raw.split(b"\0")
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(records) and records[index]:
        fields = records[index].decode("ascii", errors="strict").split()
        index += 1
        if len(fields) != 5 or not fields[0].startswith(":"):
            raise ArtifactCaptureError("unexpected git manifest record")
        old_mode, new_mode, old_blob, new_blob, status = fields
        old_mode = old_mode[1:]
        paths = [records[index]]
        index += 1
        if status[:1] in {"R", "C"}:
            paths.append(records[index])
            index += 1
        decoded_paths = [_decode_path(path) for path in paths]
        current_path = decoded_paths[-1]
        change_type = {
            "A": "added",
            "D": "deleted",
            "M": "modified",
            "T": "modified",
        }.get(status[:1], "modified")
        mode_changed = old_mode != new_mode and old_mode != "000000" and new_mode != "000000"
        if status[:1] == "R":
            change_type = "renamed"
        elif mode_changed and old_blob == new_blob:
            change_type = "mode_changed"
        entry: dict[str, Any] = {
            "path": current_path,
            "change_type": change_type,
            "old_mode": old_mode,
            "new_mode": new_mode,
            "old_blob": old_blob,
            "new_blob": new_blob,
            "binary": current_path in binary_paths or (decoded_paths[0] in binary_paths if decoded_paths else False),
        }
        if mode_changed:
            entry["mode_changed"] = True
        if status[:1] in {"R", "C"}:
            entry["previous_path"] = decoded_paths[0]
            if status[:1] == "C":
                entry["change_type"] = "added"
        entries.append(entry)
    entries.sort(key=lambda item: (str(item["path"]), str(item.get("previous_path", ""))))
    return entries


def _binary_paths(repo: Path, base_sha: str, env: dict[str, str]) -> set[str]:
    raw = _git(repo, ["diff", "--cached", "--numstat", "-z", "--no-renames", base_sha, "--"], env=env)
    paths: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        parts = record.split(b"\t", 2)
        if len(parts) == 3 and parts[0] == b"-" and parts[1] == b"-":
            paths.add(_decode_path(parts[2]))
    return paths


@contextmanager
def _temporary_index(repo: Path, base_sha: str) -> Iterator[dict[str, str]]:
    fd, index_path = tempfile.mkstemp(prefix="agentdock-publication-index-")
    os.close(fd)
    os.unlink(index_path)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index_path
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    config_path = repo / ".git" / "config"
    config_backup: bytes | None = None
    try:
        # The checkout's config is worker-created, but may contain filters
        # inherited from an untrusted source checkout. Git config is executable
        # policy, so use a minimal config for capture and restore it exactly.
        if config_path.is_file() and not config_path.is_symlink():
            config_backup = config_path.read_bytes()
            config_path.write_text(_safe_config_skeleton(config_backup), encoding="utf-8")
        # Rebuild an isolated index from the recorded base, then stage the
        # complete working tree. The caller's real index is never changed.
        _git(repo, ["read-tree", base_sha], env=env)
        # Repository-controlled .gitattributes can invoke arbitrary clean or
        # diff filters. Temporarily remove every attributes file while Git
        # stages and renders the artifact, then stage those files explicitly
        # with --no-filters. The isolated index and trusted .git directory are
        # never exposed to the sandbox export.
        attribute_files = [
            path
            for path in repo.rglob(".gitattributes")
            if ".git" not in path.parts and (path.is_file() or path.is_symlink())
        ]
        moved: list[tuple[Path, Path, int]] = []
        with tempfile.TemporaryDirectory(prefix="agentdock-attributes-") as attributes_dir:
            for path in attribute_files:
                relative = path.relative_to(repo)
                saved = Path(attributes_dir) / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                mode_bits = path.lstat().st_mode
                shutil.move(str(path), str(saved))
                moved.append((path, saved, mode_bits))
            try:
                for path, _, _ in moved:
                    _git(repo, ["update-index", "--force-remove", "--", str(path.relative_to(repo))], env=env)
                _git(repo, ["add", "-A", "-f", "--", "."], env=env)
                for path, saved, mode_bits in moved:
                    blob = _git(repo, ["hash-object", "-w", "--no-filters", "--", str(saved)], env=env).decode("ascii").strip()
                    mode = "120000" if os.stat(saved, follow_symlinks=False).st_mode & 0o170000 == 0o120000 else "100755" if mode_bits & 0o111 else "100644"
                    relative = str(path.relative_to(repo))
                    _git(repo, ["update-index", "--add", "--cacheinfo", f"{mode},{blob},{relative}"], env=env)
                yield env
            finally:
                for path, saved, _ in moved:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.exists() or path.is_symlink():
                        if path.is_dir() and not path.is_symlink():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                    shutil.move(str(saved), str(path))
    finally:
        if config_backup is not None:
            config_path.write_bytes(config_backup)
        for suffix in ("", ".lock"):
            try:
                os.unlink(index_path + suffix)
            except FileNotFoundError:
                pass


def prepare_trusted_capture_checkout(base_repo: Path, exported_workspace: Path, destination: Path) -> None:
    """Overlay an untrusted export onto a worker-owned base Git checkout.

    The export is treated as data only: its .git directory is never copied,
    and the destination keeps the worker-created object database/configuration.
    """
    export_root = exported_workspace.resolve()
    for path in exported_workspace.rglob("*"):
        if not path.is_symlink():
            continue
        resolved = path.resolve()
        if export_root not in resolved.parents and resolved != export_root:
            raise ArtifactCaptureError("sandbox export contains an escaping symlink")
        relative = resolved.relative_to(export_root)
        if relative.parts and relative.parts[0] == ".git":
            raise ArtifactCaptureError("sandbox export symlink targets Git metadata")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(base_repo, destination, symlinks=True)
    for child in destination.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in exported_workspace.iterdir():
        if child.name == ".git":
            continue
        target = destination / child.name
        if child.is_dir() and not child.is_symlink():
            shutil.copytree(child, target, symlinks=True)
        else:
            shutil.copy2(child, target, follow_symlinks=False)


def capture_publication_artifact(
    repo: Path,
    *,
    base_sha: str,
    max_artifact_bytes: int,
    max_preview_chars: int,
) -> PublicationArtifact:
    """Capture the complete final worktree against *base_sha* deterministically."""
    if not repo.is_dir() or not (repo / ".git").exists():
        raise ArtifactCaptureError("repository checkout is unavailable")
    head = _git(repo, ["rev-parse", "HEAD"]).decode("ascii", errors="strict").strip()
    if head != base_sha:
        raise ArtifactCaptureError("repository base changed while capturing artifact")
    with _temporary_index(repo, base_sha) as env:
        raw_manifest = _git(
            repo,
            [
                "diff",
                "--cached",
                "--raw",
                "-z",
                "--abbrev=40",
                "--find-renames=50%",
                "--find-copies=50%",
                base_sha,
                "--",
            ],
            env=env,
        )
        manifest = _parse_manifest(raw_manifest, _binary_paths(repo, base_sha, env))
        patch = _git(
            repo,
            [
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                "--no-renames",
                "--no-color",
                base_sha,
                "--",
            ],
            env=env,
        )
        diff_stat = _git(
            repo,
            ["diff", "--cached", "--stat", "--no-renames", base_sha, "--"],
            env=env,
        ).decode("utf-8", errors="replace").strip()
    size = len(patch)
    if size > max_artifact_bytes:
        raise ArtifactTooLarge(size, manifest)
    preview_full = patch.decode("utf-8", errors="replace")
    preview_truncated = len(preview_full) > max_preview_chars
    preview = preview_full[:max_preview_chars]
    return PublicationArtifact(
        patch=patch,
        artifact_hash=hashlib.sha256(patch).hexdigest(),
        artifact_size=size,
        manifest=manifest,
        preview=preview,
        preview_truncated=preview_truncated,
        diff_stat=diff_stat,
    )


def canonical_manifest_bytes(manifest: list[dict[str, Any]]) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
