from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "github_app_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("github_app_bootstrap", SCRIPT)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_manifest_omits_automatic_installation_events() -> None:
    manifest = bootstrap.build_manifest(
        redirect_url="http://127.0.0.1:9000/callback",
        app_name="AgentDock Dev Test",
    )
    assert "installation" not in manifest.get("default_events", [])
    assert "installation_repositories" not in manifest.get("default_events", [])
    assert "default_events" not in manifest


def test_manifest_omits_blank_webhook_and_keeps_minimum_permissions() -> None:
    manifest = bootstrap.build_manifest(
        redirect_url="http://127.0.0.1:9000/callback",
        app_name="AgentDock Dev Test",
        webhook_url=None,
    )
    assert "hook_attributes" not in manifest
    assert manifest["default_permissions"] == {
        "metadata": "read",
        "contents": "write",
        "pull_requests": "write",
    }
    assert manifest["redirect_url"] == "http://127.0.0.1:9000/callback"
    assert manifest["callback_urls"] == ["http://localhost:8000/api/github/callback"]
    assert manifest["setup_url"] == "http://localhost:8000/api/github/setup"


def test_manifest_accepts_valid_https_webhook() -> None:
    manifest = bootstrap.build_manifest(
        redirect_url="http://127.0.0.1:9000/callback",
        app_name="AgentDock Dev Test",
        webhook_url="https://example.test/github/webhooks",
    )
    assert manifest["hook_attributes"] == {
        "url": "https://example.test/github/webhooks",
        "active": True,
    }


@pytest.mark.parametrize("url", ["localhost:8000/hook", "http://localhost:8000/hook"])
def test_webhook_requires_valid_https_url(url: str) -> None:
    with pytest.raises(ValueError):
        bootstrap.build_manifest(
            redirect_url="http://127.0.0.1:9000/callback",
            app_name="AgentDock Dev Test",
            webhook_url=url,
        )


def test_blank_webhook_is_omitted() -> None:
    manifest = bootstrap.build_manifest(
        redirect_url="http://127.0.0.1:9000/callback",
        app_name="AgentDock Dev Test",
        webhook_url="",
    )
    assert "hook_attributes" not in manifest


def test_persist_credentials_writes_only_ignored_local_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    secret_dir = tmp_path / ".local-secrets"
    monkeypatch.setattr(bootstrap, "ENV_PATH", env_path)
    monkeypatch.setattr(bootstrap, "SECRET_DIR", secret_dir)
    monkeypatch.setattr(bootstrap, "PEM_PATH", secret_dir / "github-app.pem")
    slug = bootstrap.persist_credentials(
        {
            "id": 123,
            "slug": "agentdock-dev-test",
            "client_id": "client-id-placeholder",
            "client_secret": "client-secret-placeholder",
            "webhook_secret": "webhook-secret-placeholder",
            "pem": "-----BEGIN PRIVATE KEY-----\nplaceholder\n-----END PRIVATE KEY-----\n",
        }
    )
    assert slug == "agentdock-dev-test"
    assert (secret_dir / "github-app.pem").stat().st_mode & 0o777 == 0o600
    text = env_path.read_text()
    assert "GITHUB_APP_CLIENT_SECRET=client-secret-placeholder" in text
    assert "PRIVATE KEY" not in text
    assert os.stat(env_path).st_mode & 0o777 == 0o600
