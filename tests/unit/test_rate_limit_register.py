"""Strict per-IP rate limit on POST /auth/register.

The dependency-level test needs no Postgres; the HTTP test skips without it.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tests.conftest import postgres_available


@pytest.fixture
def limited(monkeypatch):
    monkeypatch.setenv("AUTH_RATE_LIMIT", "3")
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW", "60")
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)

    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.db.pool import close_pool

    clear_settings_cache()
    reset_limiters()
    close_pool()
    yield
    close_pool()
    reset_limiters()
    clear_settings_cache()


def _req(peer: str, headers: dict[str, str] | None = None):
    req = MagicMock()
    req.client = MagicMock(host=peer)
    req.headers = headers or {}
    return req


def test_auth_rate_dependency_blocks_spoofed_headers_from_bridge(limited):
    """Rotating CF-Connecting-IP from the Docker gateway must not reset the budget."""
    from site_ai_widget.api.deps import require_auth_rate
    from site_ai_widget.api.errors import ApiError

    for i in range(3):
        require_auth_rate(_req("172.18.0.1", {"cf-connecting-ip": f"198.51.100.{i}"}))
    with pytest.raises(ApiError) as exc:
        require_auth_rate(_req("172.18.0.1", {"cf-connecting-ip": "198.51.100.99"}))
    assert exc.value.status == 429


@pytest.mark.skipif(not postgres_available(), reason="Postgres is not reachable")
def test_register_rate_limited(limited):
    from site_ai_widget.api.main import create_app

    def body():
        slug = f"rl-{uuid.uuid4().hex[:10]}"
        return {
            "company_name": f"Rate {slug}",
            "company_slug": slug,
            "email": f"{slug}@example.com",
            "password": "test-pass-12",
        }

    with TestClient(create_app()) as client:
        for _ in range(3):
            r = client.post("/auth/register", json=body())
            assert r.status_code == 200, r.text
        blocked = client.post("/auth/register", json=body())
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "rate_limited"
        assert "Retry-After" in blocked.headers
