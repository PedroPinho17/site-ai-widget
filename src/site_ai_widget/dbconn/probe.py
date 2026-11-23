"""Connection test for a client database: reachable, PostgreSQL, read-only."""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.guard import client_session
from site_ai_widget.dbconn.target import ConnectionParams

MIN_SERVER_VERSION = 130000  # PostgreSQL 13

# Can this user change anything — directly, or after SET ROLE to any role it is a
# member of? "MEMBER" (not only inherited rights) so a NOINHERIT membership in a
# writer role cannot hide write rights. A normal member of a read-only group role
# still passes: that role only has SELECT.
#
# Safe side: on PostgreSQL 16+ a membership granted WITH SET FALSE, INHERIT FALSE gives
# no rights at all, but pg_has_role(..., 'MEMBER') still counts it, so such a user is
# refused too when the other role can write. Revoke that membership to pass the test.
#
# The roles CTE is tiny (the user plus its groups), so roles x relations stays cheap
# even on big databases. Privilege helpers run in an outer WHERE (OFFSET 0 keeps the
# planner from calling them on toast/catalog rows first, which raises WrongObjectType).
#
# Write rights: table INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER, column-level INSERT/UPDATE,
# sequence USAGE/UPDATE (nextval/setval), CREATE on a user schema or on the database,
# powerful role attributes, and the predefined roles that write files, run programs or
# write all data. Predefined roles are looked up by name in pg_roles, so a role that
# does not exist on an older server (pg_write_all_data is PostgreSQL 14+) is just absent.
_CAN_WRITE_SQL = r"""
WITH roles AS (
    SELECT r.oid, r.rolname, r.rolsuper, r.rolcreaterole, r.rolcreatedb,
           r.rolreplication, r.rolbypassrls
    FROM pg_roles r
    WHERE pg_has_role(current_user, r.oid, 'MEMBER')
),
rels AS (
    SELECT c.oid, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg\_%'
    OFFSET 0
),
schemas AS (
    SELECT n.oid
    FROM pg_namespace n
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND n.nspname NOT LIKE 'pg\_%'
    OFFSET 0
)
SELECT EXISTS (
           SELECT 1 FROM roles
           WHERE rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
              OR rolname IN ('pg_write_server_files', 'pg_execute_server_program', 'pg_write_all_data')
       )
    OR EXISTS (
           SELECT 1
           FROM roles ro CROSS JOIN rels t
           WHERE t.relkind <> 'S'
             AND (
                  has_table_privilege(ro.oid, t.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER')
                  OR (t.relkind IN ('r', 'p', 'v', 'f')
                      -- Column grants live in pg_attribute.attacl. Without any, the
                      -- column check could only repeat the table check above, so skip
                      -- walking every column of every table.
                      AND EXISTS (
                          SELECT 1 FROM pg_attribute a
                          WHERE a.attrelid = t.oid AND a.attacl IS NOT NULL
                      )
                      AND has_any_column_privilege(ro.oid, t.oid, 'INSERT, UPDATE'))
             )
       )
    OR EXISTS (
           SELECT 1
           FROM roles ro CROSS JOIN rels s
           WHERE s.relkind = 'S'
             AND has_sequence_privilege(ro.oid, s.oid, 'USAGE, UPDATE')
       )
    OR EXISTS (
           SELECT 1
           FROM roles ro CROSS JOIN schemas n
           WHERE has_schema_privilege(ro.oid, n.oid, 'CREATE')
       )
    OR EXISTS (
           SELECT 1 FROM roles ro
           WHERE has_database_privilege(ro.oid, current_database(), 'CREATE')
       )
"""

# Tables and views the user can SELECT (partitions are counted once, via their parent).
_READABLE_TABLES_SQL = r"""
SELECT count(*)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v')
  AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_%'
  AND has_table_privilege(c.oid, 'SELECT')
"""


@dataclass(frozen=True)
class ProbeResult:
    server_version: int
    readable_tables: int


def probe_connection(params: ConnectionParams, *, company_id: str | None = None) -> ProbeResult:
    """Connect and check the user can read but never write. Raises ApiError with a stable code."""
    with client_session(params, company_id=company_id) as conn:
        return check_connection(conn)


def check_connection(conn: psycopg.Connection) -> ProbeResult:
    """The checks themselves, on a session that is already open."""
    version = conn.info.server_version
    if version < MIN_SERVER_VERSION:
        raise ApiError("db_version_unsupported", 422)
    if _fetch_bool(conn, _CAN_WRITE_SQL):
        raise ApiError("db_user_can_write", 422)
    row = conn.execute(_READABLE_TABLES_SQL).fetchone()
    tables = int(row[0]) if row else 0
    if tables == 0:
        raise ApiError("db_no_readable_tables", 422)
    return ProbeResult(server_version=version, readable_tables=tables)


def _fetch_bool(conn: psycopg.Connection, sql: str) -> bool:
    row = conn.execute(sql).fetchone()
    return bool(row[0]) if row else True  # no answer: assume the worst
