"""Authenticated test chat. Same answer pipeline as the widget, not stored."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from site_ai_widget.api.deps import require_assistant_test_rate
from site_ai_widget.ask_service import AskResponse, answer_question
from site_ai_widget.docs.ask import ask_documents
from site_ai_widget.sqlqa.pipeline import answer_with_sql

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantTestRequest(BaseModel):
    question: str = Field(default="")
    lang: Literal["pt", "en"] | None = None
    previous_lang: Literal["pt", "en"] | None = None
    # Last visitor question, as context for a short follow-up ("E o stock?").
    previous_question: str | None = Field(default=None, max_length=1000)


@router.post("/test", response_model=AskResponse)
def test_assistant(
    body: AssistantTestRequest,
    user: dict = Depends(require_assistant_test_rate),
) -> AskResponse:
    """Answer as the widget would, for the JWT company only.

    Nothing is written to visitor sessions. The response never includes SQL,
    prompts or database error text. Answer language follows the question
    (short follow-ups use ``previous_lang``).
    """
    return answer_question(
        str(user["company_id"]),
        body.question or "",
        lang=body.lang,
        previous_lang=body.previous_lang,
        previous_question=body.previous_question,
        sql_fn=answer_with_sql,
        docs_fn=ask_documents,
    )
