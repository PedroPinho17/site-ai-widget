"""Extract PDF text, chunk, embed and persist document_chunks."""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from site_ai_widget.db.pool import connection
from site_ai_widget.docs.chunk import chunk_pages
from site_ai_widget.docs.embed import get_embedder
from site_ai_widget.docs.storage import storage_path

log = logging.getLogger("site_ai_widget")

NO_TEXT_ERROR = "no extractable text (scanned PDFs / OCR not supported)"
BATCH = 32


# Text kept from one PDF. A 300-page manual is far below this; a crafted file
# whose streams expand into millions of characters would otherwise become
# thousands of chunks and hold the embedding CPU for a long time.
MAX_EXTRACTED_CHARS = 3_000_000
TOO_MUCH_TEXT_ERROR = "too much text in this PDF (split it into smaller files)"


class TooMuchText(Exception):
    """The PDF holds more text than MAX_EXTRACTED_CHARS."""


def extract_pages(path: Path, *, max_chars: int = MAX_EXTRACTED_CHARS) -> list[str]:
    reader = PdfReader(str(path))
    pages: list[str] = []
    total = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        total += len(text)
        if total > max_chars:
            raise TooMuchText
        pages.append(text)
    return pages


def _set_status(
    document_id: UUID | str,
    company_id: UUID | str,
    status: str,
    *,
    error: str | None = None,
    page_count: int | None = None,
) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE documents
            SET status = %s,
                error = %s,
                page_count = COALESCE(%s, page_count),
                updated_at = now()
            WHERE id = %s AND company_id = %s
            """,
            (status, error, page_count, str(document_id), str(company_id)),
        )


def process_document(document_id: UUID | str, company_id: UUID | str) -> None:
    """Run in a worker thread: pending → processing → ready or failed."""
    doc_id = str(document_id)
    co_id = str(company_id)

    # Atomic claim: only one worker moves pending → processing
    with connection() as conn:
        claimed = conn.execute(
            """
            UPDATE documents
            SET status = 'processing', error = NULL, updated_at = now()
            WHERE id = %s AND company_id = %s AND status = 'pending'
            RETURNING storage_name
            """,
            (doc_id, co_id),
        ).fetchone()
    if not claimed:
        return
    storage_name = claimed[0]
    path = storage_path(storage_name)
    try:
        if not path.is_file():
            _set_status(doc_id, co_id, "failed", error="file missing on disk")
            return
        try:
            pages = extract_pages(path)
        except PdfReadError:
            _set_status(doc_id, co_id, "failed", error="could not read PDF")
            return
        except TooMuchText:
            log.warning("document text limit reached id=%s", doc_id)
            _set_status(doc_id, co_id, "failed", error=TOO_MUCH_TEXT_ERROR)
            return
        page_count = len(pages)
        chunks = chunk_pages(pages)
        if not chunks:
            _set_status(doc_id, co_id, "failed", error=NO_TEXT_ERROR, page_count=page_count)
            return

        embedder = get_embedder()
        vectors: list[list[float]] = []
        texts = [c.content for c in chunks]
        for i in range(0, len(texts), BATCH):
            vectors.extend(embedder.embed_passages(texts[i : i + BATCH]))

        with connection() as conn:
            conn.execute(
                "DELETE FROM document_chunks WHERE document_id = %s AND company_id = %s",
                (doc_id, co_id),
            )
            for chunk, vec in zip(chunks, vectors, strict=True):
                emb = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"
                conn.execute(
                    """
                    INSERT INTO document_chunks
                        (company_id, document_id, page, chunk_index, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s::vector)
                    """,
                    (co_id, doc_id, chunk.page, chunk.chunk_index, chunk.content, emb),
                )
            conn.execute(
                """
                UPDATE documents
                SET status = 'ready', error = NULL, page_count = %s, updated_at = now()
                WHERE id = %s AND company_id = %s
                """,
                (page_count, doc_id, co_id),
            )
        try:
            from site_ai_widget.docs.suggestions import regenerate_suggested_questions

            regenerate_suggested_questions(co_id)
        except Exception:
            log.warning("suggested questions skipped id=%s", doc_id)
    except Exception:
        log.exception("document processing failed id=%s", doc_id)
        _set_status(doc_id, co_id, "failed", error="processing failed")
