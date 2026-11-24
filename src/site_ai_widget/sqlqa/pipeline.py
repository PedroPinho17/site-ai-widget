"""Text-to-SQL orchestration: retrieve → generate → validate → run (one retry).

Never log the question, SQL, schema text or rows. Status, reason codes, company_id
and attempt counts are fine. SqlAnswer never carries SQL or DB error text.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

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

        return _finish(
            company_id,
            SqlAnswer(
                status="ok",
                columns=list(result.columns),
                rows=[list(row) for row in result.rows],
                row_count=result.row_count,
                truncated=result.truncated,
                attempts=attempts,
            ),
        )

    # Unreachable: the loop always returns; keep a safe fallback for type checkers.
    return _finish(
        company_id,
        SqlAnswer(status="error", reason=last_reason or "db_error", attempts=attempts),
    )


def _finish(company_id: str, answer: SqlAnswer) -> SqlAnswer:
    log.info(
        "sqlqa/pipeline company=%s status=%s reason=%s attempts=%s",
        company_id,
        answer.status,
        answer.reason,
        answer.attempts,
    )
    return answer
