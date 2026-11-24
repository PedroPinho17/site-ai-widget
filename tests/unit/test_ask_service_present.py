"""ask_service fills SQL mode with a visitor-facing answer."""
from __future__ import annotations

from uuid import uuid4

from site_ai_widget.ask_service import answer_question
from site_ai_widget.sqlqa.pipeline import SqlAnswer


def test_greeting_skips_sql_and_docs_and_has_no_sources():
    called: list[str] = []

    out = answer_question(
        uuid4(),
        "Olá",
        lang="pt",
        sql_fn=lambda *a, **k: called.append("sql"),
        docs_fn=lambda **k: called.append("docs"),
    )
    assert out.mode == "chat"
    assert out.answer == "Olá! Em que posso ajudar?"
    assert out.sources is None
    assert called == []

    thanks = answer_question(
        uuid4(),
        "thanks",
        lang="en",
        sql_fn=lambda *a, **k: called.append("sql"),
        docs_fn=lambda **k: called.append("docs"),
    )
    assert thanks.mode == "chat"
    assert thanks.answer is not None
    assert "welcome" in thanks.answer.lower()
    assert called == []


def test_sql_answer_uses_present_fn_lang_and_no_sql():
    company = uuid4()
    seen: list[tuple] = []

    def fake_sql(cid, question, **kwargs):
        return SqlAnswer(
            status="ok",
            columns=["name", "price"],
            rows=[["Azeite", 8.9]],
            row_count=1,
            truncated=False,
            attempts=1,
        )

    def present(columns, rows, lang, *, truncated=False, question=None, currency=None):
        seen.append((columns, rows, lang, truncated, question))
        return "O azeite custa 8,9."

    out = answer_question(
        company,
        "Qual o preço do azeite?",
        lang="pt",
        sql_fn=fake_sql,
        docs_fn=lambda **k: {"matched": False},
        present_fn=present,
    )
    assert out.mode == "sql"
    assert out.answer == "O azeite custa 8,9."
    assert seen == [
        (["name", "price"], [["Azeite", 8.9]], "pt", False, "Qual o preço do azeite?")
    ]
    assert "SELECT" not in (out.answer or "")


def test_empty_sql_rows_get_not_found_answer():
    out = answer_question(
        uuid4(),
        "produto inexistente?",
        lang="pt",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[],
            row_count=0,
            truncated=False,
            attempts=1,
        ),
        docs_fn=lambda **k: {"matched": False},
        present_fn=lambda cols, rows, lang, **kw: "Não encontrei essa informação.",
    )
    assert out.mode == "sql"
    assert out.answer == "Não encontrei essa informação."
    assert out.rows == []


def test_truncated_flag_reaches_the_answer_and_presenter_failure_falls_back():
    def boom(columns, rows, lang, *, truncated=False, question=None, currency=None):
        raise RuntimeError("presenter bug")

    out = answer_question(
        uuid4(),
        "todos os produtos?",
        lang="en",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[[f"Item {i}"] for i in range(500)],
            row_count=500,
            truncated=True,
            attempts=1,
        ),
        docs_fn=lambda **k: {"matched": False},
        present_fn=boom,
    )
    assert out.truncated is True
    assert out.answer is not None
    assert out.answer.splitlines()[-1] == "and more than 490 more"


def test_docs_answers_always_include_source_chips():
    doc_id = str(uuid4())

    out = answer_question(
        uuid4(),
        "Qual a política de devoluções?",
        lang="pt",
        sql_fn=lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
        docs_fn=lambda **k: {
            "matched": True,
            "answer": "Pode devolver em 14 dias.",
            "citations": [
                {
                    "document_id": doc_id,
                    "document_name": "politica-devolucoes.pdf",
                    "page": 1,
                }
            ],
        },
    )
    assert out.mode == "docs"
    assert out.sources is not None
    assert len(out.sources) == 1
    assert out.sources[0].document_name == "politica-devolucoes.pdf"
    assert out.sources[0].document_id == doc_id


def test_sql_presenter_receives_company_currency(monkeypatch):
    company = uuid4()
    seen: dict[str, object] = {}

    def present(columns, rows, lang, *, truncated=False, question=None, currency=None):
        seen["currency"] = currency
        return "Azeite — 8,49 €"

    monkeypatch.setattr(
        "site_ai_widget.ask_service._company_currency",
        lambda cid: "EUR",
    )
    out = answer_question(
        company,
        "Qual o preço do azeite?",
        lang="pt",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name", "price"],
            rows=[["Azeite", "8.49"]],
            row_count=1,
            truncated=False,
            attempts=1,
        ),
        docs_fn=lambda **k: {"matched": False},
        present_fn=present,
    )
    assert out.mode == "sql"
    assert seen["currency"] == "EUR"
    assert out.answer == "Azeite — 8,49 €"
