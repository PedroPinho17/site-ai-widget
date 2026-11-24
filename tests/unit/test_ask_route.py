"""POST /ask unit tests (SQL then docs fallback)."""
from __future__ import annotations

import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.deps import reset_limiters
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.main import create_app
from site_ai_widget.docs.embed import FakeEmbedder, set_embedder
from site_ai_widget.sqlqa.pipeline import SqlAnswer

SENTINEL_SELECT = "SELECT name FROM products WHERE sku = 'SENTINEL-ASK-SKU'"
SENTINEL_Q = "SENTINEL-ASK-QUESTION"
SENTINEL_ROW = "SENTINEL-ROW-ASK"


@pytest.fixture
def ask_app(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    monkeypatch.setenv("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
    clear_settings_cache()
    reset_limiters()
    set_embedder(FakeEmbedder())

    app = create_app()
    company_id = uuid4()
    user = {
        "id": uuid4(),
        "company_id": company_id,
        "email": "ask@example.com",
        "role": "member",
        "token_version": 0,
    }
    from site_ai_widget.api import auth as auth_mod

    app.dependency_overrides[auth_mod.current_user] = lambda: user

    with TestClient(app) as client:
        yield client, company_id, user

    app.dependency_overrides.clear()
    set_embedder(None)
    reset_limiters()
    clear_settings_cache()


def _docs_hit(**kwargs):
    return {
        "answer": "Aberto ao domingo.",
        "citations": [
            {
                "document_id": str(uuid4()),
                "document_name": "manual.pdf",
                "page": 1,
            }
        ],
        "matched": True,
    }


def test_ask_sql_mode(ask_app, monkeypatch):
    client, company_id, _ = ask_app

    def fake_sql(cid, question, **kwargs):
        assert str(cid) == str(company_id)
        assert "company_id" not in kwargs
        return SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[["Azeite Atlas"]],
            row_count=1,
            truncated=False,
            attempts=1,
        )

    monkeypatch.setattr("site_ai_widget.api.routes.ask.answer_with_sql", fake_sql)
    r = client.post("/ask", json={"question": "precos?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "sql"
    assert body["columns"] == ["name"]
    assert body["rows"] == [["Azeite Atlas"]]
    assert body["row_count"] == 1
    assert body["truncated"] is False
    assert body["answer"] == "name: Azeite Atlas"
    assert "SELECT" not in r.text


def test_ask_fallback_to_docs(ask_app, monkeypatch):
    client, _, _ = ask_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )
    monkeypatch.setattr("site_ai_widget.api.routes.ask.ask_documents", _docs_hit)
    r = client.post("/ask", json={"question": "domingo?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "docs"
    assert body["answer"] == "Aberto ao domingo."
    assert len(body["sources"]) == 1
    assert body["sources"][0]["page"] == 1


def test_ask_mode_none_when_both_fail(ask_app, monkeypatch):
    client, _, _ = ask_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="error", reason="timeout", attempts=1),
    )
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.ask_documents",
        lambda **kwargs: (_ for _ in ()).throw(ApiError("llm_not_configured", 503)),
    )
    r = client.post("/ask", json={"question": "anything?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "none"
    assert body["message"]
    assert "SELECT" not in body["message"]
    assert "timeout" not in body["message"]


@pytest.mark.parametrize(
    "exc",
    [
        ApiError("llm_unavailable", 503),
        RuntimeError("SECRET"),
    ],
    ids=["api_error", "runtime_secret"],
)
def test_sql_exception_falls_through_to_docs_then_none(ask_app, monkeypatch, exc):
    client, _, _ = ask_app

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr("site_ai_widget.api.routes.ask.answer_with_sql", boom)
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.ask_documents",
        lambda **kwargs: (_ for _ in ()).throw(ApiError("llm_not_configured", 503)),
    )
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "none"
    assert "SECRET" not in r.text
    assert "llm_unavailable" not in r.text


def test_docs_found_without_citation_markers_is_docs_mode(ask_app, monkeypatch):
    """Search hit text but the answer has no [n] → still mode docs, empty sources."""
    client, _, _ = ask_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.ask_documents",
        lambda **kwargs: {
            "answer": "Horario de abertura sem marcador.",
            "citations": [],
            "matched": True,
        },
    )
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "docs"
    assert body["answer"]
    assert body["sources"] == []


def test_no_docs_matched_is_mode_none(ask_app, monkeypatch):
    client, _, _ = ask_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.ask_documents",
        lambda **kwargs: {
            "answer": "I could not find relevant information in the uploaded documents.",
            "citations": [],
            "matched": False,
        },
    )
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 200
    assert r.json()["mode"] == "none"


