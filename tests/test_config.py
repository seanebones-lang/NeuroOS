"""Deployment configuration safety tests."""

import pytest
from pydantic import ValidationError

from neuro_os.config import Settings


def _production_settings(**overrides: object) -> Settings:
    values = {
        "environment": "production",
        "debug": False,
        "secret_key": "a-unique-production-secret-that-is-long-enough",
        "cors_origins": "https://app.example.com, https://admin.example.com",
        "openai_api_key": "test-provider-key",
        "anthropic_api_key": None,
        "google_api_key": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_require_a_safe_configuration():
    settings = _production_settings()

    assert settings.cors_origin_list == ["https://app.example.com", "https://admin.example.com"]


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"secret_key": "too-short"}, "SECRET_KEY"),
        ({"debug": True}, "DEBUG"),
        ({"database_echo": True}, "DATABASE_ECHO"),
        ({"access_token_expire_minutes": 1441}, "ACCESS_TOKEN_EXPIRE_MINUTES"),
        ({"openai_api_key": None}, "AI provider"),
        ({"cors_origins": ""}, "CORS_ORIGINS"),
        ({"cors_origins": "*"}, "HTTPS origins"),
        ({"cors_origins": "http://app.example.com"}, "HTTPS origins"),
    ],
)
def test_production_settings_reject_unsafe_values(
    overrides: dict[str, object], expected_message: str
):
    with pytest.raises(ValidationError, match=expected_message):
        _production_settings(**overrides)


def test_development_allows_local_defaults():
    settings = Settings(environment="development", cors_origins="http://localhost:3000, http://localhost:3000")

    assert settings.cors_origin_list == ["http://localhost:3000"]
