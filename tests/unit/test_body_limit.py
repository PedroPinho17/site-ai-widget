"""Request-body cap applied before multipart parsing (no Postgres)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.body_limit import ONE_MIB, default_limit_for
from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.deps import reset_limiters


@pytest.fixture
def small_cap_client(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    clear_settings_cache()
    reset_limiters()
    from site_ai_widget.api.main import create_app

    with TestClient(create_app()) as client:
        yield client
    clear_settings_cache()
    reset_limiters()


def test_limit_for_routes():
    limit_for = default_limit_for(25 * ONE_MIB)
    assert limit_for({"method": "POST", "path": "/documents"}) == 26 * ONE_MIB
    assert limit_for({"method": "POST", "path": "/documents/"}) == 26 * ONE_MIB
    assert limit_for({"method": "POST", "path": "/auth/login"}) == ONE_MIB
    assert limit_for({"method": "PATCH", "path": "/documents"}) == ONE_MIB


def test_content_length_over_cap_rejected_before_auth(small_cap_client):
    body = b"%PDF-" + b"0" * (2 * ONE_MIB + 10)
    r = small_cap_client.post(
        "/documents",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "upload_too_large"


def test_chunked_body_over_cap_rejected(small_cap_client):
    def chunks():
        for _ in range(40):
            yield b"0" * (64 * 1024)  # 2.5 MiB total, no Content-Length

    r = small_cap_client.post(
        "/documents",
        content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "upload_too_large"


def test_json_routes_capped_at_one_mib(small_cap_client):
    r = small_cap_client.post(
        "/auth/login",
        content=b'{"email":"a@example.com","password":"' + b"x" * (ONE_MIB + 10) + b'"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413


def test_small_requests_pass_through(small_cap_client):
    assert small_cap_client.get("/health").status_code == 200
    # Under the cap: reaches auth (401 without a token), not 413
    r = small_cap_client.post(
        "/documents",
        files={"file": ("a.pdf", b"%PDF-1.4 small", "application/pdf")},
    )
    assert r.status_code == 401
