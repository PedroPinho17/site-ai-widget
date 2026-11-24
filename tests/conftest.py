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


@pytest.fixture(scope="session", autouse=True)
def _drop_leftover_noise_schema():
    """A killed run can leave the 300 zz_noise tables on the demo client DB; they would
    change other tests' table counts. Drop them once at the start. Quietly does nothing
    when the demo client DB (or its owner password) is not available, e.g. in CI.
    """
    owner_pw = os.environ.get("CLIENTDB_PASSWORD")
    if owner_pw:
        try:
            import psycopg

            with psycopg.connect(
                host="127.0.0.1",
                port=5436,
                dbname="atlas",
                user="atlas_owner",
                password=owner_pw,
                connect_timeout=_POSTGRES_CONNECT_TIMEOUT,
                autocommit=True,
            ) as conn:
                conn.execute("DROP SCHEMA IF EXISTS zz_noise CASCADE")
        except Exception:
            pass
    yield


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


@pytest.fixture
def demo_key_restored():
    """Snapshot the public demo widget key and put it back after the test.

    Tests that move ``wk_demo_mercearia_atlas`` onto a throwaway company must not
    leave it there (the Atlas admin would lose it) and must never create the
    real Mercearia Atlas company themselves (the seed would then skip its admin).
    """
    from site_ai_widget.db.pool import close_pool, connection
    from site_ai_widget.seed import DEMO_WIDGET_KEY

    close_pool()
    with connection() as conn:
        before = conn.execute(
            "SELECT company_id, allowed_origins, is_active FROM widget_keys WHERE public_key = %s",
            (DEMO_WIDGET_KEY,),
        ).fetchone()
    yield
    close_pool()
    with connection() as conn:
        if before is None:
            conn.execute("DELETE FROM widget_keys WHERE public_key = %s", (DEMO_WIDGET_KEY,))
        else:
            conn.execute(
                """
                UPDATE widget_keys SET company_id = %s, allowed_origins = %s, is_active = %s
                WHERE public_key = %s
                """,
                (before[0], before[1], before[2], DEMO_WIDGET_KEY),
            )
    close_pool()


@pytest.fixture(autouse=True)
def _no_live_llm(request, monkeypatch):
    """Unit tests never call the real LLM provider (network, cost, nondeterminism).

    ``.env`` may hold real keys for the running app; every test starts with none.
    A test that needs a key sets one itself. Live checks live in tests/integration
    and opt in with LIVE_DEMO=1.
    """
    if request.node.get_closest_marker("integration"):
        yield
        return
    from site_ai_widget.api.config import clear_settings_cache

    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    clear_settings_cache()
    yield
    clear_settings_cache()
