from app.core.config import Settings


def test_cors_origins_comma_separated() -> None:
    settings = Settings(cors_origins="http://localhost:3000,http://127.0.0.1:3000")
    assert settings.cors_origin_list == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_json_array() -> None:
    settings = Settings(cors_origins='["http://localhost:3000"]')
    assert settings.cors_origin_list == ["http://localhost:3000"]
