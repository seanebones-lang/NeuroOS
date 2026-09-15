"""NeuroOS core configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


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
    debug: bool = False
    log_level: str = "INFO"

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
    access_token_expire_minutes: int = 30 * 24 * 60  # 30 days

    # AI Providers
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

    # Default models
    default_model: str = "gpt-4o-mini"
    fast_model: str = "gpt-4o-mini"
    heavy_model: str = "gpt-4o"
    embed_model: str = "text-embedding-3-small"

    # Scheduler
    scheduler_timezone: str = "America/Chicago"

    # Email (for notifications)
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    email_from: Optional[str] = None


settings = Settings()
