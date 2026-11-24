"""POST /assistant/test uses the JWT company and does not store visitor chats."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.deps import reset_limiters
from site_ai_widget.api.main import create_app
from site_ai_widget.sqlqa.pipeline import SqlAnswer
from tests.conftest import postgres_available


@pytest.fixture
def assistant_app(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    monkeypatch.setenv("ASK_RATE_LIMIT", "1000")
    monkeypatch.setenv("API_RATE_LIMIT", "1000")
    clear_settings_cache()
    reset_limiters()
    app = create_app()
    company_a = uuid4()
    company_b = uuid4()
    users = {
        "a": {
            "id": uuid4(),
            "company_id": company_a,
            "email": "a@example.com",
            "role": "member",
            "token_version": 0,
        },
        "b": {
            "id": uuid4(),
            "company_id": company_b,
            "email": "b@example.com",
            "role": "member",
            "token_version": 0,
        },
    }
    holder = {"who": "a"}
    from site_ai_widget.api import auth as auth_mod

    app.dependency_overrides[auth_mod.current_user] = lambda: users[holder["who"]]
    with TestClient(app) as client:
        yield client, users, holder
    app.dependency_overrides.clear()
    reset_limiters()
    clear_settings_cache()


def test_test_chat_is_company_scoped_and_not_stored(assistant_app, monkeypatch):
    client, users, holder = assistant_app
    seen: list[str] = []

    def fake_sql(company_id, question, **kwargs):
        seen.append(str(company_id))
        price = "8.49" if str(company_id) == str(users["a"]["company_id"]) else "1.00"
        return SqlAnswer(
            status="ok",
            columns=["price"],
            rows=[[price]],
            row_count=1,
            truncated=False,
            attempts=1,
            tables=["products"],
        )

    def refuse_store(*_args, **_kwargs):
        raise AssertionError("test chats must not be stored")

    monkeypatch.setattr("site_ai_widget.api.routes.assistant.answer_with_sql", fake_sql)
    monkeypatch.setattr("site_ai_widget.widget.access.append_turn", refuse_store)

    holder["who"] = "a"
    first = client.post("/assistant/test", json={"question": "Qual o preço do azeite?", "lang": "pt"})
    assert first.status_code == 200
    body = first.json()
    assert body["mode"] == "sql"
    assert body["tables"] == ["products"]
    assert "8,49" in body["answer"] or "8.49" in body["answer"]
    assert "SELECT" not in first.text

    holder["who"] = "b"
    second = client.post("/assistant/test", json={"question": "Qual o preço do azeite?", "lang": "pt"})
    assert second.status_code == 200
    assert second.json()["answer"] != body["answer"]
    assert seen == [str(users["a"]["company_id"]), str(users["b"]["company_id"])]


def test_greeting_on_test_endpoint_has_no_sources(assistant_app, monkeypatch):
    client, _, holder = assistant_app
    holder["who"] = "a"

    def boom(*_a, **_k):
        raise AssertionError("greeting must not look up data")

    monkeypatch.setattr("site_ai_widget.api.routes.assistant.answer_with_sql", boom)
    monkeypatch.setattr("site_ai_widget.api.routes.assistant.ask_documents", boom)
    response = client.post("/assistant/test", json={"question": "Bom dia", "lang": "pt"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "chat"
    assert body["sources"] is None
    assert body["tables"] is None or body["tables"] == []
    assert "ajudar" in body["answer"]


def test_test_endpoint_requires_a_jwt():
    clear_settings_cache()
    reset_limiters()
    with TestClient(create_app()) as client:
        response = client.post("/assistant/test", json={"question": "Olá", "lang": "pt"})
    clear_settings_cache()
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_invalid"


@pytest.mark.skipif(not postgres_available(), reason="Postgres is not reachable (docker compose up -d db)")
def test_test_chats_are_not_stored_as_visitor_conversations(assistant_app, monkeypatch):
    from site_ai_widget.db.pool import connection

    client, users, holder = assistant_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.assistant.answer_with_sql",
        lambda *a, **k: SqlAnswer(
            status="ok", columns=["price"], rows=[[Decimal("8.49")]], row_count=1, truncated=False
        ),
    )

    def counts():
        with connection() as conn:
            return (
                conn.execute("SELECT count(*) FROM visitor_sessions").fetchone()[0],
                conn.execute("SELECT count(*) FROM visitor_messages").fetchone()[0],
            )

    before = counts()
    holder["who"] = "a"
    for question in ("Qual o preço do azeite?", "Olá"):
        assert client.post("/assistant/test", json={"question": question, "lang": "pt"}).status_code == 200
    assert counts() == before
