"""Text-to-SQL orchestration: retrieve → generate → validate → run (one retry).

Never log the question, SQL, schema text or rows. Status, reason codes, company_id
and attempt counts are fine. SqlAnswer never carries SQL or DB error text.
"""
from __future__ import annotations

import logging
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp

from site_ai_widget.llm.client import complete_chat
from site_ai_widget.sqlqa.catalog import is_catalog_question
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
            result = _run_sql_with_unaccent_fallback(
                validated, company_id=company_id, allowed=schema.allowed
            )
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
            or (
                (_has_join(validated) or is_catalog_question(question))
                and not _has_numeric_filter(validated)
            )
        ):
            # Text equality, a join that matched nothing, or a catalog question
            # ("que queijos têm?"): rewrite once (ILIKE / stem / drop a join).
            # A numeric filter that matched nothing (s.quantity > 0, price < 5)
            # is a real empty result, catalog question or not: a retry must never
            # drop the stock condition and list products as if they were available.
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


def _unaccent_call(inner: exp.Expression) -> exp.Anonymous:
    return exp.Anonymous(this="unaccent", expressions=[exp.Lower(this=inner)])


def _is_unaccent_call(node: exp.Expression | None) -> bool:
    return (
        isinstance(node, exp.Anonymous)
        and str(node.this).lower() == "unaccent"
    )


def _ilike_unaccent(column: exp.Column, pattern: str) -> exp.ILike:
    """``unaccent(lower(col)) ILIKE unaccent(lower('%word%'))``."""
    return exp.ILike(
        this=_unaccent_call(column.copy()),
        expression=_unaccent_call(exp.Literal.string(pattern)),
    )


def _escape_like_literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _stem_for_search(word: str) -> str:
    """Singular-ish stem so ``queijos`` matches ``Queijo…`` via ILIKE ``%queijo%``.

    Strips a trailing plural ``es`` / ``s`` when the word is long enough to stay
    discriminative (``queijos`` → ``queijo``, ``cheeses`` → ``chees``).
    """
    text = word.strip()
    folded = _fold(text)
    # Prefer -es over -s (``boxes`` → ``box``); plain -s covers ``queijos`` → ``queijo``.
    if len(folded) >= 6 and folded.endswith("es") and text.lower().endswith("es"):
        return text[:-2]
    if len(folded) >= 5 and folded.endswith("s") and not folded.endswith("ss") and text.lower().endswith(
        "s"
    ):
        return text[:-1]
    return text


def _like_pattern_for_word(word: str) -> str:
    stem = _stem_for_search(word)
    return f"%{_escape_like_literal(stem)}%"


def _relax_text_match(sql: str, question: str) -> str | None:
    """Turn ``lower(col) = 'word'`` into accent-insensitive ILIKE for typed words.

    A shop question names part of a product (``azeite`` / ``queijos``); the stored
    name is longer and may use different accents or the singular. Only WHERE
    filters whose literal the visitor typed change; ``%``, ``_`` and backslash in
    the word are escaped so they match literally. Returns None when there is
    nothing to relax. Never logs the SQL.
    """
    tree = _parse(sql)
    if tree is None:
        return None
    changed = False
    for eq, column, text in _text_equalities(tree):
        if not _typed_by_visitor(text, question):
            continue
        eq.replace(_ilike_unaccent(column, _like_pattern_for_word(text)))
        changed = True
    if not changed:
        return None
    return tree.sql(dialect="postgres")


def _ilike_pattern_text(node: exp.Expression) -> str | None:
    """String pattern of an ILIKE RHS (literal, ``lower('…')``, or ``unaccent(lower('…'))``)."""
    current: exp.Expression = node
    if _is_unaccent_call(current) and current.expressions:
        current = current.expressions[0]
    text = _string_literal(current)
    if text is not None:
        return text
    if isinstance(current, exp.Literal) and current.is_string:
        return current.this
    return None


def _ilike_column(node: exp.Expression) -> exp.Column | None:
    """Column under ``col``, ``lower(col)``, or ``unaccent(lower(col))``."""
    current: exp.Expression = node
    if _is_unaccent_call(current) and current.expressions:
        current = current.expressions[0]
    if isinstance(current, exp.Lower):
        current = current.this
    return current if isinstance(current, exp.Column) else None


