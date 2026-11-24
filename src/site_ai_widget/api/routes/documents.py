"""Panel document upload / list / detail / delete (company-scoped)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from psycopg.errors import UniqueViolation

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.deps import panel_user, require_admin
from site_ai_widget.api.errors import ApiError
from site_ai_widget.db.pool import connection
from site_ai_widget.docs.storage import delete_storage_file, new_storage_name, storage_path
from site_ai_widget.docs.validate import (
    inspect_pdf,
    sanitize_original_name,
    stream_upload_to_path,
)
from site_ai_widget.docs.worker import enqueue_document

router = APIRouter(prefix="/documents", tags=["documents"])


def _row_to_list_item(row) -> dict:
    return {
        "id": str(row[0]),
        "original_name": row[1],
        "size_bytes": row[2],
        "page_count": row[3],
        "status": row[4],
        "error": row[7] if len(row) > 7 else None,
        "created_at": row[5].isoformat() if row[5] else None,
        "updated_at": row[6].isoformat() if row[6] else None,
    }


def _get_own_document(doc_id: UUID, company_id) -> tuple:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id, original_name, storage_name, size_bytes, sha256, page_count,
                   status, error, created_at, updated_at
            FROM documents
            WHERE id = %s AND company_id = %s
            """,
            (str(doc_id), str(company_id)),
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return row


def _document_count(company_id) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE company_id = %s",
            (str(company_id),),
        ).fetchone()
    return int(row[0]) if row else 0


@router.post("", status_code=202)
def upload_document(
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
) -> dict:
    # Sync def on purpose: file.file.read(), pypdf and psycopg all block, so
    # FastAPI runs this in its threadpool instead of on the event loop. The
    # whole request body is capped by BodySizeLimitMiddleware before parsing.
    settings = get_settings()
    # Cheap early refusal before any byte is written; re-checked under a lock
    # at insert time so parallel uploads cannot pass the limit together.
    if _document_count(user["company_id"]) >= settings.max_documents:
        raise ApiError("document_limit", 409, n=settings.max_documents)
    storage_name = new_storage_name()
    dest = storage_path(storage_name)
    original = sanitize_original_name(file.filename)
    replaced_storage: str | None = None

    size_bytes, sha256 = stream_upload_to_path(
        file.file,
        dest,
        max_bytes=settings.max_upload_bytes,
    )
    try:
        page_count = inspect_pdf(dest, max_pages=settings.max_pdf_pages)
    except ApiError:
        dest.unlink(missing_ok=True)
        raise

    try:
        with connection() as conn:
            # Serialise uploads per company for the count check.
            conn.execute(
                "SELECT 1 FROM companies WHERE id = %s FOR UPDATE",
                (str(user["company_id"]),),
            )
            existing = conn.execute(
                """
                SELECT id, status, storage_name FROM documents
                WHERE company_id = %s AND sha256 = %s
                """,
                (str(user["company_id"]), sha256),
            ).fetchone()
            if existing and existing[1] == "failed":
                # Re-ingestion: the same file failed before; replace that row
                # (chunks cascade) instead of blocking the retry as a duplicate.
                conn.execute(
                    "DELETE FROM documents WHERE id = %s AND company_id = %s",
                    (str(existing[0]), str(user["company_id"])),
                )
                replaced_storage = existing[2]
            elif existing:
                dest.unlink(missing_ok=True)
                raise ApiError("document_duplicate", 409, document_id=str(existing[0]))
            count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE company_id = %s",
                (str(user["company_id"]),),
            ).fetchone()[0]
            if count >= settings.max_documents:
                dest.unlink(missing_ok=True)
                raise ApiError("document_limit", 409, n=settings.max_documents)

            row = conn.execute(
                """
                INSERT INTO documents (
                    company_id, original_name, storage_name, size_bytes, sha256,
                    page_count, status, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id, status, created_at
                """,
                (
                    str(user["company_id"]),
                    original,
                    storage_name,
                    size_bytes,
                    sha256,
                    page_count,
                    str(user["id"]),
                ),
            ).fetchone()
    except UniqueViolation:
        dest.unlink(missing_ok=True)
        with connection() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE company_id = %s AND sha256 = %s",
                (str(user["company_id"]), sha256),
            ).fetchone()
        raise ApiError(
            "document_duplicate",
            409,
            document_id=str(existing[0]) if existing else "",
        ) from None
    except ApiError:
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    if replaced_storage and replaced_storage != storage_name:
        delete_storage_file(replaced_storage)
    enqueue_document(row[0], user["company_id"])
    return {
        "id": str(row[0]),
        "original_name": original,
        "status": row[1],
        "page_count": page_count,
        "size_bytes": size_bytes,
        "created_at": row[2].isoformat() if row[2] else None,
    }


@router.get("")
def list_documents(user: dict = Depends(panel_user)) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, original_name, size_bytes, page_count, status, created_at, updated_at, error
            FROM documents
            WHERE company_id = %s
            ORDER BY created_at DESC
            """,
            (str(user["company_id"]),),
        ).fetchall()
    return [_row_to_list_item(r) for r in rows]


@router.get("/{document_id}")
def get_document(document_id: UUID, user: dict = Depends(panel_user)) -> dict:
    row = _get_own_document(document_id, user["company_id"])
    with connection() as conn:
        chunk_count = conn.execute(
            """
            SELECT COUNT(*) FROM document_chunks
            WHERE document_id = %s AND company_id = %s
            """,
            (str(document_id), str(user["company_id"])),
        ).fetchone()[0]
    return {
        "id": str(row[0]),
        "original_name": row[1],
        "size_bytes": row[3],
        "sha256": row[4],
        "page_count": row[5],
        "status": row[6],
        "error": row[7],
        "chunk_count": int(chunk_count),
        "created_at": row[8].isoformat() if row[8] else None,
        "updated_at": row[9].isoformat() if row[9] else None,
    }


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: UUID, user: dict = Depends(require_admin)) -> None:
    row = _get_own_document(document_id, user["company_id"])
    storage_name = row[2]
    with connection() as conn:
        deleted = conn.execute(
            """
            DELETE FROM documents
            WHERE id = %s AND company_id = %s
            RETURNING id
            """,
            (str(document_id), str(user["company_id"])),
        ).fetchone()
    if not deleted:
        raise ApiError("not_found", 404)
    delete_storage_file(storage_name)
    from site_ai_widget.docs.suggestions import enqueue_suggestions

    enqueue_suggestions(user["company_id"])


@router.get("/{document_id}/file")
def get_document_file(document_id: UUID, user: dict = Depends(panel_user)) -> FileResponse:
    """Authenticated PDF stream. Company isolation; never a public URL."""
    row = _get_own_document(document_id, user["company_id"])
    storage_name = row[2]
    original = row[1]
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
