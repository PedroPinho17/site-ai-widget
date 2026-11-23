"""Read the tables, views and columns the read-only user can see (Phase 3)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import psycopg

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.guard import client_session
from site_ai_widget.dbconn.probe import check_connection
from site_ai_widget.dbconn.target import ConnectionParams

MAX_TABLES = 1000
MAX_COLUMNS = 20000
_MAX_IDENT = 63
_MAX_TYPE = 200
_MAX_ORDINAL = 1600

# r = table, p = partitioned table (the parent), v = view, m = materialized view.
_KINDS = {"r": "table", "p": "table", "v": "view", "m": "view"}

# One row per column the user may SELECT, in tables and views it may reach.
# Partitions (children) are skipped: they are pieces of the parent table.
_SCHEMA_SQL = """
SELECT n.nspname,
       c.relname,
       c.relkind::text,
       a.attname,
       format_type(a.atttypid, a.atttypmod),
       NOT a.attnotnull,
       a.attnum
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND NOT c.relispartition
  AND n.nspname <> 'information_schema'
  AND n.nspname !~ '^pg_'
  AND has_schema_privilege(n.oid, 'USAGE')
  AND has_column_privilege(c.oid, a.attnum, 'SELECT')
ORDER BY n.nspname, c.relname, a.attnum
LIMIT %s
"""


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool
    ordinal: int


@dataclass(frozen=True)
class TableInfo:
    schema: str
    name: str
    kind: str  # "table" or "view"
    columns: tuple[ColumnInfo, ...]


def read_schema(params: ConnectionParams, *, company_id: str | None = None) -> list[TableInfo]:
    """Return what the user can read. Raises ApiError with a stable code."""
    with client_session(params, company_id=company_id) as conn:
        # Check again: the user may have gained write rights since the connection was saved.
        check_connection(conn)
        rows = _read_rows(conn)

    tables = group_rows(rows)
    if len(tables) > MAX_TABLES:
        raise ApiError("db_schema_too_large", 422)
    return tables


def _read_rows(conn: psycopg.Connection) -> list[tuple]:
    """Stream the schema query one row at a time and stop at the first bad or extra row.

    A plain execute() would download the whole result before we see row one, and a
    hostile server may ignore LIMIT. stream() hands rows over as they arrive, so
    collect_rows() can stop the read early.

    One oversized row is still downloaded whole by libpq before we can look at it;
    the session deadline in guard.py (15 s) is what bounds that case.
    """
    with conn.cursor() as cur:
        stream = cur.stream(_SCHEMA_SQL, (MAX_COLUMNS + 1,))
        try:
            return collect_rows(stream)
        except BaseException:
            # Close the connection BEFORE the stream: on early exit stream() would
            # otherwise send a cancel and then read the rest of the result, and a
            # hostile server never ends it. On a closed connection both are skipped.
            conn.close()
            stream.close()
            raise


def collect_rows(rows: Iterable[tuple]) -> list[tuple]:
    """Validate and keep rows as they arrive. Stop as soon as we pass MAX_COLUMNS."""
    out: list[tuple] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in rows:
        row = tuple(raw)
        _check_row(row)
        key = (row[0], row[1], row[3])  # schema, table, column
        if key in seen:
            raise ApiError("db_schema_invalid", 422)
        seen.add(key)
        out.append(row)
        if len(out) > MAX_COLUMNS:
            raise ApiError("db_schema_too_large", 422)
    return out


def _check_row(row: tuple) -> None:
    """Refuse values a real PostgreSQL never sends for this query."""
    if len(row) != 7:
        raise ApiError("db_schema_invalid", 422)
    schema, table, relkind, column, data_type, is_nullable, ordinal = row
    _check_ident(schema)
    _check_ident(table)
    _check_ident(column)
    if not isinstance(relkind, str) or relkind not in _KINDS:
        raise ApiError("db_schema_invalid", 422)
    if not isinstance(data_type, str) or not data_type or len(data_type) > _MAX_TYPE:
        raise ApiError("db_schema_invalid", 422)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in data_type):
        raise ApiError("db_schema_invalid", 422)
    if not isinstance(is_nullable, bool):
        raise ApiError("db_schema_invalid", 422)
    # True/False are ints in Python; ordinal must be int but not bool.
    if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise ApiError("db_schema_invalid", 422)
    if ordinal < 1 or ordinal > _MAX_ORDINAL:
        raise ApiError("db_schema_invalid", 422)


def _check_ident(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > _MAX_IDENT:
        raise ApiError("db_schema_invalid", 422)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ApiError("db_schema_invalid", 422)


def group_rows(rows: list[tuple]) -> list[TableInfo]:
    """Turn one row per column into one TableInfo per table, keeping the order."""
    grouped: dict[tuple[str, str], tuple[str, list[ColumnInfo]]] = {}
    for schema, table, relkind, column, data_type, is_nullable, ordinal in rows:
        kind = _KINDS.get(relkind)
        if kind is None:
            continue
        column_info = ColumnInfo(column, data_type, bool(is_nullable), int(ordinal))
        grouped.setdefault((schema, table), (kind, []))[1].append(column_info)
    return [
        TableInfo(schema=schema, name=table, kind=kind, columns=tuple(columns))
        for (schema, table), (kind, columns) in grouped.items()
    ]
