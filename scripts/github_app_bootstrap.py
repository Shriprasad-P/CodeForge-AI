#!/usr/bin/env python3
"""Development-only GitHub App Manifest bootstrap for AgentDock."""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SECRET_DIR = ROOT / ".local-secrets"
PEM_PATH = SECRET_DIR / "github-app.pem"
COMPOSE_PEM_PATH = "/run/secrets/github-app.pem"
GITHUB_MANIFEST_URL = "https://github.com/settings/apps/new"
GITHUB_CONVERSION_URL = "https://api.github.com/app-manifests/{code}/conversions"


class BootstrapExchangeError(RuntimeError):
    """A safe, non-sensitive manifest conversion failure."""


@dataclass
class _BootstrapState:
    expected_state: str
    manifest: str
    result: dict[str, object] | None = None
    error: str | None = None
    callback_received: bool = False
    code_received: bool = False
    state_valid: bool | None = None
    exchange_attempted: bool = False
    exchange_status: int | None = None
    callback_count: int = 0
    done: threading.Event = field(default_factory=threading.Event)


def _validate_url(value: str, *, field: str, https_only: bool = False) -> None:
    parsed = urlparse(value)
    allowed = {"https"} if https_only else {"http", "https"}
    if parsed.scheme not in allowed or not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{field} must be an absolute {'HTTPS' if https_only else 'HTTP(S)'} URL")


def build_manifest(*, redirect_url: str, app_name: str, webhook_url: str | None = None) -> dict[str, object]:
    _validate_url(redirect_url, field="redirect_url")
    callback_url = "http://localhost:8000/api/github/callback"
    setup_url = "http://localhost:8000/api/github/setup"
    _validate_url(callback_url, field="callback_urls[0]")
    _validate_url(setup_url, field="setup_url")
    manifest: dict[str, object] = {
        "name": app_name,
        "url": "http://localhost:3000",
        "redirect_url": redirect_url,
        "callback_urls": [callback_url],
        "setup_url": setup_url,
        "description": "Development GitHub App for AgentDock publication verification.",
        "public": False,
        "default_permissions": {"metadata": "read", "contents": "write", "pull_requests": "write"},
        "request_oauth_on_install": True,
        "setup_on_update": False,
    }
    _validate_url(manifest["url"], field="url")
    if webhook_url:
        _validate_url(webhook_url, field="hook_attributes.url", https_only=True)
        manifest["hook_attributes"] = {"url": webhook_url, "active": True}
    return manifest


def _update_env(values: dict[str, str]) -> None:
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = existing.splitlines()
    updated: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in values:
            updated.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(line)
    if updated and updated[-1] != "":
        updated.append("")
    updated.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    ENV_PATH.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)


def validate_credentials_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise BootstrapExchangeError("GitHub returned a non-object JSON response")
    missing: list[str] = []
    for key in ("id", "slug", "client_id", "client_secret", "webhook_secret"):
        value = payload.get(key)
        if not isinstance(value, (int, str)) or not str(value).strip():
            missing.append(key)
    pem = payload.get("pem")
    if not isinstance(pem, str) or not pem.startswith("-----BEGIN") or "PRIVATE KEY" not in pem:
        missing.append("pem")
    if missing:
        raise BootstrapExchangeError(f"GitHub returned incomplete App credentials (missing: {', '.join(missing)})")
    return payload


def exchange_manifest_code(code: str, state: _BootstrapState | None = None) -> dict[str, object]:
    try:
        response = httpx.post(
            GITHUB_CONVERSION_URL.format(code=quote(code, safe="")),
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            timeout=20,
        )
        if state is not None:
            state.exchange_status = response.status_code
        if response.status_code >= 400:
            raise BootstrapExchangeError(f"GitHub manifest conversion failed (HTTP {response.status_code})")
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            raise BootstrapExchangeError("GitHub returned an unexpected response content type")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise BootstrapExchangeError("GitHub returned malformed JSON") from exc
        return validate_credentials_payload(payload)
    except BootstrapExchangeError:
        raise
    except httpx.TimeoutException as exc:
        raise BootstrapExchangeError("GitHub manifest conversion timed out") from exc
    except httpx.HTTPError as exc:
        raise BootstrapExchangeError("GitHub manifest conversion request failed") from exc


def persist_credentials(payload: dict[str, object]) -> str:
    payload = validate_credentials_payload(payload)
    pem = payload["pem"]
    required = {
        "GITHUB_APP_ID": str(payload.get("id", "")),
        "GITHUB_APP_SLUG": str(payload.get("slug", "")),
        "GITHUB_APP_CLIENT_ID": str(payload.get("client_id", "")),
        "GITHUB_APP_CLIENT_SECRET": str(payload.get("client_secret", "")),
        "GITHUB_APP_PRIVATE_KEY_PATH": COMPOSE_PEM_PATH,
        "GITHUB_WEBHOOK_SECRET": str(payload.get("webhook_secret", "")),
        "GITHUB_CALLBACK_URL": "http://localhost:8000/api/github/callback",
        "GITHUB_SETUP_URL": "http://localhost:8000/api/github/setup",
    }
    if any(not value or value == "None" for value in required.values()):
        raise RuntimeError("GitHub returned incomplete App credentials")
    SECRET_DIR.mkdir(mode=0o700, exist_ok=True)
    os.chmod(SECRET_DIR, 0o700)
    PEM_PATH.write_text(pem, encoding="utf-8")
    os.chmod(PEM_PATH, 0o600)
    _update_env(required)
    return required["GITHUB_APP_SLUG"]


