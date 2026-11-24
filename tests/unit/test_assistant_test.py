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
            columns=["name", "price"],
            rows=[["Azeite", price]],
            row_count=1,
            truncated=False,
            attempts=1,
            tables=["products"],
        )

    def refuse_store(*_args, **_kwargs):
        raise AssertionError("test chats must not be stored")

    monkeypatch.setattr("site_ai_widget.api.routes.assistant.answer_with_sql", fake_sql)
    monkeypatch.setattr("site_ai_widget.widget.access.append_turn", refuse_store)
    monkeypatch.setattr("site_ai_widget.ask_service._company_currency", lambda cid: "EUR")
    monkeypatch.setattr("site_ai_widget.ask_service._company_locale", lambda cid: "pt")

    holder["who"] = "a"
    first = client.post("/assistant/test", json={"question": "Qual o preço do azeite?", "lang": "pt"})
    assert first.status_code == 200
    body = first.json()
    assert body["mode"] == "sql"
    assert body["tables"] == ["products"]
    assert "8,49 €" in body["answer"]
    assert "8.49" not in body["answer"]
    assert "SELECT" not in first.text

    # Omitting lang still uses the company widget locale (pt), like the embed data-lang.
    omitted = client.post("/assistant/test", json={"question": "Qual o preço do azeite?"})
    assert omitted.status_code == 200
    assert "8,49 €" in omitted.json()["answer"]
    assert "8.49" not in omitted.json()["answer"]

    holder["who"] = "b"
    second = client.post("/assistant/test", json={"question": "Qual o preço do azeite?", "lang": "pt"})
    assert second.status_code == 200
    assert second.json()["answer"] != body["answer"]
    assert seen == [
        str(users["a"]["company_id"]),
        str(users["a"]["company_id"]),
        str(users["b"]["company_id"]),
    ]


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


# --- rate limit: per user, separate from widget visitors ---------------------


def _limits(monkeypatch, **env: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    clear_settings_cache()
    reset_limiters()


def test_ten_quick_test_messages_from_one_admin_succeed(assistant_app, monkeypatch):
    """Regression: the 2nd message returned 429 because it shared the per-IP ask bucket."""
    client, _, holder = assistant_app
    # The smallest allowed visitor ask bucket (3 slots = one /ask) must not matter here.
    _limits(monkeypatch, ASK_RATE_LIMIT="3")
    holder["who"] = "a"
    statuses = [
        client.post("/assistant/test", json={"question": "Olá", "lang": "pt"}).status_code
        for _ in range(10)
    ]
    assert statuses == [200] * 10


def test_test_limit_is_per_user_and_never_touches_the_visitor_bucket(assistant_app, monkeypatch):
    from site_ai_widget.api import deps

    client, _, holder = assistant_app
    _limits(monkeypatch, ASSISTANT_TEST_RATE_LIMIT="3")
    holder["who"] = "a"
    for _ in range(3):
        assert client.post("/assistant/test", json={"question": "Olá"}).status_code == 200
    blocked = client.post("/assistant/test", json={"question": "Olá"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in blocked.headers
    # Another admin has their own bucket.
    holder["who"] = "b"
    assert client.post("/assistant/test", json={"question": "Olá"}).status_code == 200
    # Nothing was charged to the per-IP bucket widget visitors and /ask use.
    assert deps.ask_limiter is not None
    assert not any(key.startswith("ask:") for key in deps.ask_limiter._calls)


def test_busy_widget_visitors_do_not_block_the_admin(assistant_app, monkeypatch):
    from site_ai_widget.api import deps
    from site_ai_widget.api.errors import ApiError

    client, _, holder = assistant_app
    _limits(monkeypatch, ASK_RATE_LIMIT="3")
    deps._ensure_limiters()
    # Fill the per-IP visitor bucket for the test client.
    with pytest.raises(ApiError):
        for _ in range(10):
            deps.ask_limiter.check("ask:testclient", n=3)
    holder["who"] = "a"
    assert client.post("/assistant/test", json={"question": "Olá"}).status_code == 200


def test_default_test_limit_is_thirty_per_minute():
    from site_ai_widget.api.config import Settings

    fields = Settings.model_fields
    assert fields["assistant_test_rate_limit"].default == 30
    assert fields["assistant_test_rate_limit_window"].default == 60
