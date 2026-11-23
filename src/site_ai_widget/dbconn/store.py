"""Save the introspected schema in our database (Phase 3).

New tables arrive as 'pending'. New columns arrive as 'pending', or as 'excluded' +
is_sensitive when their name looks personal or secret (see sensitive.py).
What already exists keeps its status, sensitivity and description: those are the
admin's decisions — except an approved column whose data type changed goes back to
'pending' so the admin reviews it (SQL written against the old type would break).
What disappeared gets is_present = false and, if it was approved, goes back to
'pending' (the V005 CHECK requires it). Removed tables also lose their embedding.

Embeddings are not computed here: the caller refreshes them after this transaction
commits, so the model never runs while rows are locked. refresh_ids also lists approved
tables whose embedding is still NULL, so a failed refresh is retried next time.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from site_ai_widget.dbconn.introspect import TableInfo
from site_ai_widget.dbconn.sensitive import is_sensitive_name


@dataclass(frozen=True)
class SyncResult:
    tables: int
    columns: int
    removed_tables: int
    needs_review: int
    refresh_ids: tuple[str, ...]


def _approved_columns(conn: psycopg.Connection, company_id: str) -> dict[str, dict[str, str]]:
    """One snapshot for the whole company: table_id -> {column_name: data_type}."""
    rows = conn.execute(
        """
        SELECT table_id, column_name, data_type
        FROM schema_columns
        WHERE company_id = %s AND status = 'approved' AND is_present
        """,
        (company_id,),
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for table_id, name, data_type in rows:
        out.setdefault(str(table_id), {})[name] = data_type
    return out


def _upsert_columns(conn: psycopg.Connection, column_rows: list[tuple]) -> None:
    """One batched INSERT ... SELECT FROM unnest(...) ON CONFLICT per table."""
    if not column_rows:
        return
    companies, tids, names, types, nullables, ordinals, sensitives, statuses = zip(
        *column_rows, strict=True
    )
    # Sensitivity is only decided for NEW columns. ON CONFLICT does not touch
    # is_sensitive, so the admin's decisions survive a re-introspect. Status is
    # kept too, unless an approved column changed type — then it goes to pending.
    conn.execute(
        """
        INSERT INTO schema_columns
            (company_id, table_id, column_name, data_type, is_nullable, ordinal, is_sensitive, status)
        SELECT *
        FROM unnest(
            %s::uuid[], %s::uuid[], %s::text[], %s::text[],
            %s::boolean[], %s::integer[], %s::boolean[], %s::text[]
        ) AS x(company_id, table_id, column_name, data_type, is_nullable, ordinal, is_sensitive, status)
        ON CONFLICT (table_id, column_name) DO UPDATE SET
            data_type = EXCLUDED.data_type,
            is_nullable = EXCLUDED.is_nullable,
            ordinal = EXCLUDED.ordinal,
            is_present = true,
            status = CASE
                WHEN schema_columns.status = 'approved'
                     AND schema_columns.data_type IS DISTINCT FROM EXCLUDED.data_type
                THEN 'pending'
                ELSE schema_columns.status
            END,
            updated_at = now()
        """,
        (
            list(companies),
            list(tids),
            list(names),
            list(types),
            list(nullables),
            list(ordinals),
            list(sensitives),
            list(statuses),
        ),
    )


def save_schema(conn: psycopg.Connection, company_id: str, tables: list[TableInfo]) -> SyncResult:
    before = _approved_columns(conn, company_id)
    table_ids = []
    column_count = 0
    needs_review = 0
    refresh_ids: set[str] = set()

    for table in tables:
        (table_id,) = conn.execute(
            """
            INSERT INTO schema_tables (company_id, schema_name, table_name, kind)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (company_id, schema_name, table_name) DO UPDATE SET
                kind = EXCLUDED.kind,
                is_present = true,
                updated_at = now()
            RETURNING id
            """,
            (company_id, table.schema, table.name, table.kind),
        ).fetchone()
        table_ids.append(table_id)
        tid = str(table_id)

        column_rows = []
        new_types: dict[str, str] = {}
        for c in table.columns:
            sensitive = is_sensitive_name(c.name)
            status = "excluded" if sensitive else "pending"
            column_rows.append(
                (company_id, table_id, c.name, c.data_type, c.is_nullable, c.ordinal, sensitive, status)
            )
            new_types[c.name] = c.data_type

        _upsert_columns(conn, column_rows)

        names = [c.name for c in table.columns]
        conn.execute(
            """
            UPDATE schema_columns
            SET is_present = false,
                status = CASE WHEN status = 'approved' THEN 'pending' ELSE status END,
                updated_at = now()
            WHERE table_id = %s AND is_present AND column_name <> ALL(%s)
            """,
            (table_id, names),
        )
        column_count += len(names)

        # An approved column that changed type, or is gone, needs review and a new embedding.
        for name, old_type in before.get(tid, {}).items():
            if new_types.get(name) != old_type:
                needs_review += 1
                refresh_ids.add(tid)

    removed = conn.execute(
        """
        UPDATE schema_tables
        SET is_present = false,
            status = CASE WHEN status = 'approved' THEN 'pending' ELSE status END,
            embedding = NULL,
            updated_at = now()
        WHERE company_id = %s AND is_present AND id <> ALL(%s)
        """,
        (company_id, table_ids),
    ).rowcount

    # Columns of removed tables: mark absent (and un-approve) in one UPDATE ... FROM.
    if removed:
        conn.execute(
            """
            UPDATE schema_columns AS c
            SET is_present = false,
                status = CASE WHEN c.status = 'approved' THEN 'pending' ELSE c.status END,
                updated_at = now()
            FROM schema_tables AS t
            WHERE c.table_id = t.id
              AND t.company_id = %s
              AND NOT t.is_present
              AND c.is_present
            """,
            (company_id,),
        )
        present = {str(x) for x in table_ids}
        for tid, cols in before.items():
            if tid not in present:
                needs_review += len(cols)

    # Approved tables still without an embedding (e.g. the model failed last time,
    # which leaves NULL on purpose) are refreshed again after this commit.
    for (missing_id,) in conn.execute(
        """
        SELECT id FROM schema_tables
        WHERE company_id = %s AND status = 'approved' AND is_present AND embedding IS NULL
        """,
        (company_id,),
    ):
        refresh_ids.add(str(missing_id))

    return SyncResult(
        tables=len(table_ids),
        columns=column_count,
        removed_tables=removed,
        needs_review=needs_review,
        refresh_ids=tuple(refresh_ids),
    )
