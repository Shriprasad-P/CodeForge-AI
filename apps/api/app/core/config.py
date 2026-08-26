from __future__ import annotations

import json
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).resolve()
DEFAULT_DATABASE_PASSWORD = "agentdock_dev_password"
DEFAULT_SESSION_SECRET = "dev-only-change-me-agentdock-session-secret"


def _env_files() -> tuple[str, ...]:
    """Resolve .env paths for local monorepo and Docker (/app) layouts."""
    candidates: list[Path] = []
    # Monorepo checkout: apps/api/app/core/config.py → repo root and apps/api
    if len(_HERE.parents) > 4:
        candidates.append(_HERE.parents[4] / ".env")
    if len(_HERE.parents) > 2:
        candidates.append(_HERE.parents[2] / ".env")
    candidates.append(Path(".env"))
    # Preserve order; pydantic-settings ignores missing files.
    return tuple(str(path) for path in candidates)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AgentDock"
    app_env: str = "development"
    log_level: str = "INFO"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_public_url: str = "http://localhost:8000"
    # Comma-separated or JSON array string (avoid list[] so dotenv does not JSON-decode first)
    cors_origins: str = "http://localhost:3000"

    database_url: str = f"postgresql+asyncpg://agentdock:{DEFAULT_DATABASE_PASSWORD}@localhost:5433/agentdock"
    redis_url: str = "redis://localhost:6380/0"

    ready_timeout_seconds: float = 2.0

    # Auth (Phase 2) — SESSION_SECRET is only for future signed payloads; sessions use hashed tokens.
    session_secret: str = DEFAULT_SESSION_SECRET
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_cookie_name: str = "agentdock_session"
    cookie_secure: bool | None = None  # None → Secure when app_env != development
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None
    auth_rate_limit_attempts: int = 20
    auth_rate_limit_window_seconds: int = 60

    # GitHub App (Phase 3) — leave empty for local runs without GitHub configured.
    github_app_id: str = ""
    github_app_slug: str = "agentdock"
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_webhook_secret: str = ""
    github_callback_url: str = "http://localhost:8000/api/github/callback"
    github_setup_url: str = "http://localhost:8000/api/github/setup"
    github_frontend_success_url: str = "http://localhost:3000/github"
    github_api_base_url: str = "https://api.github.com"
    github_oauth_base_url: str = "https://github.com"
    github_oauth_state_ttl_seconds: int = 600
    github_http_timeout_seconds: float = 15.0

    # Sandbox / executions (Phase 4)
    sandbox_provider: str = "docker"
    sandbox_image: str = "agentdock-sandbox:local"
    sandbox_cpu_limit: float = 1.0
    sandbox_memory_limit: str = "512m"
    sandbox_pids_limit: int = 256
    sandbox_timeout_seconds: int = 120
    sandbox_max_output_bytes: int = 256_000
    sandbox_network_disabled: bool = True
    # github = clone on worker with installation token; fixture = copy local fixture (dev/CI)
    sandbox_checkout_mode: str = "fixture"
    sandbox_fixture_repo_path: str = ""
    execution_queue_key: str = "agentdock:executions"
    outbox_queue_key: str = "agentdock:outbox"
    execution_rate_limit_attempts: int = 10
    execution_rate_limit_window_seconds: int = 60
    execution_max_active_per_user: int = 3
    worker_concurrency: int = 2
    worker_reconcile_stale_seconds: int = 900
    outbox_dispatch_batch_size: int = 50
    outbox_dispatch_lease_seconds: int = 30
    outbox_worker_lease_seconds: int = 900
    outbox_dispatch_visibility_seconds: int = 60
    outbox_retry_backoff_seconds: int = 5
    outbox_max_attempts: int = 8
    outbox_reconcile_interval_seconds: int = 15
    # Command argv length / count bounds
    execution_max_command_args: int = 32
    execution_max_arg_length: int = 256

    # Agent / LLM (Phase 5)
    llm_provider: str = ""  # openai | fake | ollama
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    openai_api_base: str = "https://api.openai.com/v1"
    ollama_base_url: str = "http://localhost:11434"
    agent_queue_key: str = "agentdock:agent_runs"
    agent_max_steps: int = 20
    agent_max_tool_calls: int = 40
    agent_max_runtime_seconds: int = 600
    agent_max_context_chars: int = 48_000
    agent_max_file_read_bytes: int = 64_000
    agent_max_search_results: int = 40
    agent_max_tool_output_chars: int = 16_000
    agent_max_diff_chars: int = 80_000
    agent_max_diff_preview_chars: int = 80_000
    agent_max_publication_artifact_bytes: int = 8_000_000
    agent_max_task_chars: int = 4_000
    agent_max_active_per_user: int = 2
    agent_llm_retries: int = 2

    # Realtime WebSocket (Phase 6)
    ws_max_connections_per_user: int = 10
    ws_max_connections_per_run: int = 5
    ws_command_chunk_chars: int = 512

    # Phase 7 publication. The test remote is only honored in local/test mode.
    git_author_name: str = "AgentDock"
    git_author_email: str = "agentdock@users.noreply.github.com"
    publication_test_remote_url: str = ""
    publication_mock_prs: bool = False

    def __init__(self, **values: Any) -> None:
        super().__init__(**values)
        self.validate_production_security()

    def validate_production_security(self) -> None:
        """Fail closed before a production API or worker can initialize."""
        if self.app_env.lower().strip() not in {"production", "prod"}:
            return

        errors: list[str] = []
        database = self.database_url.strip()
        parsed = urlsplit("")
        database_hostname = ""
        database_username = ""
        try:
            parsed = urlsplit(database.replace("+asyncpg", "", 1))
            database_password = unquote(parsed.password or "").strip()
            database_name = (parsed.path or "").strip("/")
            database_hostname = parsed.hostname or ""
            database_username = parsed.username or ""
        except ValueError:
            database_password = ""
            database_name = ""
        if (
            not database
            or not database_hostname
            or not database_username
            or not database_password
            or not database_name
            or database_password == DEFAULT_DATABASE_PASSWORD
        ):
            errors.append("DATABASE_URL must contain injected production database credentials")
        if (
            not self.session_secret.strip()
            or self.session_secret.strip() == DEFAULT_SESSION_SECRET
            or len(self.session_secret) < 32
        ):
            errors.append("SESSION_SECRET must be a unique production secret of at least 32 characters")
        if self.cookie_secure is False:
            errors.append("COOKIE_SECURE cannot be false in production")
        if errors:
            raise ValueError("Invalid production security configuration: " + "; ".join(errors))

    @cached_property
    def cors_origin_list(self) -> list[str]:
        text = self.cors_origins.strip()
        if text.startswith("["):
            return [str(item) for item in json.loads(text)]
        return [part.strip() for part in text.split(",") if part.strip()]

    @property
    def cookie_secure_flag(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env.lower() not in {"development", "test", "local"}

    @property
    def sync_database_url(self) -> str:
        """Alembic/sync drivers need a non-asyncpg URL."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)

    @property
    def github_configured(self) -> bool:
        return bool(
            self.github_app_id
            and self.github_app_client_id
            and self.github_app_client_secret
            and (self.github_app_private_key or self.github_app_private_key_path)
        )

    @property
    def github_webhook_configured(self) -> bool:
        return self.github_configured and bool(self.github_webhook_secret)

    @property
    def agent_configured(self) -> bool:
        provider = self.llm_provider.lower().strip()
        if provider == "fake":
            return True
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "ollama":
            return bool(self.ollama_base_url)
        return False

    def github_private_key_pem(self) -> str:
        raw = self.github_app_private_key.strip()
        if raw:
            return raw.replace("\\n", "\n")
        if self.github_app_private_key_path:
            return Path(self.github_app_private_key_path).expanduser().read_text(encoding="utf-8")
        raise ValueError("GitHub App private key is not configured")

@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