class _BootstrapHandler(BaseHTTPRequestHandler):
    bootstrap: _BootstrapState

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/start":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            action = f"{GITHUB_MANIFEST_URL}?{urlencode({'state': self.bootstrap.expected_state})}"
            manifest = html.escape(self.bootstrap.manifest, quote=True)
            body = (
                "<!doctype html><form id='f' method='post' action='" + action + "'>"
                "<input type='hidden' name='manifest' value=\"" + manifest + "\"></form>"
                "<p>Continuing to GitHub App registration…</p><script>f.submit()</script>"
            )
            self.wfile.write(body.encode())
            return
        if parsed.path != "/callback":
            self.send_error(404)
            return
        self.bootstrap.callback_received = True
        self.bootstrap.callback_count += 1
        if self.bootstrap.callback_count > 1:
            self._finish(409, "GitHub callback was already handled")
            return
        callback_state = query.get("state", [""])[0]
        if callback_state != self.bootstrap.expected_state:
            self.bootstrap.state_valid = False
            self.bootstrap.error = "GitHub callback state did not match"
            self._finish(400, self.bootstrap.error)
            return
        code = query.get("code", [""])[0]
        if not code:
            self.bootstrap.state_valid = True
            self.bootstrap.code_received = False
            self.bootstrap.error = "GitHub callback did not contain a registration code"
            self._finish(400, self.bootstrap.error)
            return
        self.bootstrap.state_valid = True
        self.bootstrap.code_received = True
        self.bootstrap.exchange_attempted = True
        try:
            self.bootstrap.result = exchange_manifest_code(code, self.bootstrap)
            self._finish(200, "GitHub App registration received. You may close this tab.")
        except BootstrapExchangeError as exc:
            self.bootstrap.error = str(exc)
            self._finish(502, self.bootstrap.error)

    def _finish(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode())
        self.bootstrap.done.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap an AgentDock development GitHub App")
    parser.add_argument("--no-open", action="store_true", help="print the local start URL instead of opening it")
    parser.add_argument("--name", default="", help="override the generated GitHub App name")
    parser.add_argument("--timeout", type=float, default=3600, help="maximum seconds to wait for the GitHub callback")
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("AGENTDOCK_PUBLIC_WEBHOOK_URL", ""),
        help="optional externally reachable HTTPS webhook URL",
    )
    args = parser.parse_args()
    if os.environ.get("APP_ENV", "development").lower() not in {"development", "local", "test"}:
        raise SystemExit("Refusing to run outside development/local/test")
    state = secrets.token_urlsafe(32)
    server = HTTPServer(("127.0.0.1", 0), _BootstrapHandler)
    redirect_url = f"http://127.0.0.1:{server.server_port}/callback"
    bootstrap = _BootstrapState(
        expected_state=state,
        manifest=json.dumps(
            build_manifest(
                redirect_url=redirect_url,
                app_name=args.name or f"AgentDock Dev {secrets.token_hex(3)}",
                webhook_url=args.webhook_url or None,
            ),
            separators=(",", ":"),
        )
    )
    server.RequestHandlerClass.bootstrap = bootstrap
    start_url = f"http://127.0.0.1:{server.server_port}/start"
    print("Open the local URL to register the development GitHub App:")
    print(start_url)
    print(f"GitHub redirect URL: {redirect_url}")
    if not args.no_open:
        webbrowser.open(start_url)
    server.timeout = 1
    deadline = time.monotonic() + max(args.timeout, 0)
    while not bootstrap.done.wait(0.1):
        if time.monotonic() >= deadline:
            bootstrap.error = "Timed out waiting for the GitHub callback"
            break
        server.handle_request()
    server.server_close()
    print(f"Manifest callback received: {'yes' if bootstrap.callback_received else 'no'}")
    print(f"Code received: {'yes' if bootstrap.code_received else 'no'}")
    print(f"State validation: {'passed' if bootstrap.state_valid is True else 'failed' if bootstrap.state_valid is False else 'not run'}")
    print(f"Manifest exchange attempted: {'yes' if bootstrap.exchange_attempted else 'no'}")
    if bootstrap.exchange_status is not None:
        print(f"GitHub response status: {bootstrap.exchange_status}")
    if bootstrap.error:
        raise SystemExit(bootstrap.error)
    if bootstrap.result is None:
        raise SystemExit("GitHub App registration returned no credentials")
    try:
        slug = persist_credentials(bootstrap.result)
    except (OSError, RuntimeError) as exc:
        print("Credential persistence: failed")
        raise SystemExit(f"Credential persistence failed: {type(exc).__name__}") from exc
    print("Credential persistence: succeeded")
    print(f"GitHub App credentials saved locally for slug: {slug}")
    print("Install it only on the disposable agentdock-live-test repository:")
    print(f"https://github.com/apps/{slug}/installations/new")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
