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

from site_ai_widget.answer_checks import is_no_information
from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError
from site_ai_widget.greetings import small_talk_reply
from site_ai_widget.reply_lang import resolve_answer_lang
from site_ai_widget.sqlqa.present import (
    NOT_ANSWERED,
    format_sql_answer_deterministic,
    present_sql_answer,
)

log = logging.getLogger("site_ai_widget")

_NONE_MESSAGE = "I could not answer that from the connected database or your documents."
# Same wording as the widget's own unknown-answer copy (embed.js emptyAnswer).
_UNKNOWN_ANSWER = {
    "pt": "Não consegui responder a isso.",
    "en": "I could not answer that.",
}
_BUSY_MESSAGE = {
    "pt": "Estou com muitos pedidos neste momento. Tente novamente daqui a um minuto.",
    "en": "I am receiving many requests right now. Please try again in a minute.",
}
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
    # Language of this answer; clients send it back as previous_lang.
    lang: Literal["pt", "en"] | None = None
    message: str | None = None
    # "busy": every LLM key is cooling down. ``answer`` holds a friendly pt/en
    # text; clients may show their own localized copy. Never a provider detail.
    notice: Literal["busy"] | None = None


def answer_question(
    company_id: str,
    question: str,
    *,
    lang: str | None = None,
    previous_lang: str | None = None,
    sql_fn=None,
    docs_fn=None,
    present_fn=None,
) -> AskResponse:
    """Try text-to-SQL; on failure fall back to document Q&A.

    ``company_id`` is chosen by the caller (the JWT on ``/ask``, the widget key
    on the public route). It is never taken from the question body.
    ``lang`` is the widget ``data-lang`` / request hint; the answer language
    follows the visitor question (see :func:`resolve_answer_lang`).
    ``previous_lang`` is used for short follow-ups (``ok``, ``Olá``).
    """
    settings = get_settings()
    max_chars = min(settings.max_question_chars, _HARD_QUESTION_CHARS)
    started = time.perf_counter()
    question = (question or "").strip()
    if not question:
        raise ApiError("validation_error", 422)
    if len(question) > max_chars:
        raise ApiError("question_too_long", 422, n=max_chars)

    company_id = str(company_id)
    effective_lang = resolve_answer_lang(
        question,
        request_lang=lang,
        previous_lang=previous_lang,
        company_locale=_company_locale(company_id),
    )

    reply = small_talk_reply(question, effective_lang)
    if reply is not None:
        log.info("ask/sql stage=greeting")
        return AskResponse(mode="chat", answer=reply, elapsed_ms=_elapsed(started), lang=effective_lang)

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

    sql_answer = None
    llm_busy = False
    try:
        sql_answer = sql_fn(company_id, question)
    except ApiError as exc:
        llm_busy = exc.code == "llm_unavailable"
        log.warning("ask/sql failed: %s", exc.code)
    except Exception as exc:
        log.warning("ask/sql failed: %s", type(exc).__name__)
    if sql_answer is not None and getattr(sql_answer, "reason", None) == "llm_busy":
        llm_busy = True

    if sql_answer is None:
        log.info("ask/sql stage=skip reason=error")
    elif sql_answer.status != "ok":
        log.info(
            "ask/sql stage=skip status=%s rows=%s",
            sql_answer.status,
            sql_answer.row_count,
        )
    elif not sql_answer.rows:
        # Catalog miss: try documents next (same as refused / no_sql).
        log.info("ask/sql stage=skip status=ok rows=0")
    else:
        log.info("ask/sql stage=present rows=%s", len(sql_answer.rows))

    if sql_answer is not None and sql_answer.status == "ok" and sql_answer.rows:
        rows = sql_answer.rows[:_MAX_SQL_ROWS]
        columns = list(sql_answer.columns)
        truncated = bool(sql_answer.truncated or sql_answer.row_count > _MAX_SQL_ROWS)
        currency = _company_currency(company_id)
        # "e mais N produtos" only for product lists; stores or categories are "resultados".
        tables = list(getattr(sql_answer, "tables", []) or [])
        products = not tables or "products" in tables
        log.info(
            "ask/sql stage=present rows=%s lang=%s currency=%s",
            len(rows),
            effective_lang or "en",
            bool(currency),
        )
        try:
            answer = present_fn(
                columns,
                rows,
                effective_lang,
                truncated=truncated,
                question=question,
                currency=currency,
                products=products,
            )
        except Exception as exc:
            log.info("ask/sql stage=fallback reason=present_error error=%s", type(exc).__name__)
            answer = format_sql_answer_deterministic(
                columns,
                rows,
                effective_lang,
                truncated=truncated,
                currency=currency,
                products=products,
            )
        if answer == NOT_ANSWERED or is_no_information(answer):
            # The rows do not answer this question (e.g. opening hours for a
            # delivery question): never present them, try documents instead.
            log.info("ask/sql stage=skip reason=not_answered rows=%s", len(rows))
        else:
            return AskResponse(
                mode="sql",
                columns=columns,
                rows=rows,
                row_count=min(sql_answer.row_count, len(rows)),
                truncated=truncated,
                answer=answer,
                tables=list(getattr(sql_answer, "tables", []) or []),
                elapsed_ms=_elapsed(started),
                lang=effective_lang,
            )

    try:
        docs = docs_fn(company_id=company_id, question=question)
    except ApiError as exc:
        if exc.code == "llm_unavailable":
            log.warning("ask reason=llm_busy")
            return _busy_response(effective_lang, started)
        log.warning("ask/docs failed: %s", exc.code)
        return _unknown_response(effective_lang, started)
    except Exception as exc:
        log.warning("ask/docs failed: %s", type(exc).__name__)
        return _unknown_response(effective_lang, started)

    # Decide from search hits (matched), not from whether the answer cited [n].
    if not docs.get("matched"):
        if llm_busy:
            # The SQL stage could not run, so "not found" would be a guess.
            log.warning("ask reason=llm_busy")
            return _busy_response(effective_lang, started)
        return _unknown_response(effective_lang, started)

    if is_no_information(docs.get("answer") or ""):
        # "Não consta nos documentos…": an unknown answer, worded like the
        # others, with no chips and counted as unanswered.
        log.info("ask/docs stage=none reason=no_information")
        return _unknown_response(effective_lang, started)
    citations = [AskCitation(**c) for c in docs.get("citations") or []]
    return AskResponse(
        mode="docs",
        answer=docs["answer"],
        sources=citations,
        elapsed_ms=_elapsed(started), lang=effective_lang,
    )