def test_company_id_from_auth_not_body(ask_app, monkeypatch):
    client, company_id, _ = ask_app
    seen: list[str] = []

    def fake_sql(cid, question, **kwargs):
        seen.append(str(cid))
        return SqlAnswer(status="no_sql", attempts=1)

    monkeypatch.setattr("site_ai_widget.api.routes.ask.answer_with_sql", fake_sql)
    monkeypatch.setattr("site_ai_widget.api.routes.ask.ask_documents", _docs_hit)
    foreign = str(uuid4())
    r = client.post(
        "/ask",
        json={"question": "q", "company_id": foreign},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "docs"
    assert seen == [str(company_id)]
    assert foreign not in seen


def test_question_empty_422(ask_app):
    client, _, _ = ask_app
    r = client.post("/ask", json={"question": "   "})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "validation_error"


def test_question_too_long_exact_code(ask_app, monkeypatch):
    client, _, _ = ask_app
    monkeypatch.setenv("MAX_QUESTION_CHARS", "20")
    clear_settings_cache()
    r = client.post("/ask", json={"question": "x" * 50})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "question_too_long"


def test_question_1001_chars_rejected_under_default_1000(ask_app):
    client, _, _ = ask_app
    r = client.post("/ask", json={"question": "x" * 1001})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "question_too_long"


def test_hard_cap_rejects_1001_even_when_env_higher(ask_app, monkeypatch):
    client, _, _ = ask_app
    monkeypatch.setenv("MAX_QUESTION_CHARS", "5000")
    clear_settings_cache()
    r = client.post("/ask", json={"question": "x" * 1001})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "question_too_long"
    assert r.json()["detail"].get("n") == 1000


def test_500_row_cap(ask_app, monkeypatch):
    client, _, _ = ask_app
    rows = [[f"r{i}"] for i in range(600)]

    def fake_sql(cid, question, **kwargs):
        return SqlAnswer(
            status="ok",
            columns=["name"],
            rows=rows,
            row_count=600,
            truncated=False,
            attempts=1,
        )

    monkeypatch.setattr("site_ai_widget.api.routes.ask.answer_with_sql", fake_sql)
    r = client.post("/ask", json={"question": "q"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "sql"
    assert len(body["rows"]) == 500
    assert body["row_count"] == 500
    assert body["truncated"] is True
    assert isinstance(body["answer"], str)
    assert body["answer"].splitlines()[-1] == "and more than 490 more items"
    assert "SELECT" not in body["answer"]


def test_response_never_contains_sql_or_select_sentinel(ask_app, monkeypatch):
    client, _, _ = ask_app
    seen_q: list[str] = []

    def fake_sql(cid, question, **kwargs):
        seen_q.append(question)
        return SqlAnswer(status="refused", reason="forbidden_op", attempts=2)

    monkeypatch.setattr("site_ai_widget.api.routes.ask.answer_with_sql", fake_sql)
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.ask_documents",
        lambda **kwargs: (_ for _ in ()).throw(ApiError("llm_unavailable", 503)),
    )
    r = client.post("/ask", json={"question": f"{SENTINEL_Q} {SENTINEL_SELECT}"})
    assert r.status_code == 200
    assert seen_q and SENTINEL_Q in seen_q[0]
    assert "SELECT" not in r.text
    assert SENTINEL_SELECT not in r.text
    assert "forbidden_op" not in r.text


def test_route_logs_have_no_sentinels(ask_app, monkeypatch, caplog):
    client, _, _ = ask_app

    def fake_sql(cid, question, **kwargs):
        return SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[[SENTINEL_ROW]],
            row_count=1,
            truncated=False,
            attempts=1,
        )

    monkeypatch.setattr("site_ai_widget.api.routes.ask.answer_with_sql", fake_sql)
    with caplog.at_level(logging.INFO, logger="site_ai_widget"):
        r = client.post("/ask", json={"question": f"{SENTINEL_Q} {SENTINEL_SELECT}"})
    assert r.status_code == 200
    text = " ".join(rec.message for rec in caplog.records)
    assert SENTINEL_Q not in text
    assert SENTINEL_SELECT not in text
    assert SENTINEL_ROW not in text
    assert "SELECT" not in text


def test_docs_api_error_logged_code_only(ask_app, monkeypatch, caplog):
    client, _, _ = ask_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.ask_documents",
        lambda **kwargs: (_ for _ in ()).throw(ApiError("llm_unavailable", 503)),
    )
    with caplog.at_level(logging.WARNING, logger="site_ai_widget"):
        r = client.post("/ask", json={"question": SENTINEL_Q})
    assert r.status_code == 200
    assert r.json()["mode"] == "none"
    assert "minuto" in (r.json().get("answer") or "").lower() or "minute" in (r.json().get("answer") or "").lower()
    text = " ".join(rec.message for rec in caplog.records)
    assert "ask reason=llm_busy" in text
    assert "quota" not in text.lower()
    assert SENTINEL_Q not in text


