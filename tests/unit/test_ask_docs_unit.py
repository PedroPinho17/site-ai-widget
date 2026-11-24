""" /ask/docs unit behaviour without Groq or a real model."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.deps import panel_user, reset_limiters
from site_ai_widget.api.main import create_app
from site_ai_widget.docs.embed import FakeEmbedder, set_embedder


@pytest.fixture
def ask_client(monkeypatch):
    # Empty env overrides values from .env (delenv alone would fall back to the file).
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
        "email": "a@example.com",
        "role": "admin",
        "token_version": 0,
    }
    app.dependency_overrides[panel_user] = lambda: user
    # ask_docs uses current_user + require_ask_rate, not panel_user
    from site_ai_widget.api import auth as auth_mod

    app.dependency_overrides[auth_mod.current_user] = lambda: user

    with TestClient(app) as client:
        yield client, company_id

    app.dependency_overrides.clear()
    set_embedder(None)
    reset_limiters()
    clear_settings_cache()


def test_ask_docs_overlong_question_422(ask_client, monkeypatch):
    client, _ = ask_client
    monkeypatch.setenv("MAX_QUESTION_CHARS", "20")
    clear_settings_cache()
    r = client.post("/ask/docs", json={"question": "x" * 50})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] in ("question_too_long", "validation_error")


def test_ask_docs_503_without_groq_when_chunks_exist(ask_client, monkeypatch):
    client, _company_id = ask_client
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    clear_settings_cache()

    fake_chunks = [
        {
            "n": 1,
            "id": uuid4(),
            "document_id": uuid4(),
            "document_name": "manual.pdf",
            "page": 1,
            "chunk_index": 0,
            "content": "Aberto ao domingo das 10 as 14.",
        }
    ]

    monkeypatch.setattr(
        "site_ai_widget.docs.ask.hybrid_search",
        lambda *a, **k: fake_chunks,
    )
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.ensure_documents_in_company",
        lambda *a, **k: None,
    )

    r = client.post("/ask/docs", json={"question": "Estao abertos ao domingo?"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "llm_not_configured"


def test_ask_rate_limit(monkeypatch):
    monkeypatch.setenv("ASK_RATE_LIMIT", "3")
    monkeypatch.setenv("ASK_RATE_LIMIT_WINDOW", "60")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
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
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.hybrid_search",
        lambda *a, **k: [],
    )

    try:
        with TestClient(app) as client:
            assert client.post("/ask/docs", json={"question": "q1"}).status_code == 200
            assert client.post("/ask/docs", json={"question": "q2"}).status_code == 200
            assert client.post("/ask/docs", json={"question": "q3"}).status_code == 200
            blocked = client.post("/ask/docs", json={"question": "q4"})
            assert blocked.status_code == 429
            assert blocked.json()["detail"]["code"] == "rate_limited"
    finally:
        app.dependency_overrides.clear()
        reset_limiters()
        clear_settings_cache()
        set_embedder(None)
