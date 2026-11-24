"""Public widget routes. The key is in the path so CORS preflight can see it."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from site_ai_widget import ask_service
from site_ai_widget.api.deps import charge_widget_ask, charge_widget_history
from site_ai_widget.ask_service import AskResponse
from site_ai_widget.widget.access import append_turn, authorize_widget, load_history, resolve_session

log = logging.getLogger("site_ai_widget")

router = APIRouter(prefix="/v1/widget", tags=["widget"])


class WidgetAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = ""
    session_token: str | None = Field(default=None, max_length=200)
    lang: Literal["pt", "en"] | None = None
    previous_lang: Literal["pt", "en"] | None = None


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
    )
    token, session_id = resolve_session(record.company_id, body.session_token)
    append_turn(session_id, record.company_id, (body.question or "").strip(), result)
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
