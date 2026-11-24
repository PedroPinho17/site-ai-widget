"""Document Q&A orchestration: retrieve → prompt → LLM → citations."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError
from site_ai_widget.db.pool import connection
from site_ai_widget.docs.prompt import build_messages, map_citations
from site_ai_widget.docs.retrieve import hybrid_search
from site_ai_widget.llm.client import complete_chat

log = logging.getLogger("site_ai_widget")

NO_CONTEXT = {
    "pt": "Não encontrei informação relevante nos documentos carregados.",
    "en": "I could not find relevant information in the uploaded documents.",
}


def _guess_lang(question: str) -> str:
    q = question.lower()
    # Lightweight heuristic for the empty-retrieval message only
    pt_markers = ("ção", "ões", "ã", "õ", "está", "estão", "horário", "abertura", "documento")
    if any(m in q for m in pt_markers):
        return "pt"
    return "en"


def ensure_documents_in_company(
    company_id: UUID | str,
    document_ids: list[UUID | str],
) -> None:
    """404 if any id is missing or belongs to another company."""
    if not document_ids:
        return
    ids = [str(d) for d in document_ids]
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id FROM documents
            WHERE company_id = %s AND id = ANY(%s::uuid[])
            """,
            (str(company_id), ids),
        ).fetchall()
    found = {str(r[0]) for r in rows}
    if len(found) != len(set(ids)):
        raise ApiError("not_found", 404)


def ask_documents(
    *,
    company_id: UUID | str,
    question: str,
    document_ids: list[UUID | str] | None = None,
    llm_complete=None,
) -> dict[str, Any]:
    """Answer ``question`` from the company's ready documents.

    ``llm_complete`` is injectable for tests (defaults to Groq).
    """
    settings = get_settings()
    q = question.strip()
    if not q:
        raise ApiError("validation_error", 422)
    if len(q) > settings.max_question_chars:
        raise ApiError("question_too_long", 422, n=settings.max_question_chars)

    if document_ids:
        ensure_documents_in_company(company_id, document_ids)

    chunks = hybrid_search(company_id, q, document_ids=document_ids)
    if not chunks:
        lang = _guess_lang(q)
        # matched=False: search found no text (callers may map this to mode "none").
        return {"answer": NO_CONTEXT[lang], "citations": [], "matched": False}

    if not settings.groq_keys() and llm_complete is None:
        raise ApiError("llm_not_configured", 503)

    messages = build_messages(q, chunks)
    complete = llm_complete or complete_chat
    # Never log question text or excerpt content
    log.info("ask/docs company=%s chunks=%s", company_id, len(chunks))
    answer = complete(messages, temperature=0)
    citations = map_citations(answer, chunks)
    # matched=True even when the answer has no [n] markers (citations may be empty).
    return {"answer": answer, "citations": citations, "matched": True}
