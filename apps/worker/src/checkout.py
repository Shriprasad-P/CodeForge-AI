"""Worker checkout helpers — clone on the worker host, never inside the sandbox with credentials."""

from __future__ import annotations

import re
import shutil
import subprocess
import os
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

TOKEN_RE = re.compile(r"(x-access-token:)([^@]+)(@)", re.IGNORECASE)
URL_CREDENTIAL_RE = re.compile(r"(https?://)([^/\s@]+@)", re.IGNORECASE)


@contextmanager
def github_credential_env(token: str) -> Iterator[dict[str, str]]:
    """Provide Git a short-lived askpass file without exposing the token.

    GitHub installation tokens must not appear in argv, process environment,
    remotes, logs, or exception text.  Git receives only a path to a 0600 file
    through an askpass helper; the directory is removed on every exit path.
    """
    if not token:
        raise ValueError("GitHub token is required")
    root = Path(tempfile.mkdtemp(prefix="agentdock-git-", dir=None))
    try:
        root.chmod(0o700)
        token_file = root / "token"
        token_file.write_text(token, encoding="utf-8")
        token_file.chmod(0o600)
        askpass = root / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' x-access-token ;;\n"
            "  *Password*) cat -- \"$GIT_ASKPASS_TOKEN_FILE\" ;;\n"
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        env = os.environ.copy()
        env.update(
            {
                "GIT_ASKPASS": str(askpass),
                "GIT_ASKPASS_TOKEN_FILE": str(token_file),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }
        )
        yield env
    finally:
        shutil.rmtree(root, ignore_errors=True)


def sanitize_text(text: str, token: str | None = None) -> str:
    redacted = text
    if token:
        redacted = redacted.replace(token, "[REDACTED]")
    redacted = TOKEN_RE.sub(r"\1[REDACTED]\3", redacted)
    redacted = URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", redacted)
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
    # Keep credentials out of argv and the persisted remote URL.
    url = f"https://github.com/{owner}/{name}.git"
    try:
        with github_credential_env(installation_token) as env:
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
                env=env,
            )
    except subprocess.CalledProcessError as exc:
        # Sanitize before raising.
        msg = sanitize_text((exc.stderr or exc.stdout or "clone failed"), installation_token)
        raise RuntimeError(msg) from None
    finally:
        # Ensure remote is sanitized even if clone partially succeeded.
        if dest.exists():
            _rewrite_remotes(dest, f"https://github.com/{owner}/{name}.git", installation_token)


def _rewrite_remotes(repo: Path, clean_url: str, token: str | None = None) -> None:
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
        config.write_text(sanitize_text(text, token), encoding="utf-8")


def assert_remote_sanitized(repo: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo), "remote", "-v"],
        check=True,
        capture_output=True,
        text=True,
    )
    out = result.stdout
    if "x-access-token:" in out or ":@" in out or URL_CREDENTIAL_RE.search(out):
        raise RuntimeError("git remote still contains credentials")
