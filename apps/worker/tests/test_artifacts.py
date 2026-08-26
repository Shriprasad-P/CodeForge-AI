from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from src.artifacts import ArtifactTooLarge, capture_publication_artifact


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.com")
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    (repo / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (repo / "rename.txt").write_text("rename me\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return repo, _git(repo, "rev-parse", "HEAD")


def test_complete_mixed_artifact_reproduces_in_fresh_checkout(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    (repo / "deleted.txt").unlink()
    (repo / "rename.txt").rename(repo / "renamed.txt")
    (repo / "binary.bin").write_bytes(b"\x00\x01\x02\x03")
    artifact = capture_publication_artifact(
        repo,
        base_sha=base_sha,
        max_artifact_bytes=1_000_000,
        max_preview_chars=40,
    )
    assert artifact.artifact_size == len(artifact.patch)
    assert artifact.artifact_hash == hashlib.sha256(artifact.patch).hexdigest()
    paths = {entry["path"] for entry in artifact.manifest}
    assert paths in (
        {
            "binary.bin",
            "deleted.txt",
            "new.txt",
            "renamed.txt",
            "tracked.txt",
        },
        {
            "binary.bin",
            "deleted.txt",
            "new.txt",
            "renamed.txt",
            "rename.txt",
            "tracked.txt",
        },
    )
    assert any(entry["binary"] for entry in artifact.manifest)
    assert artifact.preview_truncated is True

    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", "-q", str(repo), str(fresh)], check=True)
    subprocess.run(["git", "-C", str(fresh), "checkout", "-q", base_sha], check=True)
    subprocess.run(["git", "-C", str(fresh), "apply", "--binary", "-"], input=artifact.patch, check=True)
    reproduced = capture_publication_artifact(
        fresh,
        base_sha=base_sha,
        max_artifact_bytes=1_000_000,
        max_preview_chars=40,
    )
    assert reproduced.patch == artifact.patch
    assert reproduced.manifest == artifact.manifest


def test_pure_rename_and_mode_change_are_manifested(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    (repo / "rename.txt").rename(repo / "renamed.txt")
    (repo / "executable.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "executable.sh").chmod(0o755)
    artifact = capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=1_000_000, max_preview_chars=1000)
    renamed = next(entry for entry in artifact.manifest if entry["path"] == "renamed.txt")
    assert renamed["change_type"] == "renamed"
    assert renamed["previous_path"] == "rename.txt"
    assert any(entry["path"] == "executable.sh" and entry["new_mode"] == "100755" for entry in artifact.manifest)


def test_staged_and_untracked_changes_are_captured_without_changing_real_index(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "staged.txt"], check=True)
    (repo / "tracked.txt").write_text("working tree\n", encoding="utf-8")
    before_index = _git(repo, "diff", "--cached", "--binary")
    before_status = _git(repo, "status", "--short")
    artifact = capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=1_000_000, max_preview_chars=1000)
    after_index = _git(repo, "diff", "--cached", "--binary")
    after_status = _git(repo, "status", "--short")
    assert "staged.txt" in artifact.patch.decode("utf-8", errors="replace")
    assert before_index == after_index
    assert before_status == after_status


def test_noop_is_deterministic_and_tampering_changes_hash(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    first = capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=1000, max_preview_chars=1000)
    second = capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=1000, max_preview_chars=1000)
    assert first.patch == b""
    assert first.artifact_size == 0
    assert first.artifact_hash == second.artifact_hash
    assert first.manifest == second.manifest == []
    assert hashlib.sha256(first.patch + b"x").hexdigest() != first.artifact_hash


def test_oversized_artifact_is_rejected_without_partial_patch(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    (repo / "large.txt").write_text("x" * 5000, encoding="utf-8")
    with pytest.raises(ArtifactTooLarge) as exc_info:
        capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=100, max_preview_chars=100)
    assert exc_info.value.size > 100
    assert exc_info.value.manifest


def test_ignored_files_and_gitattributes_filters_cannot_hide_or_execute(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    marker = tmp_path / "filter-marker"
    filter_script = tmp_path / "filter.py"
    filter_script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('executed')\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    (repo / ".gitattributes").write_text("tracked.txt filter=evil\n", encoding="utf-8")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-qm", "attributes")
    base_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "config", "filter.evil.clean", f"python3 {filter_script}")
    _git(repo, "config", "filter.evil.smudge", "cat")
    config_before = (repo / ".git" / "config").read_bytes()
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("must publish\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

    artifact = capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=1_000_000, max_preview_chars=1000)

    assert marker.exists() is False
    assert (repo / ".git" / "config").read_bytes() == config_before
    assert {entry["path"] for entry in artifact.manifest} >= {".gitignore", "ignored.txt", "tracked.txt"}


def test_replace_refs_cannot_change_the_recorded_base(tmp_path: Path) -> None:
    repo, base_sha = _repo(tmp_path)
    (repo / "tracked.txt").write_text("replacement\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "replacement")
    replacement_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "reset", "--hard", "-q", base_sha)
    (repo / "tracked.txt").write_text("replacement\n", encoding="utf-8")
    _git(repo, "replace", base_sha, replacement_sha)
    try:
        artifact = capture_publication_artifact(repo, base_sha=base_sha, max_artifact_bytes=1_000_000, max_preview_chars=1000)
    finally:
        subprocess.run(["git", "-C", str(repo), "replace", "-d", base_sha], check=True, capture_output=True)
    assert any(entry["path"] == "tracked.txt" for entry in artifact.manifest)
