"""POST /ask/docs — panel document Q&A with hybrid RAG."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from site_ai_widget.api.auth import current_user
from site_ai_widget.api.config import get_settings
from site_ai_widget.api.deps import require_ask_rate
from site_ai_widget.api.errors import ApiError
from site_ai_widget.docs.ask import ask_documents

router = APIRouter(tags=["ask"])


class AskDocsRequest(BaseModel):
    question: str = Field(min_length=1)
    document_ids: list[UUID] | None = None


class Citation(BaseModel):
    document_id: str
    document_name: str
    page: int


class AskDocsResponse(BaseModel):
    answer: str
    citations: list[Citation]


@router.post("/ask/docs", response_model=AskDocsResponse)
def ask_docs(
    body: AskDocsRequest,
    user: dict = Depends(current_user),
    _ip: str = Depends(require_ask_rate),
) -> AskDocsResponse:
    settings = get_settings()
    if len(body.question) > settings.max_question_chars:
        raise ApiError("question_too_long", 422, n=settings.max_question_chars)
    result = ask_documents(
        company_id=user["company_id"],
        question=body.question,
        document_ids=list(body.document_ids) if body.document_ids else None,
    )
    return AskDocsResponse(
        answer=result["answer"],
        citations=[Citation(**c) for c in result["citations"]],
    )
