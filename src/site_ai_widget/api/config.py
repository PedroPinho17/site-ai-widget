"""Application settings from the environment (pydantic-settings).

Required secrets have no defaults: the process refuses to start without them.
"""
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from site_ai_widget.api.schemas import parse_panel_origins


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: str = Field(..., min_length=1, description="Postgres connection URL")
    jwt_secret: str = Field(..., min_length=32, description="HS256 signing key (min 32 chars)")
    app_port: int = Field(default=8002, ge=1, le=65535)
    enable_docs: bool = False
    jwt_hours: int = Field(default=8, ge=1, le=168)
    # Comma-separated origins of the admin panel. Never "*", never credentials.
    panel_origins: str = Field(default="http://127.0.0.1:5173,http://localhost:5173")

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
    # Minimum 3 so /ask (weight 3) can succeed even on a fresh bucket.
    ask_rate_limit: int = Field(default=20, ge=3)
    ask_rate_limit_window: int = Field(default=600, ge=1)
    # Per public widget key. At least 3, same floor as the ask bucket.
    widget_key_rate_limit: int = Field(default=60, ge=3)
    widget_key_rate_limit_window: int = Field(default=60, ge=1)
    # Per IP, every /v1/widget/* request (preflight included), charged before the
    # key/origin database lookup so random keys cannot tie up the pool.
    widget_lookup_rate_limit: int = Field(default=300, ge=1)
    widget_lookup_rate_limit_window: int = Field(default=60, ge=1)
    # Seed (and similar tooling) only resets "processing" rows older than this,
    # so a live API worker is not interrupted mid-job.
    document_stuck_processing_seconds: int = Field(default=180, ge=30, le=86_400)

    demo_password_atlas: str | None = None
    demo_password_lima: str | None = None

    # Phase 3: Fernet key that encrypts saved client DB connections (DSNs).
    # Optional like the Groq keys: without it the connection routes return 503.
    connections_key: str | None = None

    @field_validator("connections_key")
    @classmethod
    def connections_key_valid(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        try:
            Fernet(value.encode())
        except (ValueError, TypeError):
            raise ValueError(
                "CONNECTIONS_KEY must be a Fernet key (generate with Fernet.generate_key())."
            ) from None
        return value

    # Phase 3: private/local "host:port" pairs the connection test may still reach,
    # e.g. the demo client DB (127.0.0.1:5436 locally, clientdb:5432 inside Docker).
    clientdb_allowed_hosts: str = ""

    @field_validator("clientdb_allowed_hosts")
    @classmethod
    def clientdb_allowed_hosts_valid(cls, value: str) -> str:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            host, sep, port = item.rpartition(":")
            if not sep or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError("CLIENTDB_ALLOWED_HOSTS must be a comma-separated list of host:port.")
        return value

    @field_validator("panel_origins")
    @classmethod
    def panel_origins_valid(cls, value: str) -> str:
        parse_panel_origins(value)
        return value

    def panel_origin_set(self) -> frozenset[str]:
        """Canonical panel origins that may call the API from a browser."""
        return frozenset(parse_panel_origins(self.panel_origins))

    def clientdb_allowlist(self) -> frozenset[tuple[str, int]]:
        """CLIENTDB_ALLOWED_HOSTS as (host, port) pairs, host in lowercase."""
        pairs: set[tuple[str, int]] = set()
        for item in self.clientdb_allowed_hosts.split(","):
            host, _, port = item.strip().rpartition(":")
            if host and port.isdigit():
                pairs.add((host.lower(), int(port)))
        return frozenset(pairs)

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
