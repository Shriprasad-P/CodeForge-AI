from __future__ import annotations

import importlib.util
import os
import sys
import threading
from http.server import HTTPServer
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "github_app_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("github_app_bootstrap", SCRIPT)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules["github_app_bootstrap"] = bootstrap
SPEC.loader.exec_module(bootstrap)


PEM = "-----BEGIN PRIVATE KEY-----\nplaceholder\n-----END PRIVATE KEY-----\n"


class FakeResponse:
    def __init__(self, status_code: int = 201, payload: object | None = None, content_type: str = "application/json"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload


def credential_payload() -> dict[str, object]:
    return {
        "id": 123,
        "slug": "agentdock-dev-test",
        "client_id": "client-id-placeholder",
        "client_secret": "client-secret-placeholder",
        "webhook_secret": "webhook-secret-placeholder",
        "pem": PEM,
    }


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
    slug = bootstrap.persist_credentials(credential_payload())
    assert slug == "agentdock-dev-test"
    assert (secret_dir / "github-app.pem").stat().st_mode & 0o777 == 0o600
    text = env_path.read_text()
    assert "GITHUB_APP_CLIENT_SECRET=client-secret-placeholder" in text
    assert "PRIVATE KEY" not in text
    assert os.stat(env_path).st_mode & 0o777 == 0o600


def test_persist_credentials_allows_no_webhook_secret_for_api_only_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    secret_dir = tmp_path / ".local-secrets"
    monkeypatch.setattr(bootstrap, "ENV_PATH", env_path)
    monkeypatch.setattr(bootstrap, "SECRET_DIR", secret_dir)
    monkeypatch.setattr(bootstrap, "PEM_PATH", secret_dir / "github-app.pem")
    payload = credential_payload()
    del payload["webhook_secret"]
    assert bootstrap.persist_credentials(payload, require_webhook_secret=False) == "agentdock-dev-test"
    assert "GITHUB_WEBHOOK_SECRET=" in env_path.read_text()


def test_manifest_code_exchange_maps_success_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str], int]] = []

    def post(url: str, *, headers: dict[str, str], timeout: int) -> FakeResponse:
        calls.append((url, headers, timeout))
        return FakeResponse(payload=credential_payload())

    monkeypatch.setattr(bootstrap.httpx, "post", post)
    state = bootstrap._BootstrapState(expected_state="expected", manifest="{}")
    result = bootstrap.exchange_manifest_code("temporary-code", state)
    assert result == credential_payload()
    assert calls[0][0] == "https://api.github.com/app-manifests/temporary-code/conversions"
    assert calls[0][1]["Accept"] == "application/vnd.github+json"
    assert calls[0][1]["X-GitHub-Api-Version"] == "2022-11-28"
    assert state.exchange_status == 201


def test_manifest_code_exchange_allows_missing_webhook_secret_without_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = credential_payload()
    del payload["webhook_secret"]
    state = bootstrap._BootstrapState(expected_state="expected", manifest="{}", webhook_configured=False)
    monkeypatch.setattr(bootstrap.httpx, "post", lambda *args, **kwargs: FakeResponse(payload=payload))
    assert bootstrap.exchange_manifest_code("temporary-code", state) == payload


@pytest.mark.parametrize("status", [400, 422, 500, 503])
def test_manifest_code_exchange_reports_http_status_without_response_body(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(bootstrap.httpx, "post", lambda *args, **kwargs: FakeResponse(status_code=status, payload={}))
    with pytest.raises(bootstrap.BootstrapExchangeError, match=rf"HTTP {status}"):
        bootstrap.exchange_manifest_code("temporary-code")


def test_manifest_code_exchange_rejects_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap.httpx, "post", lambda *args, **kwargs: FakeResponse(payload=ValueError("bad json")))
    with pytest.raises(bootstrap.BootstrapExchangeError, match="malformed JSON"):
        bootstrap.exchange_manifest_code("temporary-code")


def test_manifest_code_exchange_rejects_missing_required_field(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = credential_payload()
    del payload["client_secret"]
    monkeypatch.setattr(bootstrap.httpx, "post", lambda *args, **kwargs: FakeResponse(payload=payload))
    with pytest.raises(bootstrap.BootstrapExchangeError, match="missing: client_secret"):
        bootstrap.exchange_manifest_code("temporary-code")


def test_manifest_code_exchange_rejects_unexpected_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bootstrap.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(payload=credential_payload(), content_type="text/html"),
    )
    with pytest.raises(bootstrap.BootstrapExchangeError, match="content type"):
        bootstrap.exchange_manifest_code("temporary-code")


def test_manifest_code_exchange_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def post(*args, **kwargs):
        raise bootstrap.httpx.TimeoutException("timed out")

    monkeypatch.setattr(bootstrap.httpx, "post", post)
    with pytest.raises(bootstrap.BootstrapExchangeError, match="timed out"):
        bootstrap.exchange_manifest_code("temporary-code")


def test_callback_exchanges_once_and_rejects_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap.httpx, "post", lambda *args, **kwargs: FakeResponse(payload=credential_payload()))
    state = bootstrap._BootstrapState(expected_state="expected-state", manifest="{}")
    server = HTTPServer(("127.0.0.1", 0), bootstrap._BootstrapHandler)
    server.RequestHandlerClass.bootstrap = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = bootstrap.httpx.Client()
    try:
        first = client.get(f"http://127.0.0.1:{server.server_port}/callback?code=temporary-code&state=expected-state")
        duplicate = client.get(f"http://127.0.0.1:{server.server_port}/callback?code=temporary-code&state=expected-state")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        client.close()
    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert state.result == credential_payload()
    assert state.callback_count == 2


@pytest.mark.parametrize(
    ("path", "status", "state_valid", "code_received"),
    [
        ("/callback?code=temporary-code&state=wrong-state", 400, False, False),
        ("/callback?state=expected-state", 400, True, False),
    ],
)
def test_callback_rejects_wrong_state_or_missing_code(
    monkeypatch: pytest.MonkeyPatch, path: str, status: int, state_valid: bool, code_received: bool
) -> None:
    post = lambda *args, **kwargs: pytest.fail("conversion must not be attempted")
    monkeypatch.setattr(bootstrap.httpx, "post", post)
    state = bootstrap._BootstrapState(expected_state="expected-state", manifest="{}")
    server = HTTPServer(("127.0.0.1", 0), bootstrap._BootstrapHandler)
    server.RequestHandlerClass.bootstrap = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = bootstrap.httpx.Client()
    try:
        response = client.get(f"http://127.0.0.1:{server.server_port}{path}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        client.close()
    assert response.status_code == status
    assert state.state_valid is state_valid
    assert state.code_received is code_received
    assert state.exchange_attempted is False
