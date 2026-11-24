"""Text-to-SQL pipeline unit tests with fakes."""
from __future__ import annotations

import logging

import pytest

from site_ai_widget.sqlqa import pipeline
from site_ai_widget.sqlqa.generator import RETRY_HINTS, retry_hint
from site_ai_widget.sqlqa.pipeline import (
    SqlAnswer,
    _is_catalog_question,
    _relax_text_match,
    _stem_for_search,
    _stem_ilike_match,
    _strip_unaccent_calls,
    _unaccent_text_match,
    answer_with_sql,
)
from site_ai_widget.sqlqa.retrieval import ColumnInfo, SchemaSlice, TableInfo
from site_ai_widget.sqlqa.runner import RunnerError, RunResult
from site_ai_widget.sqlqa.validator import SQLRejected

COMPANY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SENTINEL_Q = "SENTINEL-QUESTION-ZZZ"
SENTINEL_SQL = "SELECT name FROM products WHERE sku = 'SENTINEL-SKU'"
SENTINEL_ROW = "SENTINEL-ROW-VALUE"
BS = chr(92)  # one backslash

ALLOWED = {("public", "products"): frozenset({"id", "name", "price", "sku"})}

PRODUCTS = SchemaSlice(
    tables=(
        TableInfo(
            schema_name="public",
            table_name="products",
            kind="table",
            description="Product catalog",
            columns=(
                ColumnInfo("id", "integer", "pk"),
                ColumnInfo("name", "text", "name"),
                ColumnInfo("price", "numeric", "price"),
                ColumnInfo("sku", "text", "sku"),
            ),
        ),
    ),
    allowed=ALLOWED,
)

EMPTY = SchemaSlice(tables=(), allowed={})

GOOD_SQL = 'SELECT "products"."name" FROM "products" AS "products" LIMIT 100'


def _result(rows=None) -> RunResult:
    rows = rows or [[SENTINEL_ROW]]
    return RunResult(
        columns=["name"],
        rows=rows,
        row_count=len(rows),
        truncated=False,
        elapsed_ms=1,
    )


@pytest.fixture(autouse=True)
def _slice(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: PRODUCTS)
    # The "unaccent missing" memo is process-wide: never leak it between tests.
    monkeypatch.setattr(pipeline, "_unaccent_missing", {})


def test_success_on_first_try(monkeypatch):
    gens: list[object] = []

    def gen(question, schema, **kwargs):
        gens.append(kwargs.get("previous_error"))
        return GOOD_SQL

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: GOOD_SQL)
    monkeypatch.setattr(pipeline, "run_sql", lambda *a, **k: _result())

    out = answer_with_sql(COMPANY, SENTINEL_Q, llm_complete=lambda *a, **k: GOOD_SQL)
    assert out.status == "ok"
    assert out.attempts == 1
    assert out.rows == [[SENTINEL_ROW]]
    assert out.reason is None
    assert gens == [None]


def test_validator_refusal_then_success(monkeypatch):
    calls = {"n": 0}
    previous: list[object] = []

    def gen(question, schema, **kwargs):
        previous.append(kwargs.get("previous_error"))
        calls["n"] += 1
        if calls["n"] == 1:
            return "SELECT bogosity FROM products"
        return GOOD_SQL

    def validate(sql, allowed):
        if "bogosity" in sql:
            raise SQLRejected("column_not_allowed", "Column bogosity is not an approved column.")
        return GOOD_SQL

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", validate)
    monkeypatch.setattr(pipeline, "run_sql", lambda *a, **k: _result())

    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "ok"
    assert out.attempts == 2
    assert previous[0] is None
    assert previous[1] == retry_hint("column_not_allowed")
    assert previous[1] == RETRY_HINTS["column_not_allowed"]
    assert "bogosity" not in str(previous[1])
    assert "SELECT" not in str(previous[1])


def test_refusal_twice_ends_as_refused(monkeypatch):
    def gen(question, schema, **kwargs):
        return "SELECT badcol FROM products"

    def validate(sql, allowed):
        raise SQLRejected("column_not_allowed", "Column badcol … SELECT secrets")

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", validate)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "refused"
    assert out.reason == "column_not_allowed"
    assert out.attempts == 2
    assert out.rows == []


