"""Panel CORS is limited to PANEL_ORIGINS and never touches /v1/widget/*."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.main import create_app
from site_ai_widget.db.pool import close_pool
from tests.conftest import postgres_available

PANEL = "http://127.0.0.1:5173"
OTHER = "https://shop.example"

_CORS_NAMES = (
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
    "access-control-max-age",
)


def _client():
    clear_settings_cache()
    close_pool()
    return TestClient(create_app())


def _assert_panel_cors(response, origin: str) -> None:
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-origin"] != "*"
    assert "access-control-allow-credentials" not in response.headers
    methods = {m.strip() for m in response.headers["access-control-allow-methods"].split(",")}
    assert {"GET", "POST", "PATCH", "DELETE", "OPTIONS"} <= methods
    headers = {h.strip().lower() for h in response.headers["access-control-allow-headers"].split(",")}
    assert "authorization" in headers
    assert "content-type" in headers
    assert "origin" in response.headers["vary"].lower()


def _assert_no_cors(response) -> None:
    for name in _CORS_NAMES:
        assert name not in response.headers


def test_panel_origin_gets_cors_on_health():
    with _client() as client:
        response = client.get("/health", headers={"Origin": PANEL})
    close_pool()
    clear_settings_cache()
    assert response.status_code == 200
    _assert_panel_cors(response, PANEL)


def test_other_origin_gets_no_cors_on_health():
    with _client() as client:
        response = client.get("/health", headers={"Origin": OTHER})
    close_pool()
    clear_settings_cache()
    assert response.status_code == 200
    _assert_no_cors(response)


def test_panel_preflight_on_login():
    with _client() as client:
        allowed = client.options(
            "/auth/login",
            headers={
                "Origin": PANEL,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        denied = client.options(
            "/auth/login",
            headers={
                "Origin": OTHER,
                "Access-Control-Request-Method": "POST",
            },
        )
    close_pool()
    clear_settings_cache()
    assert allowed.status_code == 204
    _assert_panel_cors(allowed, PANEL)
    assert denied.status_code == 204
    _assert_no_cors(denied)


@pytest.mark.skipif(not postgres_available(), reason="Postgres is not reachable (docker compose up -d db)")
def test_widget_path_does_not_get_panel_cors():
    """A panel origin on /v1/widget/* is still the widget CORS rules, not PANEL_ORIGINS."""
    with _client() as client:
        response = client.options(
            "/v1/widget/wk_unknown_example_key_0001/ask",
            headers={"Origin": PANEL, "Access-Control-Request-Method": "POST"},
        )
    close_pool()
    clear_settings_cache()
    # Unknown key → widget middleware 401, and never the panel method list.
    assert response.status_code == 401
    _assert_no_cors(response)
    assert "GET" not in (response.headers.get("access-control-allow-methods") or "")


def test_widget_keys_without_jwt_is_401():
    with _client() as client:
        response = client.get("/widget-keys", headers={"Origin": PANEL})
    close_pool()
    clear_settings_cache()
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_invalid"
    _assert_panel_cors(response, PANEL)


@pytest.mark.parametrize("origin", [None, OTHER, PANEL])
def test_vary_origin_on_every_panel_response(origin):
    """A shared cache must key on Origin even when the request had none."""
    headers = {"Origin": origin} if origin else {}
    with _client() as client:
        health = client.get("/health", headers=headers)
        script = client.get("/embed.js", headers=headers)
    close_pool()
    clear_settings_cache()
    for response in (health, script):
        assert response.status_code == 200
        assert "origin" in response.headers.get("vary", "").lower()
        if origin != PANEL:
            _assert_no_cors(response)


def test_non_canonical_panel_origin_is_refused():
    """Exact match only: a variant of a PANEL_ORIGINS entry gets no CORS."""
    with _client() as client:
        responses = [
            client.get("/health", headers={"Origin": variant})
            for variant in ("http://127.0.0.1:5173/", "HTTP://127.0.0.1:5173", "http://127.0.0.1:5174", "null")
        ]
    close_pool()
    clear_settings_cache()
    for response in responses:
        _assert_no_cors(response)
