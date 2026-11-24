"""Authenticated test chat. Same answer pipeline as the widget, not stored."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from site_ai_widget.api.auth import current_user
from site_ai_widget.api.deps import require_ask_rate_triple
from site_ai_widget.ask_service import AskResponse, answer_question
from site_ai_widget.docs.ask import ask_documents
from site_ai_widget.sqlqa.pipeline import answer_with_sql

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantTestRequest(BaseModel):
    question: str = Field(default="")
    lang: Literal["pt", "en"] | None = None


@router.post("/test", response_model=AskResponse)
def test_assistant(
    body: AssistantTestRequest,
    user: dict = Depends(current_user),
    _ip: str = Depends(require_ask_rate_triple),
) -> AskResponse:
    """Answer as the widget would, for the JWT company only.

    Nothing is written to visitor sessions. The response never includes SQL,
    prompts or database error text.
    """
    return answer_question(
        str(user["company_id"]),
        body.question or "",
        lang=body.lang,
        sql_fn=answer_with_sql,
        docs_fn=ask_documents,
    )
