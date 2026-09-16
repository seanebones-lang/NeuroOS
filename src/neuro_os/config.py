"""NeuroOS core configuration and deployment safety checks."""

from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "neuro-os"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    cors_origins: str = ""
    registration_rate_limit: int = Field(default=5, ge=1)
    login_rate_limit: int = Field(default=10, ge=1)
    auth_rate_limit_window_seconds: int = Field(default=900, ge=1)
    protocol_rate_limit: int = Field(default=20, ge=1)
    protocol_rate_limit_window_seconds: int = Field(default=3600, ge=1)

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://neuro:neuro@localhost:5432/neuro",
        description="PostgreSQL async connection string",
    )
    database_echo: bool = False

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string",
    )

    # Security
    secret_key: str = Field(
        default="dev-secret-change-in-production",
        description="JWT signing secret",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=24 * 60, ge=1)  # 24 hours

    # AI Providers
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None

    # Default models
    default_model: str = "gpt-4o-mini"
    fast_model: str = "gpt-4o-mini"
    heavy_model: str = "gpt-4o"
    embed_model: str = "text-embedding-3-small"

    # Scheduler
    scheduler_timezone: str = "America/Chicago"

    # Email (for notifications)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        """Return configured browser origins with whitespace and duplicates removed."""
        origins = (origin.strip() for origin in self.cors_origins.split(","))
        return list(dict.fromkeys(origin for origin in origins if origin))

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Reject unsafe production configuration before a service starts accepting traffic."""
        if self.environment != "production":
            return self

        minimum_secret_length = 32
        placeholder_secrets = {
            "dev-secret-change-in-production",
            "your-super-secret-key-change-in-production-min-32-chars",
        }
        if self.secret_key in placeholder_secrets or len(self.secret_key) < minimum_secret_length:
            raise ValueError(
                "production SECRET_KEY must be a non-placeholder value of at least 32 characters"
            )
        if self.debug:
            raise ValueError("DEBUG must be false in production")
        if self.database_echo:
            raise ValueError("DATABASE_ECHO must be false in production")
        if self.access_token_expire_minutes > 24 * 60:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must not exceed 24 hours in production")
        if not any((self.openai_api_key, self.anthropic_api_key, self.google_api_key)):
            raise ValueError("production requires at least one configured AI provider key")
        if not self.cors_origin_list:
            raise ValueError("production CORS_ORIGINS must name at least one HTTPS browser origin")

        for origin in self.cors_origin_list:
            parsed = urlparse(origin)
            if origin == "*" or parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("production CORS_ORIGINS entries must be explicit HTTPS origins")
        return self


settings = Settings()
