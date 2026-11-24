"""Text-to-SQL orchestration: retrieve → generate → validate → run (one retry).

Never log the question, SQL, schema text or rows. Status, reason codes, company_id
and attempt counts are fine. SqlAnswer never carries SQL or DB error text.
"""
from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp

from site_ai_widget.llm.client import complete_chat
from site_ai_widget.sqlqa.generator import generate_sql, retry_hint
from site_ai_widget.sqlqa.retrieval import retrieve_schema
from site_ai_widget.sqlqa.runner import RunnerError, run_sql
from site_ai_widget.sqlqa.validator import SQLRejected, validate_sql

log = logging.getLogger("site_ai_widget")

Status = Literal["ok", "no_sql", "refused", "error"]


@dataclass(frozen=True)
class SqlAnswer:
    status: Status
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    reason: str | None = None
    attempts: int = 1
    tables: list[str] = field(default_factory=list)


def answer_with_sql(
    company_id: str,
    question: str,
    *,
    llm_complete: Callable[..., str] | None = None,
) -> SqlAnswer:
    """Retrieve schema, generate/validate/run SQL, with at most one retry.

    At most two ``generate_sql`` calls. Validator refusals and ``db_error`` retry
    once with a fixed ``retry_hint``; ``timeout`` and ``result_too_large`` do not.
    """
    complete = llm_complete or complete_chat
    schema = retrieve_schema(company_id, question)
    if not schema.tables:
        return _finish(company_id, SqlAnswer(status="no_sql", attempts=1))

    previous_error: str | None = None
    last_reason: str | None = None
    attempts = 0

    for attempt in (1, 2):
        attempts = attempt
        sql = generate_sql(
            question,
            schema,
            company_id=company_id,
            previous_error=previous_error,
            llm_complete=complete,
        )
        if sql is None:
            return _finish(company_id, SqlAnswer(status="no_sql", attempts=attempts))

        refused = False
        reason_code = ""
        try:
            validated = validate_sql(sql, schema.allowed)
        except SQLRejected as exc:
            refused = True
            reason_code = exc.reason

        if refused:
            last_reason = reason_code
            if attempt < 2:
                previous_error = retry_hint(reason_code)
                continue
            return _finish(
                company_id,
                SqlAnswer(status="refused", reason=last_reason, attempts=attempts),
            )

        try:
            result = run_sql(validated, company_id=company_id, allowed=schema.allowed)
        except RunnerError as exc:
            last_reason = exc.reason
            if exc.reason == "db_error" and attempt < 2:
                previous_error = retry_hint("db_error")
                continue
            # timeout / result_too_large / not_validated / second db_error: stop
            status: Status = "refused" if exc.reason == "not_validated" else "error"
            return _finish(
                company_id,
                SqlAnswer(status=status, reason=exc.reason, attempts=attempts),
            )

        if result.row_count == 0:
            result = _relax_empty_text_match(
                validated,
                result,
                question=question,
                company_id=company_id,
                allowed=schema.allowed,
            )
        if result.row_count == 0 and attempt < 2 and (
            _has_text_equality(validated)
            or (_has_join(validated) and not _has_numeric_filter(validated))
        ):
            # Text equality or a join that matched nothing: rewrite once (ILIKE /
            # multi-word / drop a join). A numeric filter that matched nothing
            # (s.quantity > 0, price < 5) is a real empty result: it is never sent
            # back, so a retry cannot drop the stock condition and list the product
            # as if it were available.
            log.info("sqlqa/pipeline company=%s stage=no_rows attempts=%s", company_id, attempts)
            previous_error = retry_hint("no_rows")
            continue

        return _finish(
            company_id,
            SqlAnswer(
                status="ok",
                columns=list(result.columns),
                rows=[list(row) for row in result.rows],
                row_count=result.row_count,
                truncated=result.truncated,
                attempts=attempts,
                tables=_table_names(validated),
            ),
        )

    # Unreachable: the loop always returns; keep a safe fallback for type checkers.
    return _finish(
        company_id,
        SqlAnswer(status="error", reason=last_reason or "db_error", attempts=attempts),
    )


