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

    def present(columns, rows, lang, *, truncated=False, question=None, currency=None, products=True):
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


def test_empty_sql_rows_fall_through_to_docs():
    doc_id = str(uuid4())
    present_called = {"n": 0}

    def present(*_a, **_k):
        present_called["n"] += 1
        return "should not present empty sql"

    out = answer_question(
        uuid4(),
        "Que queijos têm?",
        lang="pt",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[],
            row_count=0,
            truncated=False,
            attempts=1,
        ),
        docs_fn=lambda **k: {
            "matched": True,
            "answer": "O destaque inclui o Queijo da Serra.",
            "citations": [
                {
                    "document_id": doc_id,
                    "document_name": "manual.pdf",
                    "page": 1,
                }
            ],
        },
        present_fn=present,
    )
    assert out.mode == "docs"
    assert out.answer == "O destaque inclui o Queijo da Serra."
    assert present_called["n"] == 0
    assert out.sources is not None and out.sources[0].document_name == "manual.pdf"


def test_empty_sql_and_docs_miss_is_none():
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
        present_fn=lambda *a, **k: "should not present",
    )
    assert out.mode == "none"
    assert out.answer is None


def test_truncated_flag_reaches_the_answer_and_presenter_failure_falls_back():
    def boom(columns, rows, lang, *, truncated=False, question=None, currency=None, products=True):
        raise RuntimeError("presenter bug")

    out = answer_question(
        uuid4(),
        "Which products do you have?",
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
    assert out.answer.splitlines()[-1] == "and more than 490 more items"


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

    def present(columns, rows, lang, *, truncated=False, question=None, currency=None, products=True):
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


def test_omitted_lang_uses_company_widget_locale(monkeypatch):
    company = uuid4()
    seen: dict[str, object] = {}

    def present(columns, rows, lang, *, truncated=False, question=None, currency=None, products=True):
        seen["lang"] = lang
        return "Azeite — 8,49 €"

    monkeypatch.setattr("site_ai_widget.ask_service._company_locale", lambda cid: "pt")
    monkeypatch.setattr("site_ai_widget.ask_service._company_currency", lambda cid: "EUR")
    out = answer_question(
        company,
        "Qual o preço do azeite?",
        lang=None,
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
    assert seen["lang"] == "pt"
    assert out.answer == "Azeite — 8,49 €"


def _many_rows_sql(columns, tables):
    return lambda *a, **k: SqlAnswer(
        status="ok",
        columns=columns,
        rows=[[f"Linha {i}"] for i in range(12)],
        row_count=12,
        truncated=False,
        attempts=1,
        tables=tables,
    )


def test_more_line_says_produtos_only_for_product_lists():
    def boom(*a, **k):
        raise RuntimeError("use the deterministic list")

    products = answer_question(
        uuid4(), "Que queijos têm?", lang="pt",
        sql_fn=_many_rows_sql(["name"], ["products"]),
        docs_fn=lambda **k: {"matched": False}, present_fn=boom,
    )
    stores = answer_question(
        uuid4(), "Que lojas têm?", lang="pt",
        sql_fn=_many_rows_sql(["name"], ["stores"]),
        docs_fn=lambda **k: {"matched": False}, present_fn=boom,
    )
    stores_en = answer_question(
        uuid4(), "Which stores do you have?", lang="en",
        sql_fn=_many_rows_sql(["name"], ["stores"]),
        docs_fn=lambda **k: {"matched": False}, present_fn=boom,
    )
    assert products.answer.splitlines()[-1] == "e mais 2 produtos"
    assert stores.answer.splitlines()[-1] == "e mais 2 resultados"
    assert stores_en.answer.splitlines()[-1] == "and 2 more results"


def test_omitted_lang_for_an_english_company_gives_en_money(monkeypatch):
    from decimal import Decimal

    monkeypatch.setattr("site_ai_widget.ask_service._company_currency", lambda cid: "EUR")
    monkeypatch.setattr("site_ai_widget.ask_service._company_locale", lambda cid: "en")
    out = answer_question(
        uuid4(),
        "How much is olive oil?",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok", columns=["name", "price"], rows=[["Olive oil", Decimal("8.49")]],
            row_count=1, truncated=False, attempts=1, tables=["products"],
        ),
        docs_fn=lambda **k: {"matched": False},
        present_fn=lambda columns, rows, lang, **kw: __import__(
            "site_ai_widget.sqlqa.present", fromlist=["x"]
        ).format_sql_answer_deterministic(columns, rows, lang, currency=kw.get("currency")),
    )
    assert out.answer == "Olive oil — €8.49"


def test_pt_question_formats_money_with_comma_even_if_widget_lang_is_en(monkeypatch):
    monkeypatch.setattr("site_ai_widget.ask_service._company_currency", lambda cid: "EUR")
    monkeypatch.setattr("site_ai_widget.ask_service._company_locale", lambda cid: "en")
    out = answer_question(
        uuid4(),
        "Qual o preço do azeite?",
        lang="en",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name", "price"],
            rows=[["Azeite Atlas Premium 750 ml", "8.49"]],
            row_count=1,
            truncated=False,
            attempts=1,
            tables=["products"],
        ),
        docs_fn=lambda **k: {"matched": False},
    )
    assert out.mode == "sql"
    assert out.answer == "Azeite Atlas Premium 750 ml — 8,49 €"
    assert "8.49" not in (out.answer or "")
    assert "750 ml" in (out.answer or "")


def test_short_ola_uses_previous_lang(monkeypatch):
    monkeypatch.setattr("site_ai_widget.ask_service._company_locale", lambda cid: "en")
    out = answer_question(
        uuid4(),
        "Olá",
        lang="en",
        previous_lang="pt",
        sql_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("greeting skips sql")),
        docs_fn=lambda **k: (_ for _ in ()).throw(AssertionError("greeting skips docs")),
    )
    assert out.mode == "chat"
    assert out.answer == "Olá! Em que posso ajudar?"



def test_every_answer_reports_its_language():
    greeting = answer_question(uuid4(), "Olá", lang="en", previous_lang="pt")
    assert (greeting.mode, greeting.lang) == ("chat", "pt")
    en = answer_question(
        uuid4(),
        "Do you have pão de milho?",
        lang="pt",
        sql_fn=lambda *a, **k: SqlAnswer(
            status="ok", columns=["name"], rows=[["Pão de milho"]], row_count=1,
            truncated=False, attempts=1, tables=["products"],
        ),
        docs_fn=lambda **k: {"matched": False},
        present_fn=lambda columns, rows, lang, **kw: f"lang={lang}",
    )
    assert en.lang == "en"
    assert en.answer == "lang=en"
    none = answer_question(
        uuid4(),
        "Qual a política de devoluções?",
        lang="en",
        sql_fn=lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
        docs_fn=lambda **k: {"matched": False},
    )
    assert (none.mode, none.lang) == ("none", "pt")
