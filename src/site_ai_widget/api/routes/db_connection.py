"""Panel routes: save, get, test and delete the company's client DB connection."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from site_ai_widget.api.deps import panel_user, require_admin, resolve_client_ip
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.ratelimit import RateLimiter
from site_ai_widget.api.schemas import (
    DbConnectionResponse,
    DbConnectionSave,
    IntrospectResponse,
    SchemaApprovalUpdate,
    SchemaColumnResponse,
    SchemaColumnUpdate,
    SchemaResponse,
    SchemaTableResponse,
    SchemaTableUpdate,
    SuggestResponse,
)
from site_ai_widget.db.pool import connection
from site_ai_widget.dbconn.approval import refresh_embeddings_after_commit, update_column, update_table
from site_ai_widget.dbconn.crypto import decrypt_dsn, encrypt_dsn
from site_ai_widget.dbconn.introspect import read_schema
from site_ai_widget.dbconn.probe import probe_connection
from site_ai_widget.dbconn.store import save_schema
from site_ai_widget.dbconn.suggest import suggest_tables
from site_ai_widget.dbconn.target import ConnectionParams, build_conninfo, params_from_conninfo

router = APIRouter(prefix="/db-connection", tags=["db-connection"])

# Every save/test opens a connection to an outside server, so it has its own small
# budget per company and per client IP. This stops the panel being used to scan hosts.
_probe_limiter = RateLimiter(10, 600)

# Introspect is heavier than a probe but a normal admin reloads a few times while
# approving tables — keep it off the probe bucket so save/test retries do not block it.
_introspect_limiter = RateLimiter(30, 600)

# Each call spends LLM tokens, so it has its own budget per company and per client IP.
_suggest_limiter = RateLimiter(30, 600)

# Probe errors that are about us (no free session), not about the client database.
# They are returned as-is and never stored as the connection's status.
_TRANSIENT_PROBE_CODES = frozenset({"db_busy"})


def reset_db_connection_limiters() -> None:
    """Clear probe/introspect/suggest counters (tests)."""
    _probe_limiter.reset()
    _introspect_limiter.reset()
    _suggest_limiter.reset()


def _check_probe(company_id: str, request: Request) -> None:
    # Both buckets are checked before either is spent (see RateLimiter.check_many).
    ip = resolve_client_ip(request)
    _probe_limiter.check_many((f"probe:{company_id}", f"probe:ip:{ip}"))


def _check_introspect(company_id: str, request: Request) -> None:
    ip = resolve_client_ip(request)
    _introspect_limiter.check_many((f"introspect:{company_id}", f"introspect:ip:{ip}"))


def _check_suggest(company_id: str, request: Request) -> None:
    ip = resolve_client_ip(request)
    _suggest_limiter.check_many((f"suggest:{company_id}", f"suggest:ip:{ip}"))


def _to_response(row, *, sslmode: str, readable_tables: int | None = None) -> DbConnectionResponse:
    """``row`` is the first 8 columns of :data:`_COLUMNS`."""
    return DbConnectionResponse(
        sslmode=sslmode,
        host=row[0],
        port=row[1],
        database=row[2],
        username=row[3],
        status=row[4],
        last_error=row[5],
        last_checked_at=row[6],
        last_introspected_at=row[7],
        readable_tables=readable_tables,
    )


def _load_row(company_id: str):
    with connection() as conn:
        return conn.execute(
            """
            SELECT host, port, database_name, username, status, last_error,
                   last_checked_at, last_introspected_at, dsn_encrypted, sslmode
            FROM db_connections
            WHERE company_id = %s
            """,
            (company_id,),
        ).fetchone()


def _load_tables(conn, company_id: str, table_id: str | None = None) -> list[SchemaTableResponse]:
    tables = conn.execute(
        """
        SELECT id, schema_name, table_name, kind, status, ai_suggested, description, is_present
        FROM schema_tables
        WHERE company_id = %s AND (%s::uuid IS NULL OR id = %s::uuid)
        ORDER BY schema_name, table_name
        """,
        (company_id, table_id, table_id),
    ).fetchall()
    columns = conn.execute(
        """
        SELECT table_id, id, column_name, data_type, is_nullable, ordinal,
               status, is_sensitive, description, is_present
        FROM schema_columns
        WHERE company_id = %s AND (%s::uuid IS NULL OR table_id = %s::uuid)
        ORDER BY table_id, ordinal
        """,
        (company_id, table_id, table_id),
    ).fetchall()

    # The FK (table_id, company_id) guarantees every column belongs to one of these tables.
    by_table: dict = {row[0]: [] for row in tables}
    for tid, col_id, name, data_type, nullable, ordinal, status, sensitive, description, present in columns:
        by_table[tid].append(
            SchemaColumnResponse(
                id=col_id,
                name=name,
                data_type=data_type,
                is_nullable=nullable,
                ordinal=ordinal,
                status=status,
                is_sensitive=sensitive,
                description=description,
                is_present=present,
            )
        )

    return [
        SchemaTableResponse(
            id=tid,
            schema_name=schema_name,
            name=name,
            kind=kind,
            status=status,
            ai_suggested=ai_suggested,
            description=description,
            is_present=present,
            columns=by_table[tid],
        )
        for tid, schema_name, name, kind, status, ai_suggested, description, present in tables
    ]


@router.get("", response_model=DbConnectionResponse)
def get_connection(user: dict = Depends(panel_user)) -> DbConnectionResponse:
    row = _load_row(str(user["company_id"]))
    if not row:
        raise ApiError("not_found", 404)
    return _to_response(row[:8], sslmode=row[9])


_SSL_STRENGTH = {"disable": 0, "allow": 1, "prefer": 2, "require": 3, "verify-ca": 4, "verify-full": 5}


def _password_for_save(company_id: str, body: DbConnectionSave, host: str) -> str:
    """The password to use: the new one, or the stored one on a same-target re-save.

    The stored password is reused only when host, port, database and user are
    unchanged and sslmode is not weaker. Anything else needs the password again,
    so an admin session can never send a saved secret to another server or over
    a weaker TLS setting.
    """
    if body.password is not None:
        return body.password
    existing = _load_row(company_id)
    if existing is None:
        raise ApiError("db_password_required", 422)
    stored = params_from_conninfo(decrypt_dsn(existing[8]))
    same_target = (stored.host, stored.port, stored.database, stored.username) == (
        host,
        body.port,
        body.database,
        body.username,
    )
    weaker = _SSL_STRENGTH[body.sslmode] < _SSL_STRENGTH.get(stored.sslmode, _SSL_STRENGTH["prefer"])
    if not same_target or weaker:
        raise ApiError("db_password_required", 422)
    return stored.password


@router.put("", response_model=DbConnectionResponse)
def save_connection(
    body: DbConnectionSave,
    request: Request,
    user: dict = Depends(require_admin),
) -> DbConnectionResponse:
    company_id = str(user["company_id"])
    host = body.host.strip().lower()
    # Checked before the probe budget is spent: no network access happens here.
    password = _password_for_save(company_id, body, host)
    _check_probe(company_id, request)
    params = ConnectionParams(
        host=host,
        port=body.port,
        database=body.database,
        username=body.username,
        password=password,
        sslmode=body.sslmode,
    )
    # Encrypt first: without CONNECTIONS_KEY we stop (503) before touching the client DB.
    token = encrypt_dsn(build_conninfo(params))
    # Raises 422 with a stable code when the test fails; nothing is saved then.
    result = probe_connection(params, company_id=company_id)

    with connection() as conn:
        old = conn.execute(
            "SELECT host, port, database_name FROM db_connections WHERE company_id = %s FOR UPDATE",
            (company_id,),
        ).fetchone()
        same_db = old is not None and tuple(old) == (params.host, params.port, params.database)
        if not same_db:
            # A different database: the old introspected schema no longer applies.
            conn.execute("DELETE FROM schema_tables WHERE company_id = %s", (company_id,))
        row = conn.execute(
            """
            INSERT INTO db_connections (
                company_id, dsn_encrypted, host, port, database_name, username, sslmode,
                status, last_error, last_checked_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'ok', NULL, now(), now())
            ON CONFLICT (company_id) DO UPDATE SET
                dsn_encrypted = EXCLUDED.dsn_encrypted,
                host = EXCLUDED.host,
                port = EXCLUDED.port,
                database_name = EXCLUDED.database_name,
                username = EXCLUDED.username,
                sslmode = EXCLUDED.sslmode,
                status = 'ok',
                last_error = NULL,
                last_checked_at = now(),
                last_introspected_at = CASE WHEN %s THEN db_connections.last_introspected_at END,
                updated_at = now()
            RETURNING host, port, database_name, username, status, last_error,
                      last_checked_at, last_introspected_at, sslmode
            """,
            (
                company_id,
                token,
                params.host,
                params.port,
                params.database,
                params.username,
                params.sslmode,
                same_db,
            ),
        ).fetchone()
    return _to_response(row[:8], sslmode=row[8], readable_tables=result.readable_tables)


@router.post("/test", response_model=DbConnectionResponse)
def test_connection(
    request: Request,
    user: dict = Depends(require_admin),
) -> DbConnectionResponse:
    company_id = str(user["company_id"])
    _check_probe(company_id, request)
    row = _load_row(company_id)
    if not row:
        raise ApiError("not_found", 404)
    params = params_from_conninfo(decrypt_dsn(row[8]))

    readable_tables: int | None = None
    try:
        result = probe_connection(params, company_id=company_id)
        status, error = "ok", None
        readable_tables = result.readable_tables
    except ApiError as exc:
        if exc.code in _TRANSIENT_PROBE_CODES:
            # Says nothing about the connection itself (e.g. a double click, or an
            # introspect running for this company): answer 503 and keep the saved status.
            raise
        status, error = "failed", exc.code  # only the stable code is stored

    with connection() as conn:
        updated = conn.execute(
            """
            UPDATE db_connections
            SET status = %s, last_error = %s, last_checked_at = now(), updated_at = now()
            WHERE company_id = %s
            RETURNING host, port, database_name, username, status, last_error,
                      last_checked_at, last_introspected_at, sslmode
            """,
            (status, error, company_id),
        ).fetchone()
    if not updated:
        raise ApiError("not_found", 404)  # deleted in the meantime
    return _to_response(updated[:8], sslmode=updated[8], readable_tables=readable_tables)


@router.post("/introspect", response_model=IntrospectResponse)
def introspect_connection(
    request: Request,
    user: dict = Depends(require_admin),
) -> IntrospectResponse:
    """Read the client schema, save it, then refresh the affected embeddings.

    A 409 db_conflict_retry can come back after the schema was already saved (a lock
    conflict while refreshing embeddings). Retrying is safe: introspect is idempotent,
    keeps every admin decision, and retries approved tables still without an embedding.
    """
    company_id = str(user["company_id"])
    _check_introspect(company_id, request)
    row = _load_row(company_id)
    if not row:
        raise ApiError("not_found", 404)
    dsn_token = row[8]
    params = params_from_conninfo(decrypt_dsn(dsn_token))
    # Read the client DB first, outside our transaction (it can take a few seconds).
    tables = read_schema(params, company_id=company_id)

    with connection() as conn:
        locked = conn.execute(
            "SELECT dsn_encrypted FROM db_connections WHERE company_id = %s FOR UPDATE",
            (company_id,),
        ).fetchone()
        # Deleted, or credentials/host changed, while we were reading.
        if locked is None or locked[0] != dsn_token:
            raise ApiError("not_found", 404)
        result = save_schema(conn, company_id, tables)
        conn.execute(
            """
            UPDATE db_connections
            SET status = 'ok', last_error = NULL, last_checked_at = now(),
                last_introspected_at = now(), updated_at = now()
            WHERE company_id = %s
            """,
            (company_id,),
        )

    # Embeddings after the commit: the model never runs while rows are locked. Each
    # table gets its own short transaction; a failing model leaves NULL (retried on
    # the next introspect) and the schema save above still stands.
    if result.refresh_ids:
        refresh_embeddings_after_commit(company_id, result.refresh_ids)

    return IntrospectResponse(
        tables=result.tables,
        columns=result.columns,
        removed_tables=result.removed_tables,
        needs_review=result.needs_review,
    )


@router.post("/suggest", response_model=SuggestResponse)
def suggest_schema(
    request: Request,
    user: dict = Depends(require_admin),
) -> SuggestResponse:
    company_id = str(user["company_id"])
    _check_suggest(company_id, request)
    if not _load_row(company_id):
        raise ApiError("not_found", 404)
    # One batch per call; the panel calls again while "remaining" > 0.
    result = suggest_tables(company_id)
    return SuggestResponse(suggested=result.suggested, remaining=result.remaining)


@router.get("/schema", response_model=SchemaResponse)
def get_schema(user: dict = Depends(panel_user)) -> SchemaResponse:
    with connection() as conn:
        return SchemaResponse(tables=_load_tables(conn, str(user["company_id"])))


@router.put("/schema/approval", response_model=SchemaResponse)
def save_schema_approval(
    body: SchemaApprovalUpdate,
    user: dict = Depends(require_admin),
) -> SchemaResponse:
    """Set which present tables the widget may query.

    Tables in ``approved_table_ids`` become approved (safe pending columns too).
    Other present tables that were approved become pending. Missing or foreign
    ids are 404. Absent tables cannot be approved (409).
    """
    company_id = str(user["company_id"])
    wanted = {str(tid) for tid in body.approved_table_ids}
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, status, is_present FROM schema_tables
            WHERE company_id = %s
            ORDER BY id
            FOR UPDATE
            """,
            (company_id,),
        ).fetchall()
        known = {str(row[0]) for row in rows}
        unknown = wanted - known
        if unknown:
            raise ApiError("not_found", 404)
        for table_id, status, is_present in rows:
            tid = str(table_id)
            if tid in wanted:
                if not is_present:
                    raise ApiError("schema_not_present", 409)
                if status != "approved":
                    update_table(conn, company_id, tid, {"status": "approved"})
            elif status == "approved" and is_present:
                update_table(conn, company_id, tid, {"status": "pending"})
        return SchemaResponse(tables=_load_tables(conn, company_id))


