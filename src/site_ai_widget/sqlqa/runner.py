"""Execute validator-approved SQL on the client DB (Phase 4, step 12d).

Results are untrusted data — never treat row values as instructions. Never log
the SQL, the question, connection params or row values; reason codes, row_count
and elapsed_ms are fine.

Connection params are loaded from the company's stored DSN (never taken from the
caller). ApiError and other client_session / slot / connect failures become
RunnerError("db_error") with a safe detail.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as time_of_day
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg_pool import PoolTimeout

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.guard import client_session
from site_ai_widget.dbconn.store import load_connection_params
from site_ai_widget.sqlqa.validator import AllowedSchema, SQLRejected, validate_sql

log = logging.getLogger("site_ai_widget")

_FETCH_BATCH = 100
_JSON_DEPTH_LIMIT = 5
_NAMED_CURSOR = "sqlqa_run"

_MAX_ROWS = 500
_MAX_BYTES = 1_000_000
_MAX_STATEMENT_TIMEOUT_MS = 5000
_MIN_STATEMENT_TIMEOUT_MS = 100
_MAX_DEADLINE_S = 15.0
_MIN_DEADLINE_S = 0.1
_LOCK_TIMEOUT_MS = 1000

_SAFE_DETAILS: dict[str, str] = {
    "not_validated": "SQL did not pass validation.",
    "timeout": "The query timed out.",
    "result_too_large": "The result exceeds the size limit.",
    "db_error": "The database could not run the query.",
}


@dataclass(frozen=True)
class RunResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    elapsed_ms: int


class RunnerError(Exception):
    """Query execution refused or failed.

    ``reason`` is a stable code for logs and API mapping. ``detail`` is a short
    safe hint — never SQL, the question, parameters or database error text.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail or _SAFE_DETAILS.get(reason, "")
        super().__init__(reason)


def run_sql(
    validated_sql: str,
    *,
    company_id: str,
    allowed: AllowedSchema,
    max_rows: object = _MAX_ROWS,
    max_bytes: object = _MAX_BYTES,
    statement_timeout_ms: object = _MAX_STATEMENT_TIMEOUT_MS,
    deadline_s: object = _MAX_DEADLINE_S,
) -> RunResult:
    """Re-validate then run read-only SQL under row/byte/time caps.

    Loads the company's stored client DB connection; the caller never passes
    connection params. ApiError from loading or from client_session (busy slot,
    connect failure, …) is mapped to ``db_error``.

    Raises:
        RunnerError: not_validated, timeout, result_too_large, or db_error.
        ``__cause__`` and ``__context__`` are always None.
    """
    rejected = False
    try:
        sql = validate_sql(validated_sql, allowed)
    except SQLRejected:
        rejected = True
    if rejected:
        raise RunnerError("not_validated")

    max_rows_i = _coerce_int(max_rows, _MAX_ROWS, 1, _MAX_ROWS)
    max_bytes_i = _coerce_int(max_bytes, _MAX_BYTES, 1, _MAX_BYTES)
    timeout_ms = _coerce_int(
        statement_timeout_ms,
        _MAX_STATEMENT_TIMEOUT_MS,
        _MIN_STATEMENT_TIMEOUT_MS,
        _MAX_STATEMENT_TIMEOUT_MS,
    )
    deadline = _coerce_float(deadline_s, _MAX_DEADLINE_S, _MIN_DEADLINE_S, _MAX_DEADLINE_S)

    started = _monotonic()
    deadline_at = started + deadline
    reason: str | None = None
    result: RunResult | None = None

    params = None
    try:
        params = load_connection_params(company_id)
    except (ApiError, psycopg.Error, PoolTimeout, KeyError, ValueError):
        # App DB down, pool exhausted, bad company_id or unreadable DSN: the
        # original text may hold DB details, so only the reason is kept.
        reason = "db_error"
    if reason is None and params is None:
        reason = "db_error"

    if reason is None:
        assert params is not None
        try:
            with client_session(params, company_id=company_id) as conn:
                try:
                    result = _run_on_connection(
                        conn,
                        sql,
                        max_rows=max_rows_i,
                        max_bytes=max_bytes_i,
                        statement_timeout_ms=timeout_ms,
                        deadline_at=deadline_at,
                        started=started,
                    )
                except (QueryCanceled, LockNotAvailable):
                    reason = "timeout"
                except RunnerError as exc:
                    reason = exc.reason
                except psycopg.Error:
                    reason = "db_error"
                finally:
                    with contextlib.suppress(psycopg.Error):
                        conn.rollback()
        except ApiError:
            reason = "db_error"

    if reason is not None:
        log.warning(
            "sqlqa/runner company=%s reason=%s row_count=0 elapsed_ms=%s",
            company_id,
            reason,
            _elapsed_ms(started),
        )
        raise RunnerError(reason)

    assert result is not None
    log.info(
        "sqlqa/runner company=%s row_count=%s truncated=%s elapsed_ms=%s",
        company_id,
        result.row_count,
        result.truncated,
        result.elapsed_ms,
    )
    return result