def _table_names(sql: str) -> list[str]:
    """Table names only. Never the SQL text."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.SqlglotError:
        return []
    names: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if name and name not in names:
            names.append(name)
    return names[:4]


def _string_literal(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Literal) and node.is_string:
        return node.this
    if isinstance(node, exp.Lower) and isinstance(node.this, exp.Literal) and node.this.is_string:
        return node.this.this
    return None


def _in_where_filter(node: exp.Expression) -> bool:
    """True when ``node`` is a plain WHERE filter: only AND / OR / ( ) up to WHERE.

    An equality under NOT, CASE, a function, JOIN ... ON, HAVING or the select list
    is left alone: relaxing it would change which rows are excluded or what a
    column returns.
    """
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return True
        if not isinstance(parent, (exp.And, exp.Or, exp.Paren)):
            return False
        parent = parent.parent
    return False


def _text_equalities(tree: exp.Expression) -> list[tuple[exp.EQ, exp.Column, str]]:
    """``column = 'text'`` / ``lower(column) = 'text'`` filters in WHERE clauses."""
    found: list[tuple[exp.EQ, exp.Column, str]] = []
    for eq in tree.find_all(exp.EQ):
        if not _in_where_filter(eq):
            continue
        left, right = eq.this, eq.expression
        text = _string_literal(left)
        other: exp.Expression = right
        if text is None:
            text = _string_literal(right)
            other = left
        if text is None:
            continue
        column = other.this if isinstance(other, exp.Lower) else other
        if isinstance(column, exp.Column):
            found.append((eq, column, text))
    return found


def _fold(text: str) -> str:
    """Casefolded, accent-free, single-spaced (``Pão`` -> ``pao``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.casefold().split())


def _typed_by_visitor(text: str, question: str) -> bool:
    """The literal is a word the visitor wrote, not a filter value the model chose.

    ``status = 'published'`` stays exact (ILIKE would also match ``unpublished``);
    only the visitor's own search term (``azeite``) is widened.
    """
    word = _fold(text)
    return len(word) >= 3 and any(ch.isalpha() for ch in word) and word in _fold(question)


def _parse(sql: str) -> exp.Expression | None:
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.SqlglotError:
        return None


def _has_text_equality(sql: str) -> bool:
    tree = _parse(sql)
    return tree is not None and bool(_text_equalities(tree))


_NUMERIC_COMPARISONS = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)


def _has_numeric_filter(sql: str) -> bool:
    """A WHERE / ON comparison against a number literal (quantity > 0, price <= 5)."""
    tree = _parse(sql)
    if tree is None:
        return True  # unknown shape: do not retry
    for node in tree.find_all(*_NUMERIC_COMPARISONS):
        for side in (node.this, node.expression):
            if isinstance(side, exp.Literal) and not side.is_string:
                return True
    return False


def _has_join(sql: str) -> bool:
    tree = _parse(sql)
    return tree is not None and any(True for _ in tree.find_all(exp.Join))


def _relax_text_match(sql: str, question: str) -> str | None:
    """Turn ``lower(col) = 'word'`` into ``col ILIKE '%word%'`` for typed words.

    A shop question names part of a product (``azeite``); the stored name is longer.
    Only WHERE filters whose literal the visitor typed change; ``%``, ``_`` and
    backslash in the word are escaped so they match literally. Returns None when
    there is nothing to relax. Never logs the SQL.
    """
    tree = _parse(sql)
    if tree is None:
        return None
    changed = False
    for eq, column, text in _text_equalities(tree):
        if not _typed_by_visitor(text, question):
            continue
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        eq.replace(exp.ILike(this=column.copy(), expression=exp.Literal.string(f"%{escaped}%")))
        changed = True
    if not changed:
        return None
    return tree.sql(dialect="postgres")


def _relax_empty_text_match(sql: str, result, *, question: str, company_id: str, allowed) -> object:
    """Re-run once with partial ILIKE when a typed text equality matched nothing."""
    relaxed = _relax_text_match(sql, question)
    if relaxed is None:
        log.info("sqlqa/pipeline company=%s stage=relax_text skipped=1 rows=0", company_id)
        return result
    try:
        relaxed_sql = validate_sql(relaxed, allowed)
    except SQLRejected as exc:
        log.info(
            "sqlqa/pipeline company=%s stage=relax_text validation=fail reason=%s",
            company_id,
            exc.reason,
        )
        return result
    try:
        relaxed_result = run_sql(relaxed_sql, company_id=company_id, allowed=allowed)
    except RunnerError as exc:
        log.info(
            "sqlqa/pipeline company=%s stage=relax_text validation=pass rows=0 reason=%s",
            company_id,
            exc.reason,
        )
        return result
    log.info(
        "sqlqa/pipeline company=%s stage=relax_text validation=pass rows=%s",
        company_id,
        relaxed_result.row_count,
    )
    return relaxed_result if relaxed_result.row_count > 0 else result


def _finish(company_id: str, answer: SqlAnswer) -> SqlAnswer:
    log.info(
        "sqlqa/pipeline company=%s stage=done status=%s reason=%s attempts=%s rows=%s",
        company_id,
        answer.status,
        answer.reason,
        answer.attempts,
        answer.row_count,
    )
    return answer
