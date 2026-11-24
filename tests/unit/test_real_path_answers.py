"""Regression tests on the same path the running app takes for the 3 demo questions.

POST /assistant/test -> answer_question -> real presenter / real ask_documents,
with the LLM provider call replaced by the replies the real model produced on the
Docker stack (narrow no-break spaces, zero-width spaces, non-breaking hyphens).
Rows are shaped like the runner returns them (Decimal arrives as a string).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.deps import reset_limiters
from site_ai_widget.api.main import create_app
from site_ai_widget.sqlqa.pipeline import SqlAnswer

NNBSP = " "
ZWSP = "​"
NB_HYPHEN = "‑"
MANUAL = Path(__file__).resolve().parents[2] / "demo" / "mercearia-atlas-manual.pdf"


@pytest.fixture
def api(monkeypatch):
    # A key must exist so the presenter takes the LLM branch, as in production;
    # the provider call itself is replaced per test.
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ASK_RATE_LIMIT", "1000")
    monkeypatch.setenv("API_RATE_LIMIT", "1000")
    clear_settings_cache()
    reset_limiters()
    monkeypatch.setattr("site_ai_widget.ask_service._company_currency", lambda cid: "EUR")
    monkeypatch.setattr("site_ai_widget.ask_service._company_locale", lambda cid: "pt")
    app = create_app()
    user = {"id": uuid4(), "company_id": uuid4(), "email": "admin@mercearia-atlas.example",
            "role": "admin", "token_version": 0}
    from site_ai_widget.api import auth as auth_mod

    app.dependency_overrides[auth_mod.current_user] = lambda: user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    reset_limiters()
    clear_settings_cache()


def _llm(monkeypatch, reply: str) -> list[list[dict]]:
    calls: list[list[dict]] = []

    def complete(messages, *, temperature=0):
        calls.append(messages)
        return reply

    monkeypatch.setattr("site_ai_widget.llm.client.complete_chat", complete)
    monkeypatch.setattr("site_ai_widget.docs.ask.complete_chat", complete, raising=False)
    return calls


def _sql(monkeypatch, columns, rows, tables=("products",)):
    monkeypatch.setattr(
        "site_ai_widget.api.routes.assistant.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="ok", columns=columns, rows=rows, row_count=len(rows),
                                  truncated=False, attempts=1, tables=list(tables)),
    )


def _ask(client, question):
    response = client.post("/assistant/test", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("price", ["8.49", Decimal("8.49"), 8.49])
def test_azeite_natural_sentence_with_the_real_model_reply(api, monkeypatch, price):
    """The real reply uses U+202F ("8,49 €", "750 ml"); it used to be refused."""
    _sql(monkeypatch, ["name", "price"], [["Azeite Atlas Premium 750 ml", price]])
    calls = _llm(monkeypatch, f"O Azeite Atlas Premium 750{NNBSP}ml custa 8,49{NNBSP}€.")
    body = _ask(api, "Qual o preço do azeite?")
    assert calls, "the LLM branch must run, as on the running app"
    assert body["mode"] == "sql"
    assert body["answer"] == "O Azeite Atlas Premium 750 ml custa 8,49 €."
    assert NNBSP not in body["answer"]


@pytest.mark.parametrize("price", ["8.49", Decimal("8.49"), 8.49])
@pytest.mark.parametrize(
    "reply",
    ["O Azeite Atlas Premium 750 ml custa 8.49 €.", "O Azeite Atlas Premium 750 ml custa 8,49€."],
)
def test_azeite_wrong_format_falls_back_to_pt_price(api, monkeypatch, price, reply):
    _sql(monkeypatch, ["name", "price"], [["Azeite Atlas Premium 750 ml", price]])
    _llm(monkeypatch, reply)
    body = _ask(api, "Qual o preço do azeite?")
    assert body["answer"] == "Azeite Atlas Premium 750 ml — 8,49 €"


def test_queijos_list_from_products_with_pt_prices_and_no_documents(api, monkeypatch):
    _sql(
        monkeypatch,
        ["name", "price"],
        [["Queijo da Serra da Estrela 200 g", "9.90"], ["Queijo de ovelha curado 300 g", "7.50"]],
    )
    _llm(
        monkeypatch,
        f"Temos Queijo da Serra da Estrela 200{NNBSP}g por 9,90{NNBSP}€ e "
        f"Queijo de ovelha curado 300{NNBSP}g por 7,50{NNBSP}€.",
    )

    def docs_must_not_run(**kwargs):
        raise AssertionError("documents are only a fallback when SQL returns no rows")

    monkeypatch.setattr("site_ai_widget.api.routes.assistant.ask_documents", docs_must_not_run)
    body = _ask(api, "Que queijos têm?")
    assert body["mode"] == "sql"
    assert not body.get("sources")
    assert "9,90 €" in body["answer"] and "7,50 €" in body["answer"]
    assert "9.90" not in body["answer"] and "7.50" not in body["answer"]
    # Prices read like every other answer: "custa 9,90 €", never "por 9,90 €".
    assert body["answer"] == (
        "Queijo da Serra da Estrela 200 g custa 9,90 € e Queijo de ovelha curado 300 g custa 7,50 €."
    )
    assert " por " not in body["answer"]


def _manual_chunks():
    from site_ai_widget.docs.chunk import chunk_pages
    from site_ai_widget.docs.process import extract_pages

    out = []
    for n, chunk in enumerate(chunk_pages(extract_pages(MANUAL)), start=1):
        out.append({
            "n": n, "id": uuid4(), "document_id": uuid4(),
            "document_name": "mercearia-atlas-manual.pdf",
            "page": getattr(chunk, "page", 1), "chunk_index": n - 1,
            "content": getattr(chunk, "text", str(chunk)),
        })
    return out


@pytest.mark.parametrize(
    "glue",
    [NNBSP, " ", " ", ZWSP, "", f"{ZWSP}⁠"],
    ids=["narrow-nbsp", "nbsp", "thin", "zero-width", "none", "zero-width-joiner"],
)
def test_contact_address_keeps_its_space_through_the_api(api, monkeypatch, glue):
    monkeypatch.setattr(
        "site_ai_widget.api.routes.assistant.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
    )
    monkeypatch.setattr("site_ai_widget.docs.ask.hybrid_search", lambda *a, **k: _manual_chunks()[:3])
    _llm(
        monkeypatch,
        f"Pode contactar{NB_HYPHEN}nos na Rua das Amendoeiras{glue}48, 1170{NB_HYPHEN}000 Lisboa. [1]",
    )
    body = _ask(api, "Como posso contactar-vos?")
    assert body["mode"] == "docs"
    assert "Rua das Amendoeiras 48" in body["answer"]
    assert "Amendoeiras48" not in body["answer"]
    assert "1170-000" in body["answer"]
    for ch in (NNBSP, " ", " ", ZWSP, "⁠", NB_HYPHEN):
        assert ch not in body["answer"]
    assert body.get("sources")


def test_list_reply_has_no_space_before_line_breaks(api, monkeypatch):
    """Seen on the running stack: "… 9,90 €. \nTemos …"."""
    _sql(
        monkeypatch,
        ["name", "price"],
        [["Queijo da Serra da Estrela 200 g", "9.90"], ["Queijo de ovelha curado 300 g", "7.50"]],
    )
    _llm(
        monkeypatch,
        "Temos o Queijo da Serra da Estrela 200 g por 9,90 €. \n"
        "Temos o Queijo de ovelha curado 300 g por 7,50 €.",
    )
    body = _ask(api, "Que queijos têm?")
    assert body["answer"] == (
        "O Queijo da Serra da Estrela 200 g custa 9,90 €.\n"
        "O Queijo de ovelha curado 300 g custa 7,50 €."
    )


# --- Reported in the Test assistant -----------------------------------------
UNKNOWN_PT = "Não consegui responder a isso."


@pytest.mark.parametrize(
    "presenter_reply",
    [
        "A Mercearia Atlas - Arroios não tem informação sobre entregas ao domingo.",
        "NO_ANSWER",
    ],
    ids=["model_says_no_information", "model_flags_no_answer"],
)
def test_sql_rows_that_do_not_answer_fall_through_to_unknown(api, monkeypatch, presenter_reply):
    """"Fazem entregas ao domingo?" read stores/opening_hours rows. Those rows do
    not answer it: no SQL answer, no table chips, documents next, then unknown."""
    _sql(
        monkeypatch,
        ["name", "weekday", "opens", "closes"],
        [["Mercearia Atlas - Arroios", "domingo", "10:00", "14:00"]],
        tables=("stores", "opening_hours"),
    )
    _llm(monkeypatch, presenter_reply)
    docs_calls: list[str] = []

    def no_docs(**kwargs):
        docs_calls.append(kwargs["question"])
        return {"matched": False, "answer": "", "citations": []}

    monkeypatch.setattr("site_ai_widget.api.routes.assistant.ask_documents", no_docs)
    body = _ask(api, "Fazem entregas ao domingo?")
    assert docs_calls == ["Fazem entregas ao domingo?"]
    assert body["mode"] == "none"
    assert body["answer"] == UNKNOWN_PT
    assert not body.get("tables")
    assert not body.get("sources")
    assert not body.get("rows")


def test_not_found_document_answer_is_unknown_without_chips(api, monkeypatch):
    """"Têm vinho do Porto?" got "Não consta nos documentos…" with a p. 4 chip."""
    monkeypatch.setattr(
        "site_ai_widget.api.routes.assistant.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="ok", columns=["name"], rows=[], row_count=0, attempts=1),
    )
    monkeypatch.setattr("site_ai_widget.docs.ask.hybrid_search", lambda *a, **k: _manual_chunks()[:4])
    _llm(monkeypatch, "Não consta nos documentos que a Mercearia Atlas ofereça vinho do Porto. [4]")
    body = _ask(api, "Têm vinho do Porto?")
    assert body["mode"] == "none"
    assert body["answer"] == UNKNOWN_PT
    assert not body.get("sources")


def test_sql_rows_that_answer_are_still_presented(api, monkeypatch):
    """The fall-through only applies to replies that do not answer; prices stay."""
    _sql(monkeypatch, ["name", "price"], [["Azeite Atlas Premium 750 ml", "8.49"]])
    _llm(monkeypatch, f"O Azeite Atlas Premium 750{NNBSP}ml custa 8,49{NNBSP}€.")
    body = _ask(api, "Qual o preço do azeite?")
    assert body["mode"] == "sql"
    assert body["answer"] == "O Azeite Atlas Premium 750 ml custa 8,49 €."