@router.patch("/schema/tables/{table_id}", response_model=SchemaTableResponse)
def patch_schema_table(
    table_id: UUID,
    body: SchemaTableUpdate,
    user: dict = Depends(require_admin),
) -> SchemaTableResponse:
    company_id = str(user["company_id"])
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    with connection() as conn:
        update_table(conn, company_id, str(table_id), changes)
        return _load_tables(conn, company_id, str(table_id))[0]


@router.patch("/schema/columns/{column_id}", response_model=SchemaTableResponse)
def patch_schema_column(
    column_id: UUID,
    body: SchemaColumnUpdate,
    user: dict = Depends(require_admin),
) -> SchemaTableResponse:
    company_id = str(user["company_id"])
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    with connection() as conn:
        table_id = update_column(conn, company_id, str(column_id), changes)
        # The whole table comes back: the panel sees the new status and the other columns.
        return _load_tables(conn, company_id, table_id)[0]


@router.delete("", status_code=204)
def delete_connection(user: dict = Depends(require_admin)) -> Response:
    company_id = str(user["company_id"])
    with connection() as conn:
        # Same lock order as introspect/save: db_connections first, then children.
        conn.execute(
            "SELECT 1 FROM db_connections WHERE company_id = %s FOR UPDATE",
            (company_id,),
        )
        conn.execute("DELETE FROM schema_tables WHERE company_id = %s", (company_id,))
        conn.execute("DELETE FROM db_connections WHERE company_id = %s", (company_id,))
    return Response(status_code=204)
