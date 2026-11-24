"""Shared ask flow: SQL first, then document RAG, then a fixed safe message.

``POST /ask`` and the public widget both call :func:`answer_question`. The response
never includes SQL, schema text, prompts or database error details. Row values are
untrusted data and are not fed back into an LLM here.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError

log = logging.getLogger("site_ai_widget")

_NONE_MESSAGE = "I could not answer that from the connected database or your documents."
_MAX_SQL_ROWS = 500
_HARD_QUESTION_CHARS = 1000


class AskCitation(BaseModel):
    document_id: str
    document_name: str
    page: int


class AskResponse(BaseModel):
    mode: Literal["sql", "docs", "none"]
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None
    row_count: int | None = None
    truncated: bool | None = None
    answer: str | None = None
    sources: list[AskCitation] | None = None
    message: str | None = None


def answer_question(company_id: str, question: str, *, sql_fn=None, docs_fn=None) -> AskResponse:
    """Try text-to-SQL; on failure fall back to document Q&A.

    ``company_id`` is chosen by the caller (the JWT on ``/ask``, the widget key
    on the public route). It is never taken from the question body.
    """
    settings = get_settings()
    max_chars = min(settings.max_question_chars, _HARD_QUESTION_CHARS)
    question = (question or "").strip()
    if not question:
        raise ApiError("validation_error", 422)
    if len(question) > max_chars:
        raise ApiError("question_too_long", 422, n=max_chars)

    if sql_fn is None or docs_fn is None:
        # Looked up on the owning modules at call time (patchable in tests). The
        # service never imports from a route module.
        from site_ai_widget.docs import ask as docs_ask
        from site_ai_widget.sqlqa import pipeline

        if sql_fn is None:
            sql_fn = pipeline.answer_with_sql
        if docs_fn is None:
            docs_fn = docs_ask.ask_documents

    company_id = str(company_id)
    sql_answer = None
    try:
        sql_answer = sql_fn(company_id, question)
    except Exception as exc:
        log.warning("ask/sql failed: %s", type(exc).__name__)

    if sql_answer is not None and sql_answer.status == "ok":
        rows = sql_answer.rows[:_MAX_SQL_ROWS]
        return AskResponse(
            mode="sql",
            columns=list(sql_answer.columns),
            rows=rows,
            row_count=min(sql_answer.row_count, len(rows)),
            truncated=sql_answer.truncated or sql_answer.row_count > _MAX_SQL_ROWS,
        )

    try:
        docs = docs_fn(company_id=company_id, question=question)
    except ApiError as exc:
        log.warning("ask/docs failed: %s", exc.code)
        return AskResponse(mode="none", message=_NONE_MESSAGE)
    except Exception as exc:
        log.warning("ask/docs failed: %s", type(exc).__name__)
        return AskResponse(mode="none", message=_NONE_MESSAGE)

    # Decide from search hits (matched), not from whether the answer cited [n].
    if not docs.get("matched"):
        return AskResponse(mode="none", message=_NONE_MESSAGE)

    return AskResponse(
        mode="docs",
        answer=docs["answer"],
        sources=[AskCitation(**c) for c in docs["citations"]],
    )
