"""Admin decisions on the client schema (Phase 3, step 8b).

Approve or exclude tables and columns, unmark false sensitive alarms, and keep the
embedding of each approved table up to date. Only approved, present, non-sensitive
columns of approved tables will ever reach text-to-SQL.
"""
from __future__ import annotations

import logging

import psycopg

from site_ai_widget.api.errors import ApiError
from site_ai_widget.db.pool import connection
from site_ai_widget.docs.embed import get_embedder

log = logging.getLogger("site_ai_widget")

_RETRY_SQLSTATES = frozenset({"40P01", "40001"})  # deadlock / serialization failure


def embedding_text(schema_name: str, table_name: str, description: str | None, columns: list[str]) -> str:
    """What we embed for a table: its name, its description and its approved columns."""
    parts = [f"Table {schema_name}.{table_name}"]
    if description:
        parts.append(description)
    if columns:
        parts.append("Columns: " + ", ".join(columns))
    return "\n".join(parts)


def _embedding_source(
    conn: psycopg.Connection, company_id: str, table_id: str, *, lock: bool = False
) -> tuple[str, str | None] | None:
    """(status, embedding text) of a table; the text is None unless it is approved.

    With lock=True the schema_tables row is locked (FOR UPDATE) for this transaction.
    Returns None when the table does not exist for this company.
    """
    row = conn.execute(
        "SELECT schema_name, table_name, status, description FROM schema_tables "
        "WHERE id = %s AND company_id = %s" + (" FOR UPDATE" if lock else ""),
        (table_id, company_id),
    ).fetchone()
    if row is None:
        return None
    schema_name, table_name, status, description = row
    if status != "approved":
        return status, None
    columns = [
        name
        for (name,) in conn.execute(
            """
            SELECT column_name FROM schema_columns
            WHERE table_id = %s AND company_id = %s
              AND status = 'approved' AND NOT is_sensitive AND is_present
            ORDER BY ordinal
            """,
            (table_id, company_id),
        )
    ]
    return status, embedding_text(schema_name, table_name, description, columns)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


def _set_embedding(conn: psycopg.Connection, company_id: str, table_id: str, literal: str | None) -> None:
    conn.execute(
        "UPDATE schema_tables SET embedding = %s::vector WHERE id = %s AND company_id = %s",
        (literal, table_id, company_id),
    )


def refresh_table_embedding(conn: psycopg.Connection, company_id: str, table_id: str) -> None:
    """Approved table: (re)compute its embedding. Any other status: no embedding.

    Used inside a PATCH transaction that already holds the table row lock.
    """
    source = _embedding_source(conn, company_id, table_id)
    if source is None:
        return
    _status, text = source
    if text is None:
        _set_embedding(conn, company_id, table_id, None)
        return
    vector = get_embedder().embed_passages([text])[0]
    _set_embedding(conn, company_id, table_id, _vector_literal(vector))


def refresh_table_embedding_safely(company_id: str, table_id: str) -> str:
    """Refresh one embedding without running the model while rows are locked.

    1. Read the embedding text (short transaction, no lock).
    2. Run the model outside any transaction.
    3. Short transaction: lock the row, read status + text again, and write only if
       the table is still approved and the text is the one we embedded. Otherwise a
       newer change (e.g. a PATCH) already wrote its own embedding: skip.

    A table that is not approved ends up with no embedding. If the model fails, the
    embedding is set to NULL (fail closed) — but only while the text is still the one
    we tried to embed; a newer embedding written meanwhile is left alone. The next
    introspect retries approved tables whose embedding is NULL.
    Returns "written", "skipped", "cleared", "failed" or "gone".
    """
    with connection() as conn:
        source = _embedding_source(conn, company_id, table_id)
    if source is None:
        return "gone"
    _status, text = source
    if text is None:
        return _clear_if_not_approved(company_id, table_id)

    try:
        vector = get_embedder().embed_passages([text])[0]
    except Exception as exc:
        # Only the error class: the text holds table and column names.
        log.warning("Schema table embedding failed: %s", type(exc).__name__)
        return _clear_after_failure(company_id, table_id, text)

    with connection() as conn:
        current = _embedding_source(conn, company_id, table_id, lock=True)
        if current is None:
            return "gone"
        _status, current_text = current
        if current_text is None:
            _set_embedding(conn, company_id, table_id, None)
            return "cleared"
        if current_text != text:
            return "skipped"
        _set_embedding(conn, company_id, table_id, _vector_literal(vector))
    return "written"


def _clear_after_failure(company_id: str, table_id: str, failed_text: str) -> str:
    """The model failed on `failed_text`: drop the (now stale) embedding, under the row lock.

    If the text changed meanwhile, a newer change (e.g. a PATCH) already wrote its own
    embedding for the new text: keep it. A table no longer approved gets no embedding.
    """
    with connection() as conn:
        current = _embedding_source(conn, company_id, table_id, lock=True)
        if current is None:
            return "gone"
        _status, current_text = current
        if current_text is None:
            _set_embedding(conn, company_id, table_id, None)
            return "cleared"
        if current_text != failed_text:
            return "skipped"
        _set_embedding(conn, company_id, table_id, None)
    return "failed"