def test_no_sql_from_generator(monkeypatch):
    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: None)
    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "no_sql"
    assert out.attempts == 1


def test_empty_slice(monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: EMPTY)
    called = {"gen": False}

    def gen(*a, **k):
        called["gen"] = True
        return GOOD_SQL

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "no_sql"
    assert out.attempts == 1
    assert called["gen"] is False


def test_db_error_retried_once(monkeypatch):
    runs = {"n": 0}
    previous: list[object] = []

    def gen(question, schema, **kwargs):
        previous.append(kwargs.get("previous_error"))
        return GOOD_SQL

    def run(*a, **k):
        runs["n"] += 1
        if runs["n"] == 1:
            raise RunnerError("db_error")
        return _result()

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: GOOD_SQL)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "ok"
    assert out.attempts == 2
    assert previous[1] == retry_hint("db_error")
    assert "FROM products" not in str(previous[1])
    assert SENTINEL_SQL not in str(previous[1])


@pytest.mark.parametrize("reason", ["timeout", "result_too_large"])
def test_timeout_and_result_too_large_not_retried(monkeypatch, reason):
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return GOOD_SQL

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: GOOD_SQL)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: (_ for _ in ()).throw(RunnerError(reason)),
    )

    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "error"
    assert out.reason == reason
    assert out.attempts == 1
    assert gens["n"] == 1


def test_previous_error_is_always_fixed_hint(monkeypatch):
    seen: list[object] = []

    def gen(question, schema, **kwargs):
        seen.append(kwargs.get("previous_error"))
        if kwargs.get("previous_error") is None:
            return "SELECT cost_price FROM products"
        return GOOD_SQL

    def validate(sql, allowed):
        if "cost_price" in sql:
            raise SQLRejected(
                "column_not_allowed",
                "Column cost_price is not approved. SQL was: SELECT cost_price",
            )
        return GOOD_SQL

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", validate)
    monkeypatch.setattr(pipeline, "run_sql", lambda *a, **k: _result())

    answer_with_sql(COMPANY, SENTINEL_Q)
    hint = seen[1]
    assert hint == RETRY_HINTS["column_not_allowed"]
    assert "cost_price" not in hint
    assert "SELECT" not in hint
    assert "approved" not in hint


def test_max_two_generate_calls(monkeypatch):
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return "SELECT nope FROM products"

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(
        pipeline,
        "validate_sql",
        lambda *a, **k: (_ for _ in ()).throw(SQLRejected("table_not_allowed")),
    )

    answer_with_sql(COMPANY, SENTINEL_Q)
    assert gens["n"] == 2


def test_logs_contain_no_question_sql_or_rows(monkeypatch, caplog):
    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: GOOD_SQL)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: GOOD_SQL)
    monkeypatch.setattr(pipeline, "run_sql", lambda *a, **k: _result([[SENTINEL_ROW]]))

    with caplog.at_level(logging.INFO, logger="site_ai_widget"):
        answer_with_sql(COMPANY, SENTINEL_Q)
    text = " ".join(r.message for r in caplog.records)
    assert "status=ok" in text
    assert SENTINEL_Q not in text
    assert SENTINEL_SQL not in text
    assert SENTINEL_ROW not in text
    assert "SELECT" not in text


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DROP TABLE products",
        "SELECT 1; DROP TABLE products",
        "WITH d AS (DELETE FROM products RETURNING id) SELECT id FROM d",
    ],
)
def test_real_validator_blocks_writes_without_running(monkeypatch, bad_sql):
    """Use the real validate_sql; run_sql must never be called."""
    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: bad_sql)
    run_calls = {"n": 0}

    def run(*a, **k):
        run_calls["n"] += 1
        raise AssertionError("run_sql must not be called")

    monkeypatch.setattr(pipeline, "run_sql", run)
    # Keep the real validate_sql bound on the pipeline module.
    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "refused"
    assert out.attempts == 2
    assert run_calls["n"] == 0


