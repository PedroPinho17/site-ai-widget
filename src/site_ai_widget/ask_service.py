"""Shared ask flow: SQL first, then document RAG, then a fixed safe message.

``POST /ask`` and the public widget both call :func:`answer_question`. The response
never includes SQL, schema text, prompts or database error details. SQL mode includes
a short natural-language ``answer`` built from the result rows (deterministic, with an
optional low-temperature LLM rephrase that receives only the capped rows).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Literal

from pydantic import BaseModel

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError
from site_ai_widget.greetings import small_talk_reply
from site_ai_widget.sqlqa.present import format_sql_answer_deterministic, present_sql_answer

log = logging.getLogger("site_ai_widget")

_NONE_MESSAGE = "I could not answer that from the connected database or your documents."
_MAX_SQL_ROWS = 500
_HARD_QUESTION_CHARS = 1000


class AskCitation(BaseModel):
    document_id: str
    document_name: str
    page: int


class AskResponse(BaseModel):
    mode: Literal["sql", "docs", "none", "chat"]
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    row_count: int | None = None
    truncated: bool | None = None
    answer: str | None = None
    sources: list[AskCitation] | None = None
    tables: list[str] | None = None
    elapsed_ms: int | None = None
    message: str | None = None


def answer_question(
    company_id: str,
    question: str,
    *,
    lang: str | None = None,
    sql_fn=None,
    docs_fn=None,
    present_fn=None,
) -> AskResponse:
    """Try text-to-SQL; on failure fall back to document Q&A.

    ``company_id`` is chosen by the caller (the JWT on ``/ask``, the widget key
    on the public route). It is never taken from the question body.
    ``lang`` is ``pt`` or ``en`` for the SQL answer phrasing (widget ``data-lang``).
    """
    settings = get_settings()
    max_chars = min(settings.max_question_chars, _HARD_QUESTION_CHARS)
    started = time.perf_counter()
    question = (question or "").strip()
    if not question:
        raise ApiError("validation_error", 422)
    if len(question) > max_chars:
        raise ApiError("question_too_long", 422, n=max_chars)

    reply = small_talk_reply(question, lang)
    if reply is not None:
        log.info("ask/sql stage=greeting")
        return AskResponse(mode="chat", answer=reply, elapsed_ms=_elapsed(started))

    if sql_fn is None or docs_fn is None:
        # Looked up on the owning modules at call time (patchable in tests). The
        # service never imports from a route module.
        from site_ai_widget.docs import ask as docs_ask
        from site_ai_widget.sqlqa import pipeline

        if sql_fn is None:
            sql_fn = pipeline.answer_with_sql
        if docs_fn is None:
            docs_fn = docs_ask.ask_documents

    if present_fn is None:
        present_fn = present_sql_answer

    company_id = str(company_id)
    sql_answer = None
    try:
        sql_answer = sql_fn(company_id, question)
    except Exception as exc:
        log.warning("ask/sql failed: %s", type(exc).__name__)

    if sql_answer is None:
        log.info("ask/sql stage=skip reason=error")
    elif sql_answer.status != "ok":
        log.info(
            "ask/sql stage=skip status=%s rows=%s",
            sql_answer.status,
            sql_answer.row_count,
        )
    else:
        log.info("ask/sql stage=present rows=%s", len(sql_answer.rows))

    if sql_answer is not None and sql_answer.status == "ok":
        rows = sql_answer.rows[:_MAX_SQL_ROWS]
        columns = list(sql_answer.columns)
        truncated = bool(sql_answer.truncated or sql_answer.row_count > _MAX_SQL_ROWS)
        currency = _company_currency(company_id) if present_fn is present_sql_answer else None
        log.info(
            "ask/sql stage=present rows=%s lang=%s currency=%s",
            len(rows),
            lang or "en",
            bool(currency),
        )
        try:
            answer = present_fn(
                columns,
                rows,
                lang,
                truncated=truncated,
                question=question,
                currency=currency,
            )
        except Exception as exc:
            log.info("ask/sql stage=fallback reason=present_error error=%s", type(exc).__name__)
            answer = format_sql_answer_deterministic(
                columns, rows, lang, truncated=truncated, currency=currency
            )
        return AskResponse(
            mode="sql",
            columns=columns,
            rows=rows,
            row_count=min(sql_answer.row_count, len(rows)),
            truncated=truncated,
            answer=answer,
            tables=list(getattr(sql_answer, "tables", []) or []),
            elapsed_ms=_elapsed(started),
        )

    try:
        docs = docs_fn(company_id=company_id, question=question)
    except ApiError as exc:
        log.warning("ask/docs failed: %s", exc.code)
        return AskResponse(mode="none", message=_NONE_MESSAGE, elapsed_ms=_elapsed(started))
    except Exception as exc:
        log.warning("ask/docs failed: %s", type(exc).__name__)
        return AskResponse(mode="none", message=_NONE_MESSAGE, elapsed_ms=_elapsed(started))

    # Decide from search hits (matched), not from whether the answer cited [n].
    if not docs.get("matched"):
        return AskResponse(mode="none", message=_NONE_MESSAGE, elapsed_ms=_elapsed(started))

    return AskResponse(
        mode="docs",
        answer=docs["answer"],
        sources=[AskCitation(**c) for c in docs["citations"]],
        elapsed_ms=_elapsed(started),
    )


def _elapsed(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _company_currency(company_id: str) -> str | None:
    try:
        from site_ai_widget.db.pool import connection

        with connection() as conn:
            row = conn.execute(
                "SELECT currency FROM widget_settings WHERE company_id = %s",
                (str(company_id),),
            ).fetchone()
    except Exception:
        log.info("ask/sql stage=currency status=unavailable")
        return None
    if not row or not row[0]:
        return None
    return str(row[0])