def _clear_if_not_approved(company_id: str, table_id: str) -> str:
    with connection() as conn:
        current = _embedding_source(conn, company_id, table_id, lock=True)
        if current is None:
            return "gone"
        if current[1] is not None:
            return "skipped"  # approved again in the meantime: that change embedded it
        _set_embedding(conn, company_id, table_id, None)
    return "cleared"


def refresh_embeddings_after_commit(company_id: str, table_ids) -> dict[str, str]:
    """Refresh several embeddings after the introspect transaction has committed.

    Sorted order and one short transaction per table, so two refreshes can never
    lock rows in opposite order. A failing table does not stop the others. A lock
    conflict (deadlock / serialization failure) is reported as db_conflict_retry
    once every other table has been handled.
    """
    outcomes: dict[str, str] = {}
    conflict = False
    for table_id in sorted(str(t) for t in table_ids):
        try:
            outcomes[table_id] = refresh_table_embedding_safely(company_id, table_id)
        except psycopg.Error as exc:
            if exc.sqlstate not in _RETRY_SQLSTATES:
                raise
            log.warning("Schema table embedding conflict: %s", type(exc).__name__)
            outcomes[table_id] = "conflict"
            conflict = True
    if conflict:
        raise ApiError("db_conflict_retry", 409)
    return outcomes


def update_table(conn: psycopg.Connection, company_id: str, table_id: str, changes: dict) -> None:
    try:
        _update_table(conn, company_id, table_id, changes)
    except psycopg.Error as exc:
        if exc.sqlstate in _RETRY_SQLSTATES:
            raise ApiError("db_conflict_retry", 409) from None
        raise


def _update_table(conn: psycopg.Connection, company_id: str, table_id: str, changes: dict) -> None:
    row = conn.execute(
        """
        SELECT status, description, is_present FROM schema_tables
        WHERE id = %s AND company_id = %s
        FOR UPDATE
        """,
        (table_id, company_id),
    ).fetchone()
    if row is None:
        raise ApiError("not_found", 404)  # also when it belongs to another company
    old_status, old_description, is_present = row

    status = changes.get("status", old_status)
    description = (changes["description"] or None) if "description" in changes else old_description
    if status == "approved" and not is_present:
        raise ApiError("schema_not_present", 409)

    conn.execute(
        """
        UPDATE schema_tables SET status = %s, description = %s, updated_at = now()
        WHERE id = %s AND company_id = %s
        """,
        (status, description, table_id, company_id),
    )
    if status == "approved" and old_status != "approved":
        # Approving a table approves its safe columns; sensitive ones stay excluded.
        conn.execute(
            """
            UPDATE schema_columns SET status = 'approved', updated_at = now()
            WHERE table_id = %s AND company_id = %s
              AND status = 'pending' AND NOT is_sensitive AND is_present
            """,
            (table_id, company_id),
        )
    refresh_table_embedding(conn, company_id, table_id)


def update_column(conn: psycopg.Connection, company_id: str, column_id: str, changes: dict) -> str:
    """Returns the id of the column's table."""
    try:
        return _update_column(conn, company_id, column_id, changes)
    except psycopg.Error as exc:
        if exc.sqlstate in _RETRY_SQLSTATES:
            raise ApiError("db_conflict_retry", 409) from None
        raise


def _update_column(conn: psycopg.Connection, company_id: str, column_id: str, changes: dict) -> str:
    # Lock parent table first (same order as store.py), then the column row.
    found = conn.execute(
        "SELECT table_id FROM schema_columns WHERE id = %s AND company_id = %s",
        (column_id, company_id),
    ).fetchone()
    if found is None:
        raise ApiError("not_found", 404)
    table_id = found[0]

    parent = conn.execute(
        "SELECT 1 FROM schema_tables WHERE id = %s AND company_id = %s FOR UPDATE",
        (table_id, company_id),
    ).fetchone()
    if parent is None:
        raise ApiError("not_found", 404)

    row = conn.execute(
        """
        SELECT table_id, status, is_sensitive, description, is_present FROM schema_columns
        WHERE id = %s AND company_id = %s
        FOR UPDATE
        """,
        (column_id, company_id),
    ).fetchone()
    if row is None:
        raise ApiError("not_found", 404)
    table_id, old_status, old_sensitive, old_description, is_present = row

    sensitive = changes.get("is_sensitive", old_sensitive)
    status = changes.get("status", old_status)
    description = (changes["description"] or None) if "description" in changes else old_description

    if sensitive and status == "approved":
        if changes.get("status") == "approved":
            raise ApiError("column_sensitive", 409)
        status = "excluded"  # marking an approved column sensitive takes it out at once
    if status == "approved" and not is_present:
        raise ApiError("schema_not_present", 409)

    conn.execute(
        """
        UPDATE schema_columns SET status = %s, is_sensitive = %s, description = %s, updated_at = now()
        WHERE id = %s AND company_id = %s
        """,
        (status, sensitive, description, column_id, company_id),
    )
    # The approved columns are part of the table's embedding text.
    refresh_table_embedding(conn, company_id, str(table_id))
    return str(table_id)