def _rewrite_ilike_filters(sql: str, question: str, *, require_plain: bool) -> str | None:
    """Rewrite typed ILIKE filters to unaccent + singular stem.

    When ``require_plain`` is true, only rewrite filters that are not already
    wrapped in ``unaccent`` (first pass). When false, also re-stem patterns that
    already use ``unaccent`` (``%queijos%`` → ``%queijo%``).
    """
    tree = _parse(sql)
    if tree is None:
        return None
    changed = False
    for ilike in tree.find_all(exp.ILike):
        if not _in_where_filter(ilike):
            continue
        already = _is_unaccent_call(ilike.this)
        if require_plain and already:
            continue
        pattern = _ilike_pattern_text(ilike.expression)
        if pattern is None:
            continue
        core = pattern.strip("%")
        if not core or not _typed_by_visitor(core, question):
            continue
        column = _ilike_column(ilike.this)
        if column is None:
            continue
        new_pattern = _like_pattern_for_word(core)
        if already and new_pattern == pattern:
            continue
        ilike.replace(_ilike_unaccent(column, new_pattern))
        changed = True
    if not changed:
        return None
    return tree.sql(dialect="postgres")


def _unaccent_text_match(sql: str, question: str) -> str | None:
    """Wrap plain ``col ILIKE '%word%'`` with ``unaccent(lower(...))`` for typed words."""
    return _rewrite_ilike_filters(sql, question, require_plain=True)


def _stem_ilike_match(sql: str, question: str) -> str | None:
    """Singularize typed ILIKE patterns (``%queijos%`` → ``%queijo%``) with unaccent."""
    return _rewrite_ilike_filters(sql, question, require_plain=False)


def _strip_unaccent_calls(sql: str) -> str | None:
    """Remove ``unaccent(...)`` wrappers so the query can run without the extension."""
    tree = _parse(sql)
    if tree is None:
        return None
    changed = False
    for func in list(tree.find_all(exp.Anonymous)):
        if str(func.this).lower() != "unaccent" or len(func.expressions) != 1:
            continue
        func.replace(func.expressions[0].copy())
        changed = True
    if not changed:
        return None
    return tree.sql(dialect="postgres")


# Companies whose client database has no ``unaccent`` (learned from a failed run
# that worked once stripped). Their queries are stripped up front for a while, so
# a question does not pay a failing database round trip on every query.
_UNACCENT_MISSING_TTL_SECONDS = 600
_unaccent_missing: dict[str, float] = {}


def _unaccent_known_missing(company_id: str) -> bool:
    until = _unaccent_missing.get(company_id)
    if until is None:
        return False
    if until < time.monotonic():
        _unaccent_missing.pop(company_id, None)
        return False
    return True


def _run_sql_with_unaccent_fallback(sql: str, *, company_id: str, allowed):
    """Run SQL; if ``unaccent`` is missing, strip it once and retry."""
    if _unaccent_known_missing(company_id):
        stripped = _strip_unaccent_calls(sql)
        if stripped is not None:
            return run_sql(validate_sql(stripped, allowed), company_id=company_id, allowed=allowed)
    try:
        return run_sql(sql, company_id=company_id, allowed=allowed)
    except RunnerError as exc:
        if exc.reason != "db_error":
            raise
        stripped = _strip_unaccent_calls(sql)
        if stripped is None:
            raise
        try:
            validated = validate_sql(stripped, allowed)
        except SQLRejected:
            raise exc from None
        try:
            result = run_sql(validated, company_id=company_id, allowed=allowed)
        except RunnerError:
            raise exc from None
        _unaccent_missing[company_id] = time.monotonic() + _UNACCENT_MISSING_TTL_SECONDS
        log.info(
            "sqlqa/pipeline company=%s stage=unaccent_fallback status=ok",
            company_id,
        )
        return result


def _relax_empty_text_match(sql: str, result, *, question: str, company_id: str, allowed) -> object:
    """Re-run once with partial / accent-insensitive ILIKE when a text filter matched nothing."""
    candidates = (
        _relax_text_match(sql, question),
        _unaccent_text_match(sql, question),
        _stem_ilike_match(sql, question),
    )
    tried = False
    for relaxed in candidates:
        if relaxed is None:
            continue
        tried = True
        try:
            relaxed_sql = validate_sql(relaxed, allowed)
        except SQLRejected as exc:
            log.info(
                "sqlqa/pipeline company=%s stage=relax_text validation=fail reason=%s",
                company_id,
                exc.reason,
            )
            continue
        try:
            relaxed_result = _run_sql_with_unaccent_fallback(
                relaxed_sql, company_id=company_id, allowed=allowed
            )
        except RunnerError as exc:
            log.info(
                "sqlqa/pipeline company=%s stage=relax_text validation=pass rows=0 reason=%s",
                company_id,
                exc.reason,
            )
            continue
        log.info(
            "sqlqa/pipeline company=%s stage=relax_text validation=pass rows=%s",
            company_id,
            relaxed_result.row_count,
        )
        if relaxed_result.row_count > 0:
            return relaxed_result
    if not tried:
        log.info("sqlqa/pipeline company=%s stage=relax_text skipped=1 rows=0", company_id)
    return result


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
