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


def postgres_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


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
