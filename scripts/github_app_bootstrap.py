#!/usr/bin/env python3
"""Development-only GitHub App Manifest bootstrap for AgentDock."""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SECRET_DIR = ROOT / ".local-secrets"
PEM_PATH = SECRET_DIR / "github-app.pem"
COMPOSE_PEM_PATH = "/run/secrets/github-app.pem"
GITHUB_MANIFEST_URL = "https://github.com/settings/apps/new"


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


def persist_credentials(payload: dict[str, object]) -> str:
    pem = payload.get("pem")
    if not isinstance(pem, str) or not pem.startswith("-----BEGIN"):
        raise RuntimeError("GitHub did not return a private key")
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
    state = ""
    manifest = ""
    result: dict[str, object] | None = None
    error: str | None = None
    done = threading.Event()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/start":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            action = f"{GITHUB_MANIFEST_URL}?{urlencode({'state': self.state})}"
            manifest = html.escape(self.manifest, quote=True)
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
        if query.get("state", [""])[0] != self.state:
            self.error = "GitHub callback state did not match"
            self._finish(400, self.error)
            return
        code = query.get("code", [""])[0]
        if not code:
            self.error = "GitHub callback did not contain a registration code"
            self._finish(400, self.error)
            return
        try:
            response = httpx.post(
                f"https://api.github.com/app-manifests/{code}/conversions",
                headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                timeout=20,
            )
            response.raise_for_status()
            self.result = response.json()
            self._finish(200, "GitHub App registration received. You may close this tab.")
        except (httpx.HTTPError, ValueError) as exc:
            self.error = f"GitHub App registration exchange failed: {type(exc).__name__}"
            self._finish(502, self.error)

    def _finish(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode())
        self.done.set()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap an AgentDock development GitHub App")
    parser.add_argument("--no-open", action="store_true", help="print the local start URL instead of opening it")
    parser.add_argument("--name", default="", help="override the generated GitHub App name")
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
    handler = server.RequestHandlerClass
    handler.state = state
    handler.manifest = json.dumps(
        build_manifest(
            redirect_url=f"http://127.0.0.1:{server.server_port}/callback",
            app_name=args.name or f"AgentDock Dev {secrets.token_hex(3)}",
            webhook_url=args.webhook_url or None,
        ),
        separators=(",", ":"),
    )
    handler.done = threading.Event()
    start_url = f"http://127.0.0.1:{server.server_port}/start"
    print("Open the local URL to register the development GitHub App:")
    print(start_url)
    if not args.no_open:
        webbrowser.open(start_url)
    server.timeout = 1
    while not handler.done.wait(0.1):
        server.handle_request()
    server.server_close()
    if handler.error:
        raise SystemExit(handler.error)
    if handler.result is None:
        raise SystemExit("GitHub App registration returned no credentials")
    slug = persist_credentials(handler.result)
    print(f"GitHub App credentials saved locally for slug: {slug}")
    print("Install it only on the disposable agentdock-live-test repository:")
    print(f"https://github.com/apps/{slug}/installations/new")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