def test_empty_text_equality_is_rerun_as_ilike(monkeypatch):
    """lower(name) = 'azeite' matches nothing; ILIKE '%azeite%' returns the price."""
    exact = "SELECT p.price FROM products AS p WHERE lower(p.name) = 'azeite' LIMIT 10"
    seen: list[str] = []

    def run(sql, **kwargs):
        seen.append(sql)
        if "ILIKE" in sql.upper():
            return RunResult(
                columns=["price"],
                rows=[["8.49"]],
                row_count=1,
                truncated=False,
                elapsed_ms=1,
            )
        return RunResult(columns=["price"], rows=[], row_count=0, truncated=False, elapsed_ms=1)

    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: exact)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, "Qual o preço do azeite?")
    assert out.status == "ok"
    assert out.row_count == 1
    assert out.rows == [["8.49"]]
    assert out.attempts == 1
    assert len(seen) == 2
    assert "ILIKE" in seen[1].upper()


def test_relax_text_match_escapes_like_wildcards():
    relaxed = _relax_text_match(
        "SELECT p.name FROM products AS p WHERE p.name = '100%_bio' LIMIT 5",
        "tem azeite 100%_bio?",
    )
    pattern = "'%100" + BS + "%" + BS + "_bio%'"
    assert relaxed == (
        "SELECT p.name FROM products AS p WHERE UNACCENT(LOWER(p.name)) "
        f"ILIKE UNACCENT(LOWER({pattern})) LIMIT 5"
    )


def test_numeric_equality_is_not_relaxed(monkeypatch):
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return 'SELECT p.price FROM products AS p WHERE p.price = 0 LIMIT 10'

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: RunResult(
            columns=["price"], rows=[], row_count=0, truncated=False, elapsed_ms=1
        ),
    )

    out = answer_with_sql(COMPANY, SENTINEL_Q)
    assert out.status == "ok"
    assert out.row_count == 0
    assert gens["n"] == 1  # no text filter: a real empty result, no extra LLM call


def test_relax_log_has_no_sql_or_values(monkeypatch, caplog):
    exact = "SELECT p.price FROM products AS p WHERE lower(p.name) = 'azeite' LIMIT 10"

    def run(sql, **kwargs):
        if "ILIKE" in sql.upper():
            return RunResult(
                columns=["price"], rows=[["8.49"]], row_count=1, truncated=False, elapsed_ms=1
            )
        return RunResult(columns=["price"], rows=[], row_count=0, truncated=False, elapsed_ms=1)

    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: exact)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(pipeline, "run_sql", run)

    with caplog.at_level(logging.INFO, logger="site_ai_widget"):
        answer_with_sql(COMPANY, f"{SENTINEL_Q} azeite")
    text = " ".join(r.message for r in caplog.records)
    assert SENTINEL_Q not in text
    assert "stage=relax_text" in text
    assert "validation=pass" in text
    assert "rows=1" in text
    assert "SELECT" not in text
    assert "azeite" not in text
    assert "8.49" not in text


def test_exports():
    from site_ai_widget import sqlqa

    assert sqlqa.SqlAnswer is SqlAnswer
    assert sqlqa.answer_with_sql is answer_with_sql


def test_only_the_visitors_word_is_widened_not_other_filters():
    """status = 'published' stays exact: ILIKE would also match 'unpublished'."""
    relaxed = _relax_text_match(
        "SELECT p.name FROM products AS p "
        "WHERE p.status = 'published' AND lower(p.name) = 'azeite' LIMIT 10",
        "Qual o preço do azeite?",
    )
    assert relaxed == (
        "SELECT p.name FROM products AS p "
        "WHERE p.status = 'published' AND UNACCENT(LOWER(p.name)) "
        "ILIKE UNACCENT(LOWER('%azeite%')) LIMIT 10"
    )


