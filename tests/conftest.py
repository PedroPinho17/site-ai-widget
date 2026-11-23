"""Shared fixtures. Secrets are test-only placeholders (never production defaults in app code)."""
from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

# Prefer a local .env when present; CI has no secrets and uses the placeholders below.
load_dotenv()

# Allow importing the app / settings during unit tests without a real .env
os.environ.setdefault("JWT_SECRET", "unit-test-jwt-secret-at-least-32-chars!!")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://sitewidget:test@127.0.0.1:5435/sitewidget",
)
os.environ.setdefault("AUTH_RATE_LIMIT", "1000")
os.environ.setdefault("AUTH_RATE_LIMIT_WINDOW", "60")
os.environ.setdefault("API_RATE_LIMIT", "1000")
os.environ.setdefault("API_RATE_LIMIT_WINDOW", "60")
os.environ.setdefault("ASK_RATE_LIMIT", "1000")
os.environ.setdefault("ASK_RATE_LIMIT_WINDOW", "60")
# Unit/CI must never download the embedding model
os.environ.setdefault("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
os.environ.setdefault("UPLOAD_DIR", "data/uploads-test")

# One probe per process: skipif on many modules must not each wait on TCP.
_POSTGRES_AVAILABLE: bool | None = None
_POSTGRES_CONNECT_TIMEOUT = 1  # seconds; keep short so DB-down suites finish quickly


def postgres_available() -> bool:
    """True if DATABASE_URL answers quickly. Result is cached for the session."""
    global _POSTGRES_AVAILABLE
    if _POSTGRES_AVAILABLE is not None:
        return _POSTGRES_AVAILABLE
    try:
        import psycopg

        with psycopg.connect(
            os.environ["DATABASE_URL"],
            connect_timeout=_POSTGRES_CONNECT_TIMEOUT,
        ) as conn:
            conn.execute("SELECT 1")
        _POSTGRES_AVAILABLE = True
    except Exception:
        _POSTGRES_AVAILABLE = False
    return _POSTGRES_AVAILABLE


def reset_postgres_available_cache() -> None:
    """Tests that stop/start Postgres in-process (rare)."""
    global _POSTGRES_AVAILABLE
    _POSTGRES_AVAILABLE = None


@pytest.fixture(scope="session")
def require_postgres():
    if not postgres_available():
        pytest.skip("Postgres is not reachable (start with: docker compose up -d db)")


@pytest.fixture
def settings_env(monkeypatch):
    """Fresh settings cache between tests that tweak env."""
    from site_ai_widget.api.config import clear_settings_cache

    clear_settings_cache()
    yield monkeypatch
    clear_settings_cache()
