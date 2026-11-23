"""Application settings from the environment (pydantic-settings).

Required secrets have no defaults: the process refuses to start without them.
"""
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(..., min_length=1, description="Postgres connection URL")
    jwt_secret: str = Field(..., min_length=32, description="HS256 signing key (min 32 chars)")
    app_port: int = Field(default=8002, ge=1, le=65535)
    enable_docs: bool = False
    jwt_hours: int = Field(default=8, ge=1, le=168)

    # Comma-separated IPs/CIDRs of reverse proxies whose forwarding headers we trust.
    # Empty string → loopback only (127.0.0.1, ::1).
    trusted_proxy_ips: str = ""

    auth_rate_limit: int = Field(default=10, ge=1)
    auth_rate_limit_window: int = Field(default=300, ge=1)
    api_rate_limit: int = Field(default=120, ge=1)
    api_rate_limit_window: int = Field(default=60, ge=1)

    demo_password_atlas: str | None = None
    demo_password_lima: str | None = None

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_not_placeholder(cls, value: str) -> str:
        if value.startswith("change-me") or len(value) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 random characters "
                '(generate with: python -c "import secrets; print(secrets.token_urlsafe(32))").'
            )
        return value

    @field_validator("database_url")
    @classmethod
    def database_url_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL is required")
        return value.strip()


@lru_cache
def get_settings() -> Settings:
    """Load settings once; raises ValidationError if required secrets are missing."""
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
