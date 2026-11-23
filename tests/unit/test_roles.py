"""Role enforcement on panel write routes (no Postgres).

``panel_user`` is overridden with a fake member, so ``require_admin`` rejects
the request before any handler touches the database.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.deps import panel_user, require_admin
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.main import create_app


def _user(role: str | None) -> dict:
    return {"id": uuid4(), "company_id": uuid4(), "email": "u@example.com", "role": role}


def test_require_admin_allows_admin():
    user = _user("admin")
    assert require_admin(user) is user


@pytest.mark.parametrize("role", ["member", "", "Admin", None])
def test_require_admin_rejects_non_admin(role):
    with pytest.raises(ApiError) as exc:
        require_admin(_user(role))
    assert exc.value.status == 403
    assert exc.value.code == "forbidden"


@pytest.fixture
def member_client():
    app = create_app()
    app.dependency_overrides[panel_user] = lambda: _user("member")
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/company", {"name": "Renamed Co"}),
        ("PATCH", "/company/widget-settings", {"greeting": "Hi"}),
        ("POST", "/widget-keys", {"allowed_origins": []}),
        ("PATCH", f"/widget-keys/{uuid4()}", {"is_active": False}),
        ("POST", f"/widget-keys/{uuid4()}/revoke", None),
    ],
)
def test_member_gets_403_on_write_routes(member_client, method, path, body):
    r = member_client.request(method, path, json=body)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "forbidden"
