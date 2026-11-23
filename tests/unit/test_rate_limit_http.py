"""HTTP-level auth rate limiting (strict on login)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres is not reachable",
)


def test_login_rate_limited(monkeypatch):
    monkeypatch.setenv("AUTH_RATE_LIMIT", "3")
    monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW", "60")

    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.pool import close_pool

    clear_settings_cache()
    reset_limiters()
    close_pool()

    with TestClient(create_app()) as client:
        for _ in range(3):
            r = client.post(
                "/auth/login",
                json={"email": "rate@example.com", "password": "whatever12"},
            )
            assert r.status_code == 401
        blocked = client.post(
            "/auth/login",
            json={"email": "rate@example.com", "password": "whatever12"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "rate_limited"
        assert "Retry-After" in blocked.headers

    close_pool()
    reset_limiters()
    clear_settings_cache()
