"""Hybrid retrieval: vector + full-text, fused with RRF, company-scoped."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import psycopg

from site_ai_widget.db.pool import connection
from site_ai_widget.docs.embed import get_embedder
from site_ai_widget.docs.rrf import reciprocal_rank_fusion

VECTOR_LIMIT = 20
TEXT_LIMIT = 20
TOP_CHUNKS = 6
RRF_K = 60

log = logging.getLogger("site_ai_widget")


def hybrid_search(
    company_id: UUID | str,
    question: str,
    *,
    document_ids: list[UUID | str] | None = None,
    top_n: int = TOP_CHUNKS,
) -> list[dict[str, Any]]:
    """Return fused chunks for ready documents belonging to ``company_id``."""
    co_id = str(company_id)
    query_vec = get_embedder().embed_query(question)
    emb = "[" + ",".join(f"{x:.8f}" for x in query_vec) + "]"

    doc_filter = ""
    params_base: list[Any] = [co_id]
    if document_ids:
        doc_filter = " AND c.document_id = ANY(%s::uuid[])"
        params_base.append([str(d) for d in document_ids])

    with connection() as conn:
        # SET LOCAL lasts until this transaction ends (the pool commits when
        # the ``with`` exits): it applies to the vector query below and never
        # leaks to the next user of the pooled connection. Filtered HNSW then
        # keeps scanning instead of silently dropping matches.
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")

        vector_sql = f"""
            SELECT c.id, c.document_id, c.page, c.chunk_index, c.content,
                   d.original_name AS document_name,
                   1 - (c.embedding <=> %s::vector) AS score
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id AND d.company_id = c.company_id
            WHERE c.company_id = %s
              AND d.status = 'ready'
              {doc_filter}
            ORDER BY c.embedding <=> %s::vector
            LIMIT {VECTOR_LIMIT}
        """
        vparams = [emb, *params_base, emb]
        vector_rows = conn.execute(vector_sql, vparams).fetchall()

        text_sql = f"""
            SELECT c.id, c.document_id, c.page, c.chunk_index, c.content,
                   d.original_name AS document_name,
                   ts_rank(c.tsv, websearch_to_tsquery('simple', %s)) AS score
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id AND d.company_id = c.company_id
            WHERE c.company_id = %s
              AND d.status = 'ready'
              AND c.tsv @@ websearch_to_tsquery('simple', %s)
              {doc_filter}
            ORDER BY score DESC
            LIMIT {TEXT_LIMIT}
        """
        # placeholders: ts_rank(q), company_id, @@(q), optional document_ids
        tparams: list[Any] = [question, co_id, question]
        if document_ids:
            tparams.append([str(d) for d in document_ids])
        try:
            # Savepoint: a tsquery error must not abort the outer transaction.
            with conn.transaction():
                text_rows = conn.execute(text_sql, tparams).fetchall()
        except psycopg.Error as exc:
            # Odd punctuation can make websearch_to_tsquery fail; fall back to
            # vector-only results. Never log the question text.
            log.warning("full-text search skipped (%s)", type(exc).__name__)
            text_rows = []

    def to_dict(row) -> dict[str, Any]:
        return {
            "id": row[0],
            "document_id": row[1],
            "page": row[2],
            "chunk_index": row[3],
            "content": row[4],
            "document_name": row[5],
        }

    fused = reciprocal_rank_fusion(
        [[to_dict(r) for r in vector_rows], [to_dict(r) for r in text_rows]],
        k=RRF_K,
        top_n=top_n,
    )
    for i, chunk in enumerate(fused, start=1):
        chunk["n"] = i
    return fused
