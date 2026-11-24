"""Panel 6c: settings validation, conversations isolation, signed visitor links."""
from __future__ import annotations

from uuid import uuid4

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
    clear_settings_cache()


def _register(client: TestClient):
    slug = f"co-{uuid4().hex[:10]}"
    r = client.post(
        "/auth/register",
        json={
            "company_name": f"Company {slug}",
            "company_slug": slug,
            "email": f"{slug}@example.com",
            "password": "test-pass-12",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"], r.json()["user"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_widget_settings_reject_bad_color_and_locale(client):
    token, _ = _register(client)
    bad = client.patch(
        "/company/widget-settings",
        headers=_auth(token),
        json={"primary_color": "blue"},
    )
    assert bad.status_code == 422
    bad_locale = client.patch(
        "/company/widget-settings",
        headers=_auth(token),
        json={"locale": "fr"},
    )
    assert bad_locale.status_code == 422
    ok = client.patch(
        "/company/widget-settings",
        headers=_auth(token),
        json={
            "primary_color": "#aabbcc",
            "locale": "auto",
            "show_fullscreen": False,
            "suggested_questions": ["Qual o horário de abertura?"],
        },
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["primary_color"] == "#aabbcc"
    assert body["locale"] == "auto"
    assert body["show_fullscreen"] is False
    assert body["suggested_questions"] == ["Qual o horário de abertura?"]


def test_conversations_and_stats_are_company_scoped(client):
    token_a, user_a = _register(client)
    token_b, user_b = _register(client)
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        sid = conn.execute(
            """
            INSERT INTO visitor_sessions (company_id, token_hash, lang)
            VALUES (%s, %s, 'pt')
            RETURNING id
            """,
            (user_a["company_id"], "d" * 64),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO visitor_messages (session_id, company_id, role, text)
            VALUES (%s, %s, 'user', 'Qual o horário?')
            """,
            (sid, user_a["company_id"]),
        )
        conn.execute(
            """
            INSERT INTO visitor_messages
                (session_id, company_id, role, text, mode, unanswered, elapsed_ms)
            VALUES (%s, %s, 'assistant', 'Não sei', 'none', true, 12)
            """,
            (sid, user_a["company_id"]),
        )
    listed = client.get("/conversations", headers=_auth(token_a))
    assert listed.status_code == 200
    assert any(item["id"] == str(sid) for item in listed.json())
    other = client.get("/conversations", headers=_auth(token_b))
    assert other.status_code == 200
    assert other.json() == []
    assert client.get(f"/conversations/{sid}", headers=_auth(token_b)).status_code == 404
    stats_b = client.get("/stats/dashboard", headers=_auth(token_b)).json()
    assert stats_b["top_unanswered"] == []


def test_visitor_document_link_requires_flag_and_expires(client, monkeypatch):
    token, user = _register(client)
    from site_ai_widget.api.auth import new_widget_public_key as make_key
    from site_ai_widget.db.pool import connection

    key = make_key()
    origin = "https://shop.example"
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO widget_keys (company_id, public_key, allowed_origins)
            VALUES (%s, %s, %s)
            """,
            (user["company_id"], key, [origin]),
        )
        doc = conn.execute(
            """
            INSERT INTO documents (
                company_id, original_name, storage_name, size_bytes, sha256, status
            ) VALUES (%s, 'manual.pdf', %s, 10, %s, 'ready')
            RETURNING id
            """,
            (user["company_id"], f"{uuid4()}.pdf", uuid4().hex),
        ).fetchone()[0]
    denied = client.post(
        f"/v1/widget/{key}/document-link",
        headers={"Origin": origin},
        json={"document_id": str(doc)},
    )
    assert denied.status_code == 403
    client.patch(
        "/company/widget-settings",
        headers=_auth(token),
        json={"allow_visitor_documents": True},
    )
    linked = client.post(
        f"/v1/widget/{key}/document-link",
        headers={"Origin": origin},
        json={"document_id": str(doc), "page": 2},
    )
    assert linked.status_code == 200, linked.text
    path = linked.json()["path"]
    # Relative: the widget joins it to its API base; no Host-derived URL.
    assert path.startswith(f"/v1/signed-documents/{doc}?exp=")
    assert path.endswith("#page=2")
    assert "://" not in path
    expired = client.get(f"/v1/signed-documents/{doc}?exp=1&sig={'0' * 64}")
    assert expired.status_code == 403
    assert expired.json()["detail"]["code"] == "signed_url_expired"


def test_suggestions_cache_uses_llm_once(client, monkeypatch):
    token, user = _register(client)
    from site_ai_widget.docs import suggestions as sug

    calls = {"n": 0}

    def fake_complete(messages, temperature=0.2):
        calls["n"] += 1
        return "Qual o horário de abertura?\nOnde fica a loja?\nQue produtos vendem?"

    monkeypatch.setattr(sug, "_sample_excerpts", lambda _cid: "A loja abre ao domingo.")
    out = sug.regenerate_suggested_questions(user["company_id"], complete=fake_complete)
    assert len(out) >= 3
    assert calls["n"] == 1
    cached = client.get("/company/widget-settings", headers=_auth(token)).json()
    assert cached["suggested_questions"]
    sug.regenerate_suggested_questions(user["company_id"], complete=fake_complete)
    assert calls["n"] == 2
