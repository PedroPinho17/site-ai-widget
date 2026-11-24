"""POST /ask — SQL first, then document RAG fallback."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from site_ai_widget.api.auth import current_user
from site_ai_widget.api.deps import require_ask_rate_triple
from site_ai_widget.ask_service import AskResponse, answer_question
from site_ai_widget.docs.ask import ask_documents
from site_ai_widget.sqlqa.pipeline import answer_with_sql

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(default="")


@router.post("/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    user: dict = Depends(current_user),
    _ip: str = Depends(require_ask_rate_triple),
) -> AskResponse:
    """Try text-to-SQL; on failure fall back to document Q&A.

    ``company_id`` comes only from the JWT (``current_user``). The response never
    includes SQL, schema text, prompts or database error details. SQL mode includes
    a short ``answer`` built from the result rows.
    """
    # Pass these globals through so tests can patch them on this module.
    return answer_question(
        str(user["company_id"]),
        body.question or "",
        sql_fn=answer_with_sql,
        docs_fn=ask_documents,
    )
