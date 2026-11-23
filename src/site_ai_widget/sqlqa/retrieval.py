"""Pick the approved schema tables closest to a question (Phase 4, step 12b)."""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass

import psycopg

from site_ai_widget.db.pool import connection
from site_ai_widget.docs.embed import get_embedder
from site_ai_widget.sqlqa.validator import AllowedSchema

log = logging.getLogger("site_ai_widget")

MAX_TOP_K = 4

# Caps for text written into the LLM prompt (descriptions are free text; names and
# types come from the client database and are short in a real PostgreSQL).
MAX_DESCRIPTION_CHARS = 200
MAX_NAME_CHARS = 63
MAX_TYPE_CHARS = 100


@dataclass(frozen=True)
class ColumnInfo:
    column_name: str
    data_type: str
    description: str | None


@dataclass(frozen=True)
class TableInfo:
    schema_name: str
    table_name: str
    kind: str
    description: str | None
    columns: tuple[ColumnInfo, ...]


@dataclass(frozen=True)
class SchemaSlice:
    """Approved tables/columns selected for one question — never sensitive or foreign."""

    tables: tuple[TableInfo, ...]
    allowed: AllowedSchema

    def to_prompt_text(self) -> str:
        """Compact schema block for the LLM prompt.

        Every value comes from the client database or from an admin, so it is
        untrusted: each one is flattened to a single line (no newlines, Unicode line
        separators or control characters) and capped, so it can neither add lines
        (fake tables/columns) nor break the format. One line per table, one per column.
        """
        lines: list[str] = []
        for table in self.tables:
            schema = _prompt_safe(table.schema_name, MAX_NAME_CHARS)
            name = _prompt_safe(table.table_name, MAX_NAME_CHARS)
            kind = _prompt_safe(table.kind, MAX_NAME_CHARS)
            description = _prompt_safe(table.description, MAX_DESCRIPTION_CHARS)
            lines.append(f"{schema}.{name} ({kind}): {description}")
            for col in table.columns:
                col_name = _prompt_safe(col.column_name, MAX_NAME_CHARS)
                col_type = _prompt_safe(col.data_type, MAX_TYPE_CHARS)
                col_description = _prompt_safe(col.description, MAX_DESCRIPTION_CHARS)
                lines.append(f"  - {col_name} {col_type}: {col_description}")
        return "\n".join(lines)


def _prompt_safe(text: str | None, limit: int) -> str:
    """One line of untrusted text: NFKC, no control/separator characters, capped.

    Every character in Unicode categories C* (control, format, …) and Z* (line and
    paragraph separators, odd spaces) becomes a plain space — that covers \\n, \\r,
    U+0085, U+2028 and U+2029 — then runs of spaces collapse and the result is cut
    to ``limit`` characters.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    flat = "".join(
        " " if ch != " " and unicodedata.category(ch)[0] in ("C", "Z") else ch
        for ch in normalized
    )
    return " ".join(flat.split())[:limit]


def _clamp_top_k(top_k: object) -> int:
    """1..MAX_TOP_K for an int; anything else (bool, str, float, None) → MAX_TOP_K."""
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        return MAX_TOP_K
    return max(1, min(top_k, MAX_TOP_K))


def retrieve_schema(
    company_id: str,
    question: str,
    *,
    top_k: int = MAX_TOP_K,
    conn: psycopg.Connection | None = None,
) -> SchemaSlice:
    """Embed the question and return up to ``top_k`` (clamped to 1..4) approved tables.

    Only ``status='approved'``, present, embedded tables of this company are considered.
    Columns must be approved, present and not sensitive. Tables with no such columns
    are dropped. An empty slice is returned when nothing matches.
    """
    k = _clamp_top_k(top_k)
    # Embed first: the model never runs while a pooled connection is held.
    emb = _vector_literal(get_embedder().embed_query(question))
    if conn is not None:
        return _retrieve(conn, company_id, emb, k)
    with connection() as owned:
        return _retrieve(owned, company_id, emb, k)


def _retrieve(conn: psycopg.Connection, company_id: str, emb: str, top_k: int) -> SchemaSlice:
    # Explicit transaction: SET LOCAL needs one, also on a caller's autocommit
    # connection (inside an open transaction this is a savepoint).
    with conn.transaction():
        # If the planner picks the HNSW index, keep scanning past rows of other
        # companies instead of returning too few tables (same as docs/retrieve.py).
        conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        table_rows = conn.execute(
            """
            SELECT id, schema_name, table_name, kind, description
            FROM schema_tables
            WHERE company_id = %s
              AND status = 'approved'
              AND is_present
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (company_id, emb, top_k),
        ).fetchall()

        if not table_rows:
            log.info("sqlqa/retrieval company=%s tables=%s", company_id, [])
            return SchemaSlice(tables=(), allowed={})

        table_ids = [row[0] for row in table_rows]
        column_rows = conn.execute(
            """
            SELECT table_id, column_name, data_type, description
            FROM schema_columns
            WHERE company_id = %s
              AND table_id = ANY(%s::uuid[])
              AND status = 'approved'
              AND is_present
              AND NOT is_sensitive
            ORDER BY ordinal
            """,
            (company_id, table_ids),
        ).fetchall()

    cols_by_table: dict[object, list[ColumnInfo]] = {tid: [] for tid in table_ids}
    for table_id, column_name, data_type, description in column_rows:
        bucket = cols_by_table.get(table_id)
        if bucket is None:
            continue
        bucket.append(
            ColumnInfo(
                column_name=column_name,
                data_type=data_type,
                description=description,
            )
        )

    tables: list[TableInfo] = []
    allowed: AllowedSchema = {}
    for table_id, schema_name, table_name, kind, description in table_rows:
        columns = tuple(cols_by_table.get(table_id, []))
        if not columns:
            continue
        tables.append(
            TableInfo(
                schema_name=schema_name,
                table_name=table_name,
                kind=kind,
                description=description,
                columns=columns,
            )
        )
        allowed[(schema_name, table_name)] = frozenset(c.column_name for c in columns)

    log.info(
        "sqlqa/retrieval company=%s tables=%s",
        company_id,
        [(t.schema_name, t.table_name) for t in tables],
    )
    return SchemaSlice(tables=tuple(tables), allowed=allowed)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"
