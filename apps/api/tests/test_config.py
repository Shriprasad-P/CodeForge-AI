from pathlib import Path

import pytest

from app.core.config import DEFAULT_DATABASE_PASSWORD, DEFAULT_SESSION_SECRET, Settings, _env_files


def test_cors_origins_comma_separated() -> None:
    settings = Settings(cors_origins="http://localhost:3000,http://127.0.0.1:3000")
    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_json_array() -> None:
    settings = Settings(cors_origins='["http://localhost:3000"]')
    assert settings.cors_origin_list == ["http://localhost:3000"]


def test_env_files_include_cwd_dotenv() -> None:
    files = _env_files()
    assert files[-1] == ".env"
    assert all(isinstance(path, str) for path in files)


def test_development_defaults_remain_valid() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.database_url.endswith("@localhost:5433/agentdock")


def test_production_rejects_default_database_password() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL") as exc_info:
        Settings(
            _env_file=None,
            app_env="production",
            database_url=f"postgresql+asyncpg://agentdock:{DEFAULT_DATABASE_PASSWORD}@db:5432/agentdock",
            session_secret="s" * 64,
        )
    assert DEFAULT_DATABASE_PASSWORD not in str(exc_info.value)


def test_production_rejects_default_session_secret() -> None:
    with pytest.raises(ValueError, match="SESSION_SECRET") as exc_info:
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://agentdock:injected-secret@db:5432/agentdock",
            session_secret=DEFAULT_SESSION_SECRET,
        )
    assert DEFAULT_SESSION_SECRET not in str(exc_info.value)


def test_production_rejects_default_session_secret_with_whitespace() -> None:
    with pytest.raises(ValueError, match="SESSION_SECRET") as exc_info:
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://agentdock:injected-secret@db:5432/agentdock",
            session_secret=f" {DEFAULT_SESSION_SECRET} ",
        )
    assert DEFAULT_SESSION_SECRET not in str(exc_info.value)


def test_production_rejects_missing_database_credentials() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="",
            session_secret="s" * 64,
        )


def test_valid_production_configuration_passes() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://agentdock:injected-secret@db:5432/agentdock",
        session_secret="s" * 64,
    )
    assert settings.cookie_secure_flag is True


def test_production_rejects_insecure_cookie_setting() -> None:
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://agentdock:injected-secret@db:5432/agentdock",
            session_secret="s" * 64,
            cookie_secure=False,
        )


def test_postgres_host_ports_are_dev_override_only() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    base = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    dev = (repo_root / "docker-compose.dev.yml").read_text(encoding="utf-8")
    postgres_base = base.split("\n  redis:", 1)[0]
    redis_base = base.split("\n  redis:", 1)[1].split("\n  api:", 1)[0]
    assert "ports:" not in postgres_base
    assert "ports:" not in redis_base
    assert '"${POSTGRES_PORT:-5433}:5432"' in dev
    assert '"${REDIS_PORT:-6380}:6379"' in dev
    assert "APP_ENV: ${APP_ENV:?APP_ENV must be set}" in base
    assert "SESSION_SECRET: ${SESSION_SECRET:?SESSION_SECRET must be set}" in base
