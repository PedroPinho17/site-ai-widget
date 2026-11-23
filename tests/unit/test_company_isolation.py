"""Company isolation: cross-tenant access returns 404 (not 403).

Uses Postgres when available; skips cleanly otherwise (CI has no DB).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres is not reachable (docker compose up -d db)",
)


@pytest.fixture(scope="module")
def migrated():
    from site_ai_widget.db.migrate import migrate

    migrate()


@pytest.fixture
def client(migrated):
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.pool import close_pool

    clear_settings_cache()
    reset_limiters()
    close_pool()
    app = create_app()
    with TestClient(app) as c:
        yield c
    close_pool()
    reset_limiters()


def _register(client: TestClient, slug: str | None = None):
    slug = slug or f"co-{uuid.uuid4().hex[:10]}"
    email = f"{slug}@example.com"
    password = "test-pass-12"
    r = client.post(
        "/auth/register",
        json={
            "company_name": f"Company {slug}",
            "company_slug": slug,
            "email": email,
            "password": password,
            "default_locale": "pt",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return data["access_token"], data["user"], password


def test_auth_login_success_and_failure(client):
    token, user, password = _register(client)
    ok = client.post(
        "/auth/login",
        json={"email": user["email"], "password": password},
    )
    assert ok.status_code == 200
    assert "access_token" in ok.json()

    bad = client.post(
        "/auth/login",
        json={"email": user["email"], "password": "wrong-password"},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "bad_login"

    missing = client.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": password},
    )
    assert missing.status_code == 401


def test_token_version_revoked_after_password_change(client):
    token, user, password = _register(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200

    changed = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": password, "new_password": "new-pass-99"},
    )
    assert changed.status_code == 200
    new_token = changed.json()["access_token"]
    assert new_token != token
    assert changed.json()["user"]["email"] == user["email"]

    stale = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert stale.status_code == 401
    assert stale.json()["detail"]["code"] == "session_invalid"

    # The token returned by change-password keeps this client signed in
    still_in = client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert still_in.status_code == 200

    fresh = client.post(
        "/auth/login",
        json={"email": user["email"], "password": "new-pass-99"},
    )
    assert fresh.status_code == 200
    me2 = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {fresh.json()['access_token']}"},
    )
    assert me2.status_code == 200


def test_user_cannot_read_other_company_row(client):
    token_a, user_a, _ = _register(client)
    token_b, user_b, _ = _register(client)

    # Own company by id: OK
    own = client.get(
        f"/company/{user_a['company_id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert own.status_code == 200
    assert own.json()["id"] == user_a["company_id"]

    # Other company: 404 (not 403)
    other = client.get(
        f"/company/{user_b['company_id']}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert other.status_code == 404
    assert other.json()["detail"]["code"] == "not_found"
    assert other.status_code != 403


def test_user_cannot_read_or_revoke_other_company_keys(client):
    token_a, _, _ = _register(client)
    token_b, _, _ = _register(client)

    created = client.post(
        "/widget-keys",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"allowed_origins": ["https://oficina-lima.example"]},
    )
    assert created.status_code == 201
    key_id = created.json()["id"]
    assert created.json()["public_key"].startswith("wk_")

    # A lists only own keys (empty)
    listed = client.get(
        "/widget-keys",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert listed.status_code == 200
    assert listed.json() == []

    # A cannot patch B's key
    patched = client.patch(
        f"/widget-keys/{key_id}",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"allowed_origins": ["https://evil.example"]},
    )
    assert patched.status_code == 404

    # A cannot revoke B's key
    revoked = client.post(
        f"/widget-keys/{key_id}/revoke",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert revoked.status_code == 404

    # B still has the key active
    listed_b = client.get(
        "/widget-keys",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert listed_b.status_code == 200
    assert len(listed_b.json()) == 1
    assert listed_b.json()[0]["is_active"] is True


def _demote_to_member(user_id: str) -> None:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE id = %s", (user_id,))


def test_member_cannot_write_but_can_read(client):
    admin_token, user, _ = _register(client)
    auth_h = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        "/widget-keys", headers=auth_h, json={"allowed_origins": ["https://atlas.example"]}
    )
    assert created.status_code == 201
    key_id = created.json()["id"]

    _demote_to_member(user["id"])  # role is read from the DB on each request

    assert client.get("/company", headers=auth_h).status_code == 200
    assert client.get("/company/widget-settings", headers=auth_h).status_code == 200
    assert client.get("/widget-keys", headers=auth_h).status_code == 200

    writes = [
        client.patch("/company", headers=auth_h, json={"name": "Renamed Co"}),
        client.patch("/company/widget-settings", headers=auth_h, json={"greeting": "Hi"}),
        client.post("/widget-keys", headers=auth_h, json={"allowed_origins": []}),
        client.patch(f"/widget-keys/{key_id}", headers=auth_h, json={"is_active": False}),
        client.post(f"/widget-keys/{key_id}/revoke", headers=auth_h),
    ]
    for r in writes:
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "forbidden"

    keys = client.get("/widget-keys", headers=auth_h).json()
    assert len(keys) == 1 and keys[0]["is_active"] is True


def test_widget_key_origins_are_canonicalised_and_validated(client):
    token, _, _ = _register(client)
    auth_h = {"Authorization": f"Bearer {token}"}
    ok = client.post(
        "/widget-keys",
        headers=auth_h,
        json={"allowed_origins": ["HTTPS://Shop.Example:443/", "http://localhost:3000"]},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["allowed_origins"] == ["https://shop.example", "http://localhost:3000"]

    bad = client.post(
        "/widget-keys", headers=auth_h, json={"allowed_origins": ["https://*.example.com"]}
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "validation_error"


def test_explicit_null_fields_are_ignored_not_500(client):
    token, _, _ = _register(client)
    auth_h = {"Authorization": f"Bearer {token}"}
    r = client.patch("/company", headers=auth_h, json={"name": None})
    assert r.status_code == 200, r.text
    r = client.patch("/company/widget-settings", headers=auth_h, json={"greeting": None})
    assert r.status_code == 200, r.text
    key = client.post("/widget-keys", headers=auth_h, json={}).json()
    r = client.patch(f"/widget-keys/{key['id']}", headers=auth_h, json={"is_active": None})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True