@pytest.mark.parametrize(
    "sql",
    [
        # Select-list CASE: relaxing it would change the values a column returns.
        "SELECT CASE WHEN p.name = 'azeite' THEN 1 ELSE 0 END AS f FROM products AS p LIMIT 10",
        # NOT: relaxing it would hide more rows, not fewer.
        "SELECT p.name FROM products AS p WHERE NOT (p.name = 'azeite') LIMIT 10",
        # JOIN ... ON and HAVING are not plain row filters.
        "SELECT p.name FROM products AS p JOIN products AS q ON q.name = 'azeite' LIMIT 10",
        "SELECT p.name, COUNT(*) AS n FROM products AS p GROUP BY p.name "
        "HAVING p.name = 'azeite' LIMIT 10",
        # Inside a function call in WHERE.
        "SELECT p.name FROM products AS p WHERE COALESCE(p.name = 'azeite', FALSE) LIMIT 10",
    ],
    ids=["case", "not", "join-on", "having", "function"],
)
def test_equalities_outside_plain_where_filters_are_untouched(sql):
    assert _relax_text_match(sql, "Qual o preço do azeite?") is None


@pytest.mark.parametrize(
    ("literal", "question"),
    [
        ("published", "Qual o preço do azeite?"),  # chosen by the model, not typed
        ("ab", "tem ab?"),  # too short to widen safely
        ("123", "o produto 123 existe?"),  # no letters: an id or code stays exact
    ],
)
def test_words_the_visitor_did_not_type_stay_exact(literal, question):
    sql = f"SELECT p.name FROM products AS p WHERE p.name = '{literal}' LIMIT 10"
    assert _relax_text_match(sql, question) is None


def test_accents_and_case_still_count_as_typed():
    relaxed = _relax_text_match(
        "SELECT p.name FROM products AS p WHERE lower(p.name) = 'pao' LIMIT 10",
        "Têm PÃO de milho?",
    )
    assert relaxed == (
        "SELECT p.name FROM products AS p WHERE UNACCENT(LOWER(p.name)) "
        "ILIKE UNACCENT(LOWER('%pao%')) LIMIT 10"
    )


def test_quote_in_the_word_stays_inside_the_literal():
    relaxed = _relax_text_match(
        "SELECT p.name FROM products AS p WHERE p.name = 'o''neil' LIMIT 10",
        "têm o'neil?",
    )
    assert relaxed == (
        "SELECT p.name FROM products AS p WHERE UNACCENT(LOWER(p.name)) "
        "ILIKE UNACCENT(LOWER('%o''neil%')) LIMIT 10"
    )


def test_relaxed_query_goes_through_the_real_validator(monkeypatch):
    """The ILIKE rewrite is validated again: approved tables only, LIMIT kept."""
    from site_ai_widget.sqlqa.validator import validate_sql as real_validate

    exact = "SELECT p.price FROM products AS p WHERE lower(p.name) = 'azeite' LIMIT 10"
    validated: list[str] = []

    def validate(sql, allowed):
        out = real_validate(sql, allowed)
        validated.append(out)
        return out

    def run(sql, **kwargs):
        if "ILIKE" in sql.upper():
            return RunResult(columns=["price"], rows=[["8.49"]], row_count=1, truncated=False, elapsed_ms=1)
        return RunResult(columns=["price"], rows=[], row_count=0, truncated=False, elapsed_ms=1)

    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: exact)
    monkeypatch.setattr(pipeline, "validate_sql", validate)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, "Qual o preço do azeite?")
    assert out.status == "ok"
    assert len(validated) == 2
    assert "ILIKE" in validated[1].upper()
    assert "LIMIT 10" in validated[1].upper()


def test_relaxed_query_on_an_unapproved_table_is_dropped(monkeypatch):
    """If the rewrite were ever refused, the original empty result stands; no other table is read."""
    exact = "SELECT p.price FROM products AS p WHERE lower(p.name) = 'azeite' LIMIT 10"
    ran: list[str] = []

    def validate(sql, allowed):
        if "ILIKE" in sql.upper():
            raise SQLRejected("table_not_allowed")
        return sql

    def run(sql, **kwargs):
        ran.append(sql)
        return RunResult(columns=["price"], rows=[], row_count=0, truncated=False, elapsed_ms=1)

    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return exact

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", validate)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, "Qual o preço do azeite?")
    assert out.status == "ok"
    assert out.row_count == 0
    assert all("ILIKE" not in sql.upper() for sql in ran)
    assert gens["n"] == 2  # the one LLM retry, never more


