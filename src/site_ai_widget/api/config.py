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

    # Phase 2: documents + RAG
    upload_dir: str = Field(default="data/uploads")
    max_upload_mb: int = Field(default=25, ge=1, le=200)
    max_pdf_pages: int = Field(default=300, ge=1, le=5000)
    max_question_chars: int = Field(default=1000, ge=1, le=10_000)
    embedding_model: str = Field(default="intfloat/multilingual-e5-small")
    groq_api_key: str | None = None
    groq_api_key_fallback: str | None = None
    llm_base_url: str = Field(default="https://api.groq.com/openai/v1")
    llm_model: str = Field(default="openai/gpt-oss-120b")
    llm_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    ask_rate_limit: int = Field(default=20, ge=1)
    ask_rate_limit_window: int = Field(default=600, ge=1)

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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def groq_keys(self) -> list[str]:
        keys: list[str] = []
        for raw in (self.groq_api_key, self.groq_api_key_fallback):
            if raw and raw.strip():
                keys.append(raw.strip())
        return keys


@lru_cache
def get_settings() -> Settings:
    """Load settings once; raises ValidationError if required secrets are missing."""
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
