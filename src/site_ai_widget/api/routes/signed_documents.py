"""Public short-lived PDF bytes. HMAC only; no widget origin required (top-level nav)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from site_ai_widget.api.deps import require_api_rate
from site_ai_widget.api.errors import ApiError
from site_ai_widget.db.pool import connection
from site_ai_widget.docs.signed import verify_document_access
from site_ai_widget.docs.storage import storage_path

router = APIRouter(prefix="/v1/signed-documents", tags=["widget"])


@router.get("/{document_id}")
def signed_document(
    document_id: UUID,
    exp: int = Query(...),
    sig: str = Query(..., min_length=64, max_length=64, pattern="^[0-9a-f]{64}$"),
    _ip: str = Depends(require_api_rate),
) -> FileResponse:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT d.company_id::text, d.storage_name, d.original_name, d.status,
                   COALESCE(w.allow_visitor_documents, false)
            FROM documents d
            LEFT JOIN widget_settings w ON w.company_id = d.company_id
            WHERE d.id = %s
            """,
            (str(document_id),),
        ).fetchone()
    if not row:
        # Same answer as a bad signature: the route is no document-id oracle.
        raise ApiError("signed_url_invalid", 403)
    company_id, storage_name, original, status, visitors_allowed = row
    verify_document_access(
        company_id=company_id,
        document_id=document_id,
        exp=exp,
        signature=sig,
    )
    if not visitors_allowed:
        # The admin turned the option off after the link was issued.
        raise ApiError("visitor_documents_disabled", 403)
    if status != "ready":
        raise ApiError("not_found", 404)
    path = storage_path(storage_name)
    if not path.is_file():
        raise ApiError("not_found", 404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=original,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
