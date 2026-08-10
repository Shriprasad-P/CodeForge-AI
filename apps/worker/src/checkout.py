"""Worker checkout helpers — clone on the worker host, never inside the sandbox with credentials."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

TOKEN_RE = re.compile(r"(x-access-token:)([^@]+)(@)", re.IGNORECASE)


def sanitize_text(text: str, token: str | None = None) -> str:
    redacted = text
    if token:
        redacted = redacted.replace(token, "[REDACTED]")
    redacted = TOKEN_RE.sub(r"\1[REDACTED]\3", redacted)
    redacted = re.sub(r"(?i)authorization:\s*\S+", "Authorization: [REDACTED]", redacted)
    return redacted


def prepare_fixture_checkout(fixture_path: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(fixture_path, dest, dirs_exist_ok=False)
    # Ensure a sanitized remote if .git exists
    git_config = dest / ".git" / "config"
    if git_config.exists():
        _rewrite_remotes(dest, "https://github.com/example/fixture.git")


def clone_github_repo(
    *,
    dest: Path,
    owner: str,
    name: str,
    default_branch: str,
    installation_token: str,
) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Token only in process argv environment of git — never logged.
    url = f"https://x-access-token:{installation_token}@github.com/{owner}/{name}.git"
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                default_branch,
                url,
                str(dest),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        # Sanitize before raising.
        msg = sanitize_text((exc.stderr or exc.stdout or "clone failed"), installation_token)
        raise RuntimeError(msg) from None
    finally:
        # Ensure remote is sanitized even if clone partially succeeded.
        if dest.exists():
            _rewrite_remotes(dest, f"https://github.com/{owner}/{name}.git")


def _rewrite_remotes(repo: Path, clean_url: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", clean_url],
        check=False,
        capture_output=True,
        text=True,
    )
    # Belt-and-suspenders: strip any leftover token strings from config.
    config = repo / ".git" / "config"
    if config.exists():
        text = config.read_text(encoding="utf-8")
        config.write_text(sanitize_text(text), encoding="utf-8")


def assert_remote_sanitized(repo: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "remote", "-v"],
        check=True,
        capture_output=True,
        text=True,
    )
    out = result.stdout
    if "x-access-token:" in out or ":@" in out:
        raise RuntimeError("git remote still contains credentials")