def _run_on_connection(
    conn: psycopg.Connection,
    sql: str,
    *,
    max_rows: int,
    max_bytes: int,
    statement_timeout_ms: int,
    deadline_at: float,
    started: float,
) -> RunResult:
    # Clamped integers only — never concatenate user input into these statements.
    # First command starts the transaction (no explicit BEGIN); then SET LOCAL.
    conn.execute("SET TRANSACTION READ ONLY")
    conn.execute(f"SET LOCAL statement_timeout TO {statement_timeout_ms}")
    conn.execute(f"SET LOCAL idle_in_transaction_session_timeout TO {statement_timeout_ms}")
    conn.execute(f"SET LOCAL lock_timeout TO {_LOCK_TIMEOUT_MS}")

    # Server-side cursor: rows arrive in batches instead of buffering the whole result.
    # Closed by hand: a failing CLOSE must never replace the original error
    # (timeout / result_too_large); the rollback afterwards drops the cursor anyway.
    cur = conn.cursor(name=_NAMED_CURSOR)
    try:
        cur.execute(sql)
        columns = [desc.name for desc in cur.description] if cur.description else []
        rows: list[list] = []
        truncated = False

        while True:
            need = max_rows + 1 - len(rows)
            if need <= 0:
                break
            batch = cur.fetchmany(min(_FETCH_BATCH, need))
            if not batch:
                break
            for raw in batch:
                if len(rows) >= max_rows:
                    truncated = True
                    break
                rows.append([_json_safe(value) for value in raw])
            if _monotonic() >= deadline_at:
                raise RunnerError("timeout")
            payload = json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(payload) > max_bytes:
                raise RunnerError("result_too_large")
            if truncated:
                break
    finally:
        with contextlib.suppress(psycopg.Error):
            cur.close()

    return RunResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=_elapsed_ms(started),
    )


def _json_safe(value: object, depth: int = 0) -> object:
    """Convert a cell to a JSON-native or string form. Results are untrusted data."""
    if depth > _JSON_DEPTH_LIMIT:
        return "<nested>"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time_of_day):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return "<binary>"
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v, depth + 1) for v in value]
    return str(value)


def _coerce_int(value: object, default: int, lo: int, hi: int) -> int:
    """Bad input → default, then clamp. Never raises."""
    if isinstance(value, bool):
        value = default
    elif isinstance(value, float):
        value = int(value) if math.isfinite(value) else default
    elif not isinstance(value, int):
        value = default
    return max(lo, min(hi, int(value)))


def _coerce_float(value: object, default: float, lo: float, hi: float) -> float:
    """Bad input → default, then clamp. Never raises."""
    if isinstance(value, bool):
        value = default
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value) if math.isfinite(float(value)) else default
    else:
        value = default
    return max(lo, min(hi, float(value)))


def _elapsed_ms(started: float) -> int:
    return max(0, int((_monotonic() - started) * 1000))


def _monotonic() -> float:
    """Wall-clock for deadlines; tests may monkeypatch this."""
    return time.monotonic()
