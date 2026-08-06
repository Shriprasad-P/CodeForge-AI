from __future__ import annotations

import json
from functools import cached_property, lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT_ENV = Path(__file__).resolve().parents[4] / ".env"
_LOCAL_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_ROOT_ENV), str(_LOCAL_ENV), ".env"),
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

    database_url: str = (
        "postgresql+asyncpg://agentdock:agentdock_dev_password@localhost:5432/agentdock"
    )
    redis_url: str = "redis://localhost:6379/0"

    ready_timeout_seconds: float = 2.0

    @cached_property
    def cors_origin_list(self) -> list[str]:
        text = self.cors_origins.strip()
        if text.startswith("["):
            return [str(item) for item in json.loads(text)]
        return [part.strip() for part in text.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