def test_empty_join_retries_once_with_simpler_hint(monkeypatch):
    previous: list[object] = []
    runs = {"n": 0}

    def gen(question, schema, **kwargs):
        previous.append(kwargs.get("previous_error"))
        if kwargs.get("previous_error"):
            return (
                "SELECT p.name, s.quantity FROM products AS p "
                "JOIN stock AS s ON s.product_id = p.id "
                "WHERE p.name ILIKE '%pao%' AND p.name ILIKE '%milho%' LIMIT 10"
            )
        return (
            "SELECT p.name, s.quantity FROM products AS p "
            "JOIN stock AS s ON s.product_id = p.id "
            "WHERE p.name ILIKE '%pao de milho%' LIMIT 10"
        )

    def run(sql, **kwargs):
        runs["n"] += 1
        if "AND" in sql.upper():
            return RunResult(
                columns=["name", "quantity"],
                rows=[["Pão de milho", 18]],
                row_count=1,
                truncated=False,
                elapsed_ms=1,
            )
        return RunResult(
            columns=["name", "quantity"], rows=[], row_count=0, truncated=False, elapsed_ms=1
        )

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, "Têm pão de milho em stock?")
    assert out.status == "ok"
    assert out.row_count == 1
    assert out.rows == [["Pão de milho", 18]]
    assert previous[1] == retry_hint("no_rows")
    # original empty + unaccent wrap + stem rewrite + LLM rewrite with AND patterns
    assert runs["n"] == 4


def test_no_rows_retry_runs_at_most_once(monkeypatch):
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return "SELECT p.price FROM products AS p WHERE p.name = 'caviar' LIMIT 10"

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: RunResult(columns=["price"], rows=[], row_count=0, truncated=False, elapsed_ms=1),
    )
    out = answer_with_sql(COMPANY, "Têm caviar?")
    assert out.status == "ok"
    assert out.row_count == 0
    assert out.attempts == 2
    assert gens["n"] == 2


def test_empty_stock_filter_is_not_retried_without_the_condition(monkeypatch):
    """quantity > 0 matched nothing: a real "not in stock", never retried into a plain product list."""
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return (
            "SELECT p.name FROM products AS p JOIN stock AS s ON s.product_id = p.id "
            "WHERE p.name ILIKE '%batata%' AND s.quantity > 0 LIMIT 10"
        )

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: RunResult(columns=["name"], rows=[], row_count=0, truncated=False, elapsed_ms=1),
    )
    out = answer_with_sql(COMPANY, "Têm batata nova em stock?")
    assert out.status == "ok"
    assert out.row_count == 0
    assert gens["n"] == 1


def test_failed_stock_query_is_an_error_not_an_empty_result(monkeypatch):
    """A query that fails twice is status=error: the answer layer never reads it as 0 in stock."""
    monkeypatch.setattr(
        pipeline,
        "generate_sql",
        lambda *a, **k: "SELECT p.name, s.quantity FROM products AS p JOIN stock AS s ON s.product_id = p.id LIMIT 10",
    )
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)

    def run(*a, **k):
        raise RunnerError("db_error")

    monkeypatch.setattr(pipeline, "run_sql", run)
    out = answer_with_sql(COMPANY, "Têm pão de milho em stock?")
    assert out.status == "error"
    assert out.rows == []
    assert out.attempts == 2


def test_plain_ilike_is_wrapped_with_unaccent_for_typed_words():
    wrapped = _unaccent_text_match(
        "SELECT p.name, s.quantity FROM products AS p "
        "JOIN stock AS s ON s.product_id = p.id "
        "WHERE p.name ILIKE '%pao%' AND p.name ILIKE '%milho%' LIMIT 10",
        "Têm pão de milho em stock?",
    )
    assert wrapped is not None
    assert "UNACCENT(LOWER(p.name))" in wrapped
    assert "UNACCENT(LOWER('%pao%'))" in wrapped
    assert "UNACCENT(LOWER('%milho%'))" in wrapped
    assert "s.product_id = p.id" in wrapped


