from app.core.config import Settings, _env_files


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
