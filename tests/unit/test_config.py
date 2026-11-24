"""Required settings: the app refuses to start without valid secrets (no Postgres)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from site_ai_widget.api.config import Settings

GOOD_SECRET = "unit-test-jwt-secret-at-least-32-chars!!"
GOOD_DB = "postgresql://sitewidget:test@127.0.0.1:5435/sitewidget"


@pytest.fixture
def clean_env(monkeypatch):
    """Isolate from the developer's .env and process env."""
    for name in ("JWT_SECRET", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


def _error_fields(exc: ValidationError) -> set[str]:
    return {str(e["loc"][0]) for e in exc.errors()}


def test_valid_settings_load(clean_env):
    clean_env.setenv("JWT_SECRET", GOOD_SECRET)
    clean_env.setenv("DATABASE_URL", f"  {GOOD_DB}  ")
    s = _settings()
    assert s.jwt_secret == GOOD_SECRET
    assert s.database_url == GOOD_DB
    assert s.trusted_proxy_ips == ""
    assert s.panel_origin_set() == frozenset({"http://127.0.0.1:5173", "http://localhost:5173"})


def test_panel_origins_wildcard_refused(clean_env):
    clean_env.setenv("JWT_SECRET", GOOD_SECRET)
    clean_env.setenv("DATABASE_URL", GOOD_DB)
    clean_env.setenv("PANEL_ORIGINS", "*")
    with pytest.raises(ValidationError) as exc:
        _settings()
    assert "panel_origins" in _error_fields(exc.value)


def test_missing_jwt_secret_refused(clean_env):
    clean_env.setenv("DATABASE_URL", GOOD_DB)
    with pytest.raises(ValidationError) as exc:
        _settings()
    assert "jwt_secret" in _error_fields(exc.value)


@pytest.mark.parametrize(
    "secret",
    ["", "too-short", "x" * 31, "change-me-" + "x" * 40],
)
def test_short_or_placeholder_jwt_secret_refused(clean_env, secret):
    clean_env.setenv("DATABASE_URL", GOOD_DB)
    clean_env.setenv("JWT_SECRET", secret)
    with pytest.raises(ValidationError) as exc:
        _settings()
    assert "jwt_secret" in _error_fields(exc.value)


def test_missing_database_url_refused(clean_env):
    clean_env.setenv("JWT_SECRET", GOOD_SECRET)
    with pytest.raises(ValidationError) as exc:
        _settings()
    assert "database_url" in _error_fields(exc.value)


@pytest.mark.parametrize("url", ["", "   "])
def test_blank_database_url_refused(clean_env, url):
    clean_env.setenv("JWT_SECRET", GOOD_SECRET)
    clean_env.setenv("DATABASE_URL", url)
    with pytest.raises(ValidationError) as exc:
        _settings()
    assert "database_url" in _error_fields(exc.value)
