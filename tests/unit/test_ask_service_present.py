"""ask_service fills SQL mode with a visitor-facing answer."""
from __future__ import annotations

from uuid import uuid4

from site_ai_widget.ask_service import answer_question
from site_ai_widget.sqlqa.pipeline import SqlAnswer


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

    def present(columns, rows, lang, *, truncated=False):
        seen.append((columns, rows, lang, truncated))
        return "Azeite: 8,90"

    out = answer_question(
        company,
        "Qual o preço do azeite?",
        lang="pt",
        sql_fn=fake_sql,
        docs_fn=lambda **k: {"matched": False},
        present_fn=present,
    )
    assert out.mode == "sql"
    assert out.answer == "Azeite: 8,90"
    assert seen == [(["name", "price"], [["Azeite", 8.9]], "pt", False)]
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
    def boom(columns, rows, lang, *, truncated=False):
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
