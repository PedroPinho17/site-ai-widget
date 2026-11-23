"""Health endpoint does not require Postgres."""
from __future__ import annotations

from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.main import create_app
from site_ai_widget.db.pool import close_pool


def test_health_ok():
    clear_settings_cache()
    close_pool()
    with TestClient(create_app()) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
    close_pool()
