from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import subprocess

from src.checkout import clone_github_repo, github_credential_env, sanitize_text


def test_sanitize_token() -> None:
    text = "https://x-access-token:ghs_secret@github.com/o/r.git Authorization: Bearer abc"
    out = sanitize_text(text, token="ghs_secret")
    assert "ghs_secret" not in out
    assert "[REDACTED]" in out


def test_credential_helper_is_restrictive_and_cleaned_up() -> None:
    token = "ghs_stage5_secret"
    helper_path: Path | None = None
    with github_credential_env(token) as env:
        helper_path = Path(env["GIT_ASKPASS"])
        token_path = Path(env["GIT_ASKPASS_TOKEN_FILE"])
        assert token not in " ".join(env.values())
        assert helper_path.stat().st_mode & 0o777 == 0o700
        assert token_path.stat().st_mode & 0o777 == 0o600
        assert token_path.read_text(encoding="utf-8") == token
    assert helper_path is not None
    assert not helper_path.exists()


def test_clone_never_places_token_in_argv_or_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token = "ghs_clone_secret"
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    askpass_paths: list[Path] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs.get("env")))
        env = kwargs.get("env") or {}
        if "GIT_ASKPASS" in env:
            askpass_paths.append(Path(env["GIT_ASKPASS"]))
        if argv[1:2] == ["clone"]:
            dest = Path(argv[-1])
            (dest / ".git").mkdir(parents=True)
            (dest / ".git" / "config").write_text("[remote \"origin\"]\n", encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("src.checkout.subprocess.run", fake_run)
    clone_github_repo(
        dest=tmp_path / "repo",
        owner="owner",
        name="repo",
        default_branch="main",
        installation_token=token,
    )
    assert calls
    assert all(token not in " ".join(argv) for argv, _ in calls)
    assert all(token not in " ".join((env or {}).values()) for _, env in calls)
    assert askpass_paths and all(not path.exists() for path in askpass_paths)


def test_clone_helper_is_cleaned_up_when_git_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token = "ghs_failure_secret"
    askpass_paths: list[Path] = []

    def fail_run(argv, **kwargs):
        env = kwargs.get("env") or {}
        if "GIT_ASKPASS" in env:
            askpass_paths.append(Path(env["GIT_ASKPASS"]))
        raise subprocess.CalledProcessError(128, argv, stderr="fatal: authorization failed")

    monkeypatch.setattr("src.checkout.subprocess.run", fail_run)
    with pytest.raises(RuntimeError) as exc_info:
        clone_github_repo(
            dest=tmp_path / "repo",
            owner="owner",
            name="repo",
            default_branch="main",
            installation_token=token,
        )
    assert token not in str(exc_info.value)
    assert askpass_paths and all(not path.exists() for path in askpass_paths)