def _busy_message(lang: str | None) -> str:
    # Portuguese is the product default when the language is not known yet.
    return _BUSY_MESSAGE["en"] if lang == "en" else _BUSY_MESSAGE["pt"]


def _unknown_response(lang: str | None, started: float) -> AskResponse:
    return AskResponse(
        mode="none",
        answer=_UNKNOWN_ANSWER["en"] if lang == "en" else _UNKNOWN_ANSWER["pt"],
        message=_NONE_MESSAGE,
        elapsed_ms=_elapsed(started),
        lang=lang,
    )


def _busy_response(lang: str | None, started: float) -> AskResponse:
    return AskResponse(
        mode="none",
        answer=_busy_message(lang),
        notice="busy",
        elapsed_ms=_elapsed(started),
        lang=lang,
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


def _company_locale(company_id: str) -> str | None:
    """Widget language from settings (``pt`` / ``en``). Used when the client omits lang."""
    try:
        from site_ai_widget.db.pool import connection

        with connection() as conn:
            row = conn.execute(
                "SELECT locale FROM widget_settings WHERE company_id = %s",
                (str(company_id),),
            ).fetchone()
    except Exception:
        log.info("ask/sql stage=locale status=unavailable")
        return None
    if not row or not row[0]:
        return None
    raw = str(row[0]).strip().lower()
    if raw in ("auto",):
        return None
    if raw.startswith("pt"):
        return "pt"
    if raw.startswith("en"):
        return "en"
    return None