def test_docs_unexpected_exception_is_mode_none_no_leak(ask_app, monkeypatch, caplog):
    client, _, _ = ask_app
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )

    def boom(**kwargs):
        raise RuntimeError(f"SECRET-{SENTINEL_Q}-{SENTINEL_SELECT}")

    monkeypatch.setattr("site_ai_widget.api.routes.ask.ask_documents", boom)
    with caplog.at_level(logging.WARNING, logger="site_ai_widget"):
        r = client.post("/ask", json={"question": SENTINEL_Q})
    assert r.status_code == 200
    assert r.json()["mode"] == "none"
    assert "SECRET" not in r.text
    assert SENTINEL_Q not in r.text
    assert SENTINEL_SELECT not in r.text
    text = " ".join(rec.message for rec in caplog.records)
    assert "ask/docs failed: RuntimeError" in text
    assert "SECRET" not in text


@pytest.fixture
def ask_rate_client(monkeypatch):
    """Tight ask rate limit + cleaned settings/limiters even if the test fails."""
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    monkeypatch.setenv("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
    clear_settings_cache()
    reset_limiters()
    set_embedder(FakeEmbedder())
    app = create_app()
    user = {
        "id": uuid4(),
        "company_id": uuid4(),
        "email": "r@example.com",
        "role": "member",
        "token_version": 0,
    }
    from site_ai_widget.api import auth as auth_mod

    app.dependency_overrides[auth_mod.current_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client, app, monkeypatch
    finally:
        app.dependency_overrides.clear()
        reset_limiters()
        clear_settings_cache()
        set_embedder(None)


def test_ask_rate_limit_counts_as_three(ask_rate_client):
    client, _app, monkeypatch = ask_rate_client
    monkeypatch.setenv("ASK_RATE_LIMIT", "5")
    clear_settings_cache()
    reset_limiters()
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )
    monkeypatch.setattr("site_ai_widget.api.routes.ask.ask_documents", _docs_hit)

    # 3 + 3 = 6 > 5 → second /ask is refused
    assert client.post("/ask", json={"question": "q1"}).status_code == 200
    blocked = client.post("/ask", json={"question": "q2"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"


def test_ask_docs_still_counts_as_one(ask_rate_client):
    client, _app, monkeypatch = ask_rate_client
    monkeypatch.setenv("ASK_RATE_LIMIT", "3")
    clear_settings_cache()
    reset_limiters()
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.hybrid_search",
        lambda *a, **k: [],
    )

    assert client.post("/ask/docs", json={"question": "q1"}).status_code == 200
    assert client.post("/ask/docs", json={"question": "q2"}).status_code == 200
    assert client.post("/ask/docs", json={"question": "q3"}).status_code == 200
    blocked = client.post("/ask/docs", json={"question": "q4"})
    assert blocked.status_code == 429


def test_ask_succeeds_when_limit_equals_weight(ask_rate_client):
    """With ASK_RATE_LIMIT=3, /ask (weight 3) still succeeds once."""
    client, _app, monkeypatch = ask_rate_client
    monkeypatch.setenv("ASK_RATE_LIMIT", "3")
    clear_settings_cache()
    reset_limiters()
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: SqlAnswer(
            status="ok", columns=["n"], rows=[["x"]], row_count=1, attempts=1
        ),
    )

    assert client.post("/ask", json={"question": "q1"}).status_code == 200
    blocked = client.post("/ask", json={"question": "q2"})
    assert blocked.status_code == 429
