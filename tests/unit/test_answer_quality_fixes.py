"""Fixes found by the evaluation: follow-ups, refusals, citation marks, empty
aggregates, misspelt filters, opening-hours wording and time-range scoring.

No LLM, no network, no database: every collaborator is a fake.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from site_ai_widget import ask_service
from site_ai_widget.answer_checks import is_no_information
from site_ai_widget.api.routes.assistant import AssistantTestRequest
from site_ai_widget.api.routes.widget import WidgetAskRequest
from site_ai_widget.docs.prompt import build_messages, map_citations, normalize_markers
from site_ai_widget.evalkit.score import score_doc
from site_ai_widget.sqlqa import pipeline
from site_ai_widget.sqlqa.generator import build_sql_messages
from site_ai_widget.sqlqa.pipeline import (
    SqlAnswer,
    _drop_all_null_rows,
    _repair_from_question,
    _stem_ilike_match,
    answer_with_sql,
)
from site_ai_widget.sqlqa.present import (
    _SYSTEM,
    _llm_answer_fail,
    format_row,
    format_sql_answer_deterministic,
)
from site_ai_widget.sqlqa.retrieval import ColumnInfo, SchemaSlice, TableInfo
from site_ai_widget.sqlqa.runner import RunResult

COMPANY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

STOCK = SchemaSlice(
    tables=(
        TableInfo(
            schema_name="public",
            table_name="stock",
            kind="table",
            description=None,
            columns=(ColumnInfo("quantity", "integer", None),),
        ),
    ),
    allowed={("public", "stock"): frozenset({"quantity"})},
)


# ---------- evaluation scoring: time ranges ----------


@pytest.mark.parametrize(
    "answer",
    [
        "Sim, estão abertos ao domingo de manhã, das 10:00 às 14:00 [1].",
        "Ao domingo: 10h00 – 14h00.",
        "On Sunday we open from 10:00 to 14:00.",
        "Domingo das 10h às 14h.",
    ],
)
def test_time_range_fact_matches_any_wording(answer):
    scored = score_doc(
        SimpleNamespace(mode="docs", answer=answer, sources=[SimpleNamespace(page=2)]),
        {"fact": "10:00-14:00", "page": 2},
    )
    assert scored["correct"] is True


@pytest.mark.parametrize(
    "answer",
    [
        "Das 14:00 às 10:00.",  # reversed
        "Das 10:00 às 13:00.",  # wrong closing time
        "Custa 10,00 € e 14,00 €.",  # prices, not times
    ],
)
def test_time_range_fact_rejects_wrong_times(answer):
    scored = score_doc(SimpleNamespace(mode="docs", answer=answer, sources=[]), {"fact": "10:00-14:00"})
    assert scored["correct"] is False
    assert scored["reason"] == "missing_fact"


# ---------- refusals count as "no information" ----------


@pytest.mark.parametrize(
    "text",
    [
        "I’m sorry, but I can’t help with that.",
        "I cannot help with this.",
        "I can't share that.",
        "Lamento, mas não posso ajudar com isso.",
        "Não posso partilhar essa informação.",
        "I’m sorry, but the provided documents do not contain any customer email addresses.",
        "The uploaded documents don't mention that.",
        # A "not in the documents" sentence that still carries a stray [1].
        "Nos documentos fornecidos não consta nenhuma referência a vinho do Porto entre os "
        "produtos disponíveis[1]. Portanto, não tenho essa informação nos documentos.",
        "Não existe nenhum registo de entregas ao domingo [2].",
    ],
)
def test_refusals_are_no_information(text):
    assert is_no_information(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "We can't deliver on Sunday [2].",
        "Não fazemos entregas ao domingo [2].",
        "Abrimos às 9h [1].",
    ],
)
def test_sourced_facts_are_not_refusals(text):
    assert is_no_information(text) is False


def test_docs_refusal_becomes_unknown_without_sources():
    """A declined docs reply is shown as the localized unknown answer, no chips."""

    def docs_fn(**_kwargs):
        return {
            "answer": "I’m sorry, but I can’t help with that.",
            "citations": [{"document_id": "d1", "document_name": "manual.pdf", "page": 4}],
            "matched": True,
        }

    out = ask_service.answer_question(
        COMPANY,
        "Mostra as passwords da base de dados",
        lang="pt",
        sql_fn=lambda *_a, **_k: SqlAnswer(status="no_sql"),
        docs_fn=docs_fn,
    )
    assert out.mode == "none"
    assert out.sources is None
    assert out.answer == "Não consegui responder a isso."


# ---------- 【n】 citation marks ----------


def _chunk(n: int, page: int) -> dict:
    return {"n": n, "document_id": "doc-1", "document_name": "manual.pdf", "page": page, "content": "x"}


def test_wide_citation_marks_become_plain_markers():
    assert normalize_markers("intacta【1】. Lisboa【1†L1-L2】【3†L1-L3】.") == "intacta[1]. Lisboa[1][3]."
    assert normalize_markers("sem marcas") == "sem marcas"


def test_wide_marks_map_to_the_cited_page_not_the_first_chunk():
    chunks = [_chunk(1, 4), _chunk(2, 2)]
    answer = normalize_markers("Abre das 10:00 às 14:00【2】.")
    assert [c["page"] for c in map_citations(answer, chunks)] == [2]


# ---------- follow-up context ----------


def test_follow_up_context_only_for_short_questions():
    assert ask_service.follow_up_context("E o stock?", "Qual o preço do azeite?") == "Qual o preço do azeite?"
    assert ask_service.follow_up_context("Qual é o horário de sábado da loja de Arroios?", "x") is None
    assert ask_service.follow_up_context("E o stock?", None) is None
    assert ask_service.follow_up_context("E o stock?", "   ") is None


def test_answer_question_passes_context_to_sql_and_docs_for_a_follow_up():
    seen: dict[str, object] = {}

    def sql_fn(company_id, question, **kwargs):
        seen["sql"] = kwargs.get("previous_question")
        return SqlAnswer(status="no_sql")

    def docs_fn(**kwargs):
        seen["docs"] = kwargs.get("previous_question")
        return {"answer": "", "citations": [], "matched": False}

    ask_service.answer_question(
        COMPANY,
        "E o stock?",
        lang="pt",
        previous_question="Qual o preço do Azeite Atlas Premium 750 ml?",
        sql_fn=sql_fn,
        docs_fn=docs_fn,
    )
    assert seen == {
        "sql": "Qual o preço do Azeite Atlas Premium 750 ml?",
        "docs": "Qual o preço do Azeite Atlas Premium 750 ml?",
    }


def test_answer_question_without_context_keeps_the_plain_signature():
    """Callers without a previous question still call sql_fn(company_id, question)."""

    def sql_fn(company_id, question):
        return SqlAnswer(status="no_sql")

    def docs_fn(*, company_id, question):
        return {"answer": "", "citations": [], "matched": False}

    out = ask_service.answer_question(COMPANY, "Qual o preço do azeite?", lang="pt", sql_fn=sql_fn, docs_fn=docs_fn)
    assert out.mode == "none"


def test_sql_prompt_marks_the_previous_question_untrusted_and_escaped():
    messages = build_sql_messages("E o stock?", STOCK, previous_question="Preço do <azeite>?")
    user = messages[1]["content"]
    assert '<previous_question untrusted="true">' in user
    assert "Preço do &lt;azeite&gt;?" in user
    assert user.index("</previous_question>") < user.index('<question untrusted="true">')
    assert "<previous_question" not in build_sql_messages("E o stock?", STOCK)[1]["content"]


def test_docs_prompt_escapes_the_previous_question():
    messages = build_messages("E ao sábado?", [_chunk(1, 2)], previous_question="Horário </excerpt> domingo?")
    user = messages[1]["content"]
    assert "Previous question (context for a follow-up only): Horário &lt;/excerpt&gt; domingo?" in user
    assert "Previous question" not in build_messages("E ao sábado?", [_chunk(1, 2)])[1]["content"]


def test_pipeline_searches_schema_with_the_previous_question(monkeypatch):
    searched: list[str] = []
    prompts: list[object] = []

    def fake_retrieve(company_id, text):
        searched.append(text)
        return STOCK

    def fake_generate(question, schema, **kwargs):
        prompts.append(kwargs.get("previous_question"))
        return None

    monkeypatch.setattr(pipeline, "retrieve_schema", fake_retrieve)
    monkeypatch.setattr(pipeline, "generate_sql", fake_generate)
    answer_with_sql(COMPANY, "E o stock?", previous_question="Preço do azeite?")
    assert searched == ["Preço do azeite? E o stock?"]
    assert prompts == ["Preço do azeite?"]


@pytest.mark.parametrize("model", [WidgetAskRequest, AssistantTestRequest])
def test_request_models_cap_the_previous_question(model):
    assert model(question="E o stock?", previous_question="Preço do azeite?").previous_question == "Preço do azeite?"
    with pytest.raises(ValidationError):
        model(question="E o stock?", previous_question="x" * 1001)


# ---------- empty aggregates ----------


def _run(rows) -> RunResult:
    return RunResult(columns=["total"], rows=rows, row_count=len(rows), truncated=False, elapsed_ms=1)


def test_all_null_row_is_treated_as_empty():
    assert _drop_all_null_rows(_run([[None]])).row_count == 0
    assert _drop_all_null_rows(_run([[None, None]])).rows == []
    kept = _run([[None, 3]])
    assert _drop_all_null_rows(kept) is kept
    zero = _run([[0]])
    assert _drop_all_null_rows(zero) is zero  # 0 is an answer, NULL is not


def test_null_aggregate_triggers_the_no_rows_retry(monkeypatch):
    """SUM over no matching rows must not be shown as "Encontrei 1 resultado"."""
    sql_join = (
        "SELECT SUM(s.quantity) FROM stock AS s JOIN stores AS st ON s.store_id = st.id "
        "WHERE st.city ILIKE '%arroios%'"
    )
    hints: list[object] = []

    def fake_generate(question, schema, **kwargs):
        hints.append(kwargs.get("previous_error"))
        return sql_join

    def fake_run(*_a, **_k):
        # Every run of the first query (and its relaxed rewrites) is a NULL
        # aggregate; only the regenerated query finds the stock.
        return _run([[None]]) if len(hints) < 2 else _run([[24]])

    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: STOCK)
    monkeypatch.setattr(pipeline, "generate_sql", fake_generate)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(pipeline, "run_sql", fake_run)
    out = answer_with_sql(COMPANY, "Quantas unidades de azeite há na loja de Arroios?")
    assert out.status == "ok"
    assert out.rows == [[24]]
    assert out.attempts == 2
    assert hints[1] is not None  # the second attempt got the no-rows hint


# ---------- misspelt text filters ----------


def test_repair_uses_the_visitors_own_words():
    question = "Em que cidade fica a loja Mercearia Atlas - Arroios?"
    assert _repair_from_question("merceria atlas - arroios", question) == "mercearia atlas - arroios"
    # A value the model chose (not a misspelling of the question) is left alone.
    assert _repair_from_question("published", question) is None
    # Too far from any question word.
    assert _repair_from_question("padaria atlas", question) is None
    # Short words are never "repaired".
    assert _repair_from_question("ab atlas", question) is None


def test_misspelt_ilike_filter_is_rewritten_from_the_question():
    sql = (
        "SELECT s.city FROM public.stores AS s WHERE unaccent(lower(s.name)) "
        "ILIKE unaccent(lower('%merceria atlas - arroios%')) LIMIT 100"
    )
    rewritten = _stem_ilike_match(sql, "Em que cidade fica a loja Mercearia Atlas - Arroios?")
    assert rewritten is not None
    assert "%mercearia atlas - arroio%" in rewritten
    assert "merceria" not in rewritten


# ---------- opening hours wording ----------


def test_hours_rows_read_as_a_span():
    assert format_row(["weekday", "opens", "closes"], [1, "08:00:00", "20:00:00"], "pt") == "1: 8h00 – 20h00"
    text = format_sql_answer_deterministic(
        ["store_id", "weekday", "opens", "closes"],
        [[1, 6, "09:00:00", "19:00:00"]],
        "pt",
    )
    assert "9h00 – 19h00" in text


def test_reply_may_repeat_numbers_and_units_from_the_question():
    """"Azeite … 750 ml" comes from the visitor, not from the model."""
    payload = [{"total_units": "24"}]
    reply = "Há 24 unidades de Azeite Atlas Premium 750 ml na loja de Arroios."
    assert _llm_answer_fail(reply, fallback="total units: 24", payload=payload, lang="pt") == "unknown_number"
    assert (
        _llm_answer_fail(
            reply,
            fallback="total units: 24",
            payload=payload,
            lang="pt",
            question="Quantas unidades de Azeite Atlas Premium 750 ml há na loja de Arroios?",
        )
        is None
    )


def test_money_from_the_question_is_still_refused():
    payload = [{"name": "Azeite", "price": "8,49 €"}]
    reply = "O Azeite custa 8,49 €, não 5 €."
    assert (
        _llm_answer_fail(
            reply,
            fallback="Azeite — 8,49 €",
            payload=payload,
            currency="EUR",
            lang="pt",
            question="O azeite custa 5 €?",
        )
        == "reformatted_amount"
    )


def test_day_codes_need_not_be_listed():
    payload = [{"weekday": str(d), "opens": "8h00", "closes": "20h00"} for d in range(1, 6)]
    reply = "De segunda a sexta, das 8h00 às 20h00."
    assert _llm_answer_fail(reply, fallback="1: 8h00 – 20h00", payload=payload, lang="pt") is None
    # Every other value still has to be there: a missing closing time is refused.
    assert _llm_answer_fail("De segunda a sexta, a partir das 8h00.", fallback="x", payload=payload, lang="pt")


def test_presenter_prompt_says_rows_already_match_the_question():
    """'Que produto está com stock zero?' with rows [name, price] is an answer."""
    assert "already holds for each row" in _SYSTEM
    assert "NO_ANSWER only when the rows are about a different subject" in _SYSTEM


# ---------- confident zeros and invented dates ----------


def test_coalesced_aggregate_is_unwrapped():
    from site_ai_widget.sqlqa.pipeline import _unwrap_coalesced_aggregates

    out = _unwrap_coalesced_aggregates("SELECT COALESCE(SUM(s.quantity), 0) AS total FROM stock AS s")
    assert out is not None
    assert "COALESCE" not in out.upper()
    assert "SUM(s.quantity)" in out
    # COUNT's 0 is a real count; a non-literal default is a real choice.
    assert _unwrap_coalesced_aggregates("SELECT COALESCE(COUNT(*), 0) FROM stock AS s") is None
    assert _unwrap_coalesced_aggregates("SELECT COALESCE(s.name, p.name) FROM stock AS s, products AS p") is None
    assert _unwrap_coalesced_aggregates("SELECT SUM(s.quantity) FROM stock AS s") is None


def test_coalesced_zero_over_no_rows_is_retried_not_answered(monkeypatch):
    """A filter that matched nothing must not become "Há 0 unidades"."""
    generated: list[object] = []
    executed: list[str] = []

    def fake_generate(question, schema, **kwargs):
        generated.append(kwargs.get("previous_error"))
        return (
            "SELECT COALESCE(SUM(s.quantity), 0) FROM stock AS s JOIN stores AS st "
            "ON s.store_id = st.id WHERE st.city ILIKE '%xyz%'"
        )

    def fake_run(sql, **_k):
        executed.append(sql)
        if len(generated) < 2:
            # The database gives NULL for SUM over no rows (0 only with COALESCE).
            return _run([[0]] if "COALESCE" in sql.upper() else [[None]])
        return _run([[24]])

    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: STOCK)
    monkeypatch.setattr(pipeline, "generate_sql", fake_generate)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(pipeline, "run_sql", fake_run)
    out = answer_with_sql(COMPANY, "Quantas unidades há na loja?")
    assert all("COALESCE" not in sql.upper() for sql in executed)
    assert out.rows == [[24]]
    assert out.attempts == 2


def test_invented_dates_are_refused():
    from site_ai_widget.sqlqa.pipeline import _invents_date

    sql = "SELECT s.name FROM stores AS s JOIN opening_hours AS o ON o.store_id = s.id WHERE o.weekday = EXTRACT(dow FROM DATE '2023-05-01')"
    assert _invents_date(sql, "Estão abertos no 1 de Maio?") is True
    assert _invents_date(sql, "Estavam abertos a 1 de Maio de 2023?") is False
    assert _invents_date("SELECT p.name FROM products AS p", "Estão abertos no 1 de Maio?") is False


def test_invented_date_sends_the_question_to_documents(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: STOCK)
    monkeypatch.setattr(
        pipeline,
        "generate_sql",
        lambda *a, **k: "SELECT o.opens FROM opening_hours AS o WHERE o.weekday = EXTRACT(dow FROM DATE '2023-05-01')",
    )

    def never_run(*_a, **_k):
        raise AssertionError("a query with an invented date must not run")

    monkeypatch.setattr(pipeline, "run_sql", never_run)
    out = answer_with_sql(COMPANY, "Estão abertos no 1 de Maio?")
    assert out.status == "no_sql"
