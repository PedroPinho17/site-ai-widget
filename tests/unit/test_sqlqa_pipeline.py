"""Text-to-SQL pipeline unit tests with fakes."""
from __future__ import annotations

import logging

import pytest

from site_ai_widget.sqlqa import pipeline
from site_ai_widget.sqlqa.generator import RETRY_HINTS, retry_hint
from site_ai_widget.sqlqa.pipeline import SqlAnswer, answer_with_sql
from site_ai_widget.sqlqa.retrieval import ColumnInfo, SchemaSlice, TableInfo
from site_ai_widget.sqlqa.runner import RunnerError, RunResult
from site_ai_widget.sqlqa.validator import SQLRejected

COMPANY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SENTINEL_Q = "SENTINEL-QUESTION-ZZZ"
SENTINEL_SQL = "SELECT name FROM products WHERE sku = 'SENTINEL-SKU'"
SENTINEL_ROW = "SENTINEL-ROW-VALUE"

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


def test_exports():
    from site_ai_widget import sqlqa

    assert sqlqa.SqlAnswer is SqlAnswer
    assert sqlqa.answer_with_sql is answer_with_sql
