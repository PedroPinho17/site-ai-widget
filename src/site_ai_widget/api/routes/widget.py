"""Public widget routes. The key is in the path so CORS preflight can see it."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from site_ai_widget import ask_service
from site_ai_widget.api.deps import charge_widget_ask, charge_widget_history
from site_ai_widget.api.errors import ApiError
from site_ai_widget.ask_service import AskResponse
from site_ai_widget.db.pool import connection
from site_ai_widget.docs.signed import sign_document_access
from site_ai_widget.widget.access import append_turn, authorize_widget, load_history, resolve_session

log = logging.getLogger("site_ai_widget")

router = APIRouter(prefix="/v1/widget", tags=["widget"])


class WidgetAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = ""
    session_token: str | None = Field(default=None, max_length=200)
    lang: Literal["pt", "en"] | None = None
    previous_lang: Literal["pt", "en"] | None = None
    # Last visitor question, as context for a short follow-up ("E o stock?").
    previous_question: str | None = Field(default=None, max_length=1000)


class WidgetAskResponse(AskResponse):
    session_token: str


class WidgetHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=1, max_length=200)


class WidgetHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str | None = None
    mode: Literal["sql", "docs", "none", "chat"] | None = None
    row_count: int | None = None
    created_at: datetime


class WidgetHistoryResponse(BaseModel):
    messages: list[WidgetHistoryMessage]


def _key_prefix(public_key: str) -> str:
    """Short prefix only. Never the full key."""
    if not public_key.startswith("wk_") or len(public_key) < 12:
        return "wk_"
    return public_key[:7]


def _origin(request: Request) -> str | None:
    # Origin only. Referer is not a fallback.
    return request.headers.get("origin")


@router.post("/{key}/ask", response_model=WidgetAskResponse)
def widget_ask(key: str, body: WidgetAskRequest, request: Request) -> WidgetAskResponse:
    record = authorize_widget(key, _origin(request))
    charge_widget_ask(request, record.id)
    result = ask_service.answer_question(
        record.company_id,
        body.question,
        lang=body.lang,
        previous_lang=body.previous_lang,
        previous_question=body.previous_question,
    )
    token, session_id = resolve_session(record.company_id, body.session_token)
    append_turn(
        session_id,
        record.company_id,
        (body.question or "").strip(),
        result,
        lang=result.lang,
    )
    log.info("widget ask mode=%s key_prefix=%s", result.mode, _key_prefix(key))
    # Table names are schema text: the panel test page may show them, an anonymous
    # visitor never sees them.
    public = result.model_dump(exclude={"tables"})
    return WidgetAskResponse(**public, session_token=token)


@router.post("/{key}/history", response_model=WidgetHistoryResponse)
def widget_history(key: str, body: WidgetHistoryRequest, request: Request) -> WidgetHistoryResponse:
    record = authorize_widget(key, _origin(request))
    charge_widget_history(request)
    messages = load_history(record.company_id, body.session_token)
    return WidgetHistoryResponse(messages=[WidgetHistoryMessage(**item) for item in messages])


@router.get("/{key}/config")
def widget_config(key: str, request: Request) -> dict:
    record = authorize_widget(key, _origin(request))
    with connection() as conn:
        row = conn.execute(
            """
            SELECT primary_color, greeting, locale, position, assistant_name,
                   show_suggestions, show_fullscreen, allow_visitor_documents,
                   suggested_questions
            FROM widget_settings
            WHERE company_id = %s
            """,
            (str(record.company_id),),
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    questions = list(row[8] or [])
    return {
        "primary_color": row[0],
        "greeting": row[1],
        "locale": row[2],
        "position": row[3],
        "assistant_name": row[4],
        "show_suggestions": bool(row[5]),
        "show_fullscreen": bool(row[6]),
        "allow_visitor_documents": bool(row[7]),
        "suggested_questions": questions if row[5] else [],
    }


class DocumentLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    page: int | None = Field(default=None, ge=1, le=10_000)


@router.post("/{key}/document-link")
def widget_document_link(key: str, body: DocumentLinkRequest, request: Request) -> dict:
    record = authorize_widget(key, _origin(request))
    with connection() as conn:
        settings = conn.execute(
            "SELECT allow_visitor_documents FROM widget_settings WHERE company_id = %s",
            (str(record.company_id),),
        ).fetchone()
        if not settings or not settings[0]:
            raise ApiError("visitor_documents_disabled", 403)
        doc = conn.execute(
            """
            SELECT id FROM documents
            WHERE id = %s AND company_id = %s AND status = 'ready'
            """,
            (str(body.document_id), str(record.company_id)),
        ).fetchone()
    if not doc:
        raise ApiError("not_found", 404)
    sig, exp = sign_document_access(
        company_id=record.company_id,
        document_id=body.document_id,
    )
    # Relative path: the widget joins it to the API base it was loaded from.
    # request.base_url is the internal host behind a proxy and follows the
    # client's Host header, so it must not build links.
    path = f"/v1/signed-documents/{body.document_id}?exp={exp}&sig={sig}"
    if body.page:
        path += f"#page={body.page}"
    return {"path": path, "expires_at": exp}