def test_plural_stem_matches_singular_product_names():
    assert _stem_for_search("queijos") == "queijo"
    stemmed = _stem_ilike_match(
        "SELECT p.name, p.price FROM products AS p "
        "WHERE UNACCENT(LOWER(p.name)) ILIKE UNACCENT(LOWER('%queijos%')) LIMIT 10",
        "Que queijos têm?",
    )
    assert stemmed is not None
    assert "UNACCENT(LOWER('%queijo%'))" in stemmed
    assert "%queijos%" not in stemmed


def test_catalog_plural_empty_result_is_restemmed(monkeypatch):
    """``ILIKE '%queijos%'`` matches nothing; stem retry finds Queijo da Serra."""
    sql = (
        "SELECT p.name, p.price FROM products AS p "
        "WHERE p.name ILIKE '%queijos%' LIMIT 10"
    )
    ran: list[str] = []

    def run(query, **kwargs):
        ran.append(query)
        if "%queijo%" in query.lower() and "unaccent" in query.lower():
            return RunResult(
                columns=["name", "price"],
                rows=[
                    ["Queijo da Serra da Estrela 200 g", "9.90"],
                    ["Queijo de ovelha curado 300 g", "7.50"],
                ],
                row_count=2,
                truncated=False,
                elapsed_ms=1,
            )
        return RunResult(
            columns=["name", "price"], rows=[], row_count=0, truncated=False, elapsed_ms=1
        )

    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: sql)
    monkeypatch.setattr(pipeline, "validate_sql", lambda query, allowed: query)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, "Que queijos têm?")
    assert out.status == "ok"
    assert out.row_count == 2
    assert all("Queijo" in row[0] for row in out.rows)
    assert any("%queijo%" in q.lower() for q in ran)
    assert _is_catalog_question("Que queijos têm?")
    assert _is_catalog_question("What cheeses do you have?")


def test_catalog_question_triggers_no_rows_retry_flag():
    assert _is_catalog_question("Que queijos têm?")
    assert _is_catalog_question("quais produtos têm?")
    assert _is_catalog_question("What cheeses do you have?")
    assert not _is_catalog_question("Qual o horário de abertura?")


def test_strip_unaccent_leaves_lower_ilike():
    stripped = _strip_unaccent_calls(
        "SELECT p.name FROM products AS p WHERE UNACCENT(LOWER(p.name)) "
        "ILIKE UNACCENT(LOWER('%pao%')) LIMIT 10"
    )
    assert stripped == (
        "SELECT p.name FROM products AS p WHERE LOWER(p.name) ILIKE LOWER('%pao%') LIMIT 10"
    )


def test_unaccent_missing_falls_back_once(monkeypatch):
    sql = (
        "SELECT p.name, s.quantity FROM products AS p "
        "JOIN stock AS s ON s.product_id = p.id "
        "WHERE UNACCENT(LOWER(p.name)) ILIKE UNACCENT(LOWER('%pao%')) "
        "AND UNACCENT(LOWER(p.name)) ILIKE UNACCENT(LOWER('%milho%')) LIMIT 10"
    )
    ran: list[str] = []

    def run(query, **kwargs):
        ran.append(query)
        if "UNACCENT" in query.upper():
            raise RunnerError("db_error")
        return RunResult(
            columns=["name", "quantity"],
            rows=[["Pão de milho", 18]],
            row_count=1,
            truncated=False,
            elapsed_ms=1,
        )

    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: sql)
    monkeypatch.setattr(pipeline, "validate_sql", lambda query, allowed: query)
    monkeypatch.setattr(pipeline, "run_sql", run)

    out = answer_with_sql(COMPANY, "Têm pão de milho em stock?")
    assert out.status == "ok"
    assert out.rows == [["Pão de milho", 18]]
    assert len(ran) == 2
    assert "UNACCENT" in ran[0].upper()
    assert "UNACCENT" not in ran[1].upper()
    assert "product_id" in ran[1]


