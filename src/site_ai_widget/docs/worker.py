"""In-process background document processing (no Redis)."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from site_ai_widget.db.pool import connection
from site_ai_widget.docs.process import process_document

log = logging.getLogger("site_ai_widget")

_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="doc-worker")
    return _executor


def enqueue_document(document_id: UUID | str, company_id: UUID | str) -> None:
    _pool().submit(process_document, str(document_id), str(company_id))


def enqueue_callable(fn, *args) -> None:
    _pool().submit(fn, *args)


def recover_stuck_documents() -> int:
    """Reset ``processing`` → ``pending`` and re-queue (app restart recovery)."""
    with connection() as conn:
        rows = conn.execute(
            """
            UPDATE documents
            SET status = 'pending', error = NULL, updated_at = now()
            WHERE status = 'processing'
            RETURNING id, company_id
            """
        ).fetchall()
        pending = conn.execute(
            "SELECT id, company_id FROM documents WHERE status = 'pending'"
        ).fetchall()
    # Re-queue recovered + any leftover pending
    seen: set[str] = set()
    jobs = list(rows) + list(pending)
    for doc_id, company_id in jobs:
        key = str(doc_id)
        if key in seen:
            continue
        seen.add(key)
        enqueue_document(doc_id, company_id)
    if jobs:
        log.info("Document worker: re-queued %s document(s)", len(seen))
    return len(seen)


def shutdown_worker() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=False)
        _executor = None