def test_missing_unaccent_is_remembered_so_later_queries_skip_the_failing_run(monkeypatch):
    sql = (
        "SELECT p.name FROM products AS p "
        "WHERE unaccent(lower(p.name)) ILIKE unaccent(lower('%pao%')) LIMIT 10"
    )
    ran: list[str] = []

    def run(sql_text, **kwargs):
        ran.append(sql_text)
        if "unaccent" in sql_text.lower():
            raise RunnerError("db_error")
        return RunResult(columns=["name"], rows=[["Pão de milho"]], row_count=1, truncated=False, elapsed_ms=1)

    monkeypatch.setattr(pipeline, "generate_sql", lambda *a, **k: sql)
    monkeypatch.setattr(pipeline, "validate_sql", lambda s, allowed: s)
    monkeypatch.setattr(pipeline, "run_sql", run)

    first = answer_with_sql(COMPANY, "Têm pao de milho?")
    assert first.rows == [["Pão de milho"]]
    assert [("unaccent" in s.lower()) for s in ran] == [True, False]

    ran.clear()
    second = answer_with_sql(COMPANY, "Têm pao de milho?")
    assert second.rows == [["Pão de milho"]]
    # Known missing: stripped up front, no failing round trip.
    assert [("unaccent" in s.lower()) for s in ran] == [False]


def test_unaccent_rewrite_keeps_quotes_escaped_and_passes_the_real_validator():
    from site_ai_widget.sqlqa.pipeline import _unaccent_text_match
    from site_ai_widget.sqlqa.validator import validate_sql as real_validate

    sql = "SELECT p.name FROM products AS p WHERE p.name ILIKE '%pão d''oro%' LIMIT 10"
    rewritten = _unaccent_text_match(sql, "têm pão d'oro?")
    assert rewritten is not None
    assert "unaccent(lower('%pão d''oro%'))" in rewritten.lower()
    validated = real_validate(rewritten, ALLOWED)
    assert "LIMIT 10" in validated.upper()
    assert "INSERT" not in validated.upper()


def test_plain_plural_word_is_still_stemmed():
    from site_ai_widget.sqlqa.pipeline import _unaccent_text_match

    sql = "SELECT p.name FROM products AS p WHERE p.name ILIKE '%queijos%' LIMIT 10"
    out = _unaccent_text_match(sql, "Que queijos têm?")
    assert out is not None
    assert "'%queijo%'" in out



@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Que queijos têm?", True),
        ("Têm queijos?", True),
        ("Quais vinhos vendem?", True),
        ("What cheeses do you have?", True),
        ("Do you sell bread?", True),
        ("O queijo da serra tem lactose?", False),
        ("Qual o preço do azeite?", False),
    ],
)
def test_catalog_question_is_detected_by_shape_not_product_words(question, expected):
    from site_ai_widget.sqlqa.pipeline import _is_catalog_question

    assert _is_catalog_question(question) is expected


def test_catalog_question_with_an_empty_stock_filter_is_not_retried(monkeypatch):
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return (
            "SELECT p.name, p.price FROM products AS p JOIN stock AS s ON s.product_id = p.id "
            "WHERE unaccent(lower(p.name)) ILIKE unaccent(lower('%queijo%')) AND s.quantity > 0 LIMIT 10"
        )

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: RunResult(columns=["name", "price"], rows=[], row_count=0, truncated=False, elapsed_ms=1),
    )
    out = answer_with_sql(COMPANY, "Que queijos têm em stock?")
    assert out.row_count == 0
    assert gens["n"] == 1


def test_catalog_question_without_numeric_filter_is_retried_once(monkeypatch):
    gens = {"n": 0}

    def gen(*a, **k):
        gens["n"] += 1
        return "SELECT p.name, p.price FROM products AS p WHERE p.name ILIKE '%queijos%' LIMIT 10"

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "validate_sql", lambda sql, allowed: sql)
    monkeypatch.setattr(
        pipeline,
        "run_sql",
        lambda *a, **k: RunResult(columns=["name", "price"], rows=[], row_count=0, truncated=False, elapsed_ms=1),
    )
    answer_with_sql(COMPANY, "Que queijos têm?")
    assert gens["n"] == 2
