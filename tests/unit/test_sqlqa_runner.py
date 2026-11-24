"""SQL runner unit tests with fakes (no client DB)."""
from __future__ import annotations

import contextlib
import json
import logging
import math
from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg_pool import PoolTimeout

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.target import ConnectionParams
from site_ai_widget.sqlqa import runner
from site_ai_widget.sqlqa.runner import RunnerError, RunResult, run_sql
from site_ai_widget.sqlqa.validator import validate_sql

ALLOWED = {
    ("public", "products"): frozenset({"id", "name", "price", "sku"}),
    ("public", "stores"): frozenset({"id", "name", "city"}),
}

PARAMS = ConnectionParams("127.0.0.1", 5436, "atlas", "atlas_readonly", "fake-pw")
COMPANY = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SENTINEL_SKU = "SENTINEL-SKU-ZZZ"
SENTINEL_ROW = "SENTINEL-ROW-VALUE"
SENTINEL_QUESTION = "SENTINEL-QUESTION-TEXT"


def _ok_sql(text: str = "SELECT name FROM products") -> str:
    return validate_sql(text, ALLOWED)


class _FakeCursor:
    def __init__(self, conn: _FakeConn, name: str | None = None) -> None:
        self._conn = conn
        self.name = name
        self.description: list[object] | None = None
        self._rows: list[tuple] = []
        self._pos = 0

    def execute(self, sql: str, params=None) -> None:
        self._conn.executed.append(sql)
        self._conn.cursor_names.append(self.name)
        if params is not None:
            self._conn.execute_params.append(params)
        if self._conn.raise_on_query and _is_user_query(sql):
            raise self._conn.raise_on_query
        if _is_user_query(sql):
            self.description = list(self._conn.columns)
            self._rows = list(self._conn.query_rows)
            self._pos = 0

    def fetchmany(self, size: int = 0) -> list[tuple]:
        self._conn.fetchmany_sizes.append(size)
        if self._conn.on_fetchmany is not None:
            self._conn.on_fetchmany(self._conn, size)
        end = self._pos + size
        batch = self._rows[self._pos : end]
        self._pos = end
        self._conn.rows_delivered += len(batch)
        return batch

    def close(self) -> None:
        self._conn.close_calls += 1
        if self._conn.close_error is not None:
            raise self._conn.close_error

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *args) -> None:
        self.close()


class _Col:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeConn:
    def __init__(
        self,
        *,
        columns: list[str] | None = None,
        query_rows: list[tuple] | None = None,
        raise_on_query: BaseException | None = None,
        rollback_error: BaseException | None = None,
        close_error: BaseException | None = None,
        on_fetchmany=None,
    ) -> None:
        self.close_error = close_error
        self.close_calls = 0
        self.columns = [_Col(n) for n in (columns or ["name"])]
        self.query_rows = query_rows or []
        self.raise_on_query = raise_on_query
        self.rollback_error = rollback_error
        self.on_fetchmany = on_fetchmany
        self.executed: list[str] = []
        self.execute_params: list[object] = []
        self.fetchmany_sizes: list[int] = []
        self.rows_delivered = 0
        self.cursor_names: list[str | None] = []
        self.rollback_calls = 0
        self.commit_calls = 0

    def execute(self, sql: str, params=None) -> None:
        self.executed.append(sql)
        if params is not None:
            self.execute_params.append(params)

    def cursor(self, name: str | None = None) -> _FakeCursor:
        return _FakeCursor(self, name=name)

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def commit(self) -> None:
        self.commit_calls += 1


def _is_user_query(sql: str) -> bool:
    head = sql.strip().upper()
    return head.startswith("SELECT") or head.startswith("WITH") or head.startswith("INSERT")


def _patch_session(monkeypatch, conn: _FakeConn, params: ConnectionParams = PARAMS):
    opened: list[tuple] = []

    @contextlib.contextmanager
    def fake_session(session_params, *, company_id=None):
        opened.append((session_params, company_id))
        yield conn

    monkeypatch.setattr(runner, "client_session", fake_session)
    monkeypatch.setattr(runner, "load_connection_params", lambda company_id: params)
    return opened


def _run(monkeypatch, conn: _FakeConn, sql: str | None = None, **kwargs):
    _patch_session(monkeypatch, conn)
    return run_sql(sql or _ok_sql(), company_id=COMPANY, allowed=ALLOWED, **kwargs)


# ---------- validation gate ----------


def test_rejected_sql_never_opens_connection(monkeypatch):
    opened: list[object] = []
    loads: list[object] = []

    @contextlib.contextmanager
    def boom(*args, **kwargs):
        opened.append(True)
        raise AssertionError("client_session must not be called")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "client_session", boom)
    monkeypatch.setattr(runner, "load_connection_params", lambda cid: loads.append(cid) or PARAMS)
    with pytest.raises(RunnerError) as exc:
        run_sql("INSERT INTO products (name) VALUES ('x')", company_id=COMPANY, allowed=ALLOWED)
    assert exc.value.reason == "not_validated"
    assert opened == []
    assert loads == []


def test_write_never_reaches_db_and_select_sets_read_only(monkeypatch):
    with pytest.raises(RunnerError) as exc:
        run_sql("UPDATE products SET price = 0", company_id=COMPANY, allowed=ALLOWED)
    assert exc.value.reason == "not_validated"

    conn = _FakeConn(query_rows=[("Azeite",)])
    _run(monkeypatch, conn)
    assert "BEGIN" not in conn.executed
    assert "SET TRANSACTION READ ONLY" in conn.executed
    assert any(s.startswith("SET LOCAL statement_timeout") for s in conn.executed)
    assert any(s.startswith("SET LOCAL idle_in_transaction_session_timeout") for s in conn.executed)
    assert "SET LOCAL lock_timeout TO 1000" in conn.executed
    assert conn.cursor_names == ["sqlqa_run"]


def test_raw_sql_executes_validator_rewrite_not_raw_text(monkeypatch):
    raw = "SELECT name FROM products"
    rewritten = validate_sql(raw, ALLOWED)
    assert "LIMIT 100" in rewritten
    conn = _FakeConn(query_rows=[("x",)])
    _patch_session(monkeypatch, conn)
    run_sql(raw, company_id=COMPANY, allowed=ALLOWED)
    assert rewritten in conn.executed
    assert raw not in conn.executed


def test_set_local_runs_before_user_query(monkeypatch):
    conn = _FakeConn(query_rows=[("x",)])
    _run(monkeypatch, conn)
    query_idx = next(i for i, s in enumerate(conn.executed) if _is_user_query(s))
    set_idxs = [i for i, s in enumerate(conn.executed) if s.startswith("SET ")]
    assert set_idxs
    assert max(set_idxs) < query_idx


# ---------- truncation / size / timeout / batching ----------


def test_truncation_at_max_rows(monkeypatch):
    conn = _FakeConn(columns=["id"], query_rows=[(i,) for i in range(10)])
    result = _run(monkeypatch, conn, _ok_sql("SELECT id FROM products"), max_rows=3)
    assert result.truncated is True
    assert result.row_count == 3
    assert result.rows == [[0], [1], [2]]
    assert conn.fetchmany_sizes[0] == min(runner._FETCH_BATCH, 3 + 1)


def test_exact_max_rows_is_not_truncated(monkeypatch):
    conn = _FakeConn(columns=["id"], query_rows=[(i,) for i in range(3)])
    result = _run(monkeypatch, conn, _ok_sql("SELECT id FROM products"), max_rows=3)
    assert result.truncated is False
    assert result.row_count == 3


def test_result_too_large(monkeypatch):
    big = "x" * 200
    conn = _FakeConn(columns=["name"], query_rows=[(big,), (big,), (big,)])
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, max_bytes=50)
    assert exc.value.reason == "result_too_large"
    assert "SELECT" not in exc.value.detail
    assert big not in exc.value.detail


def test_byte_cap_stops_before_all_rows_read(monkeypatch):
    monkeypatch.setattr(runner, "_FETCH_BATCH", 2)
    rows = [(f"row-{i}-" + ("y" * 40),) for i in range(20)]
    conn = _FakeConn(columns=["name"], query_rows=rows)
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, max_bytes=80)
    assert exc.value.reason == "result_too_large"
    assert conn.rows_delivered < len(rows)
    assert conn.rows_delivered >= 1


def test_query_canceled_maps_to_timeout(monkeypatch):
    conn = _FakeConn(raise_on_query=QueryCanceled("canceling statement due to statement timeout"))
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn)
    assert exc.value.reason == "timeout"
    assert "canceling" not in exc.value.detail


def test_lock_not_available_maps_to_timeout(monkeypatch):
    conn = _FakeConn(raise_on_query=LockNotAvailable("could not obtain lock"))
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn)
    assert exc.value.reason == "timeout"
    assert "lock" not in exc.value.detail.lower()


def test_deadline_with_fake_clock(monkeypatch):
    calls = {"n": 0}

    def fake_mono() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 100.0

    monkeypatch.setattr(runner, "_monotonic", fake_mono)
    conn = _FakeConn(columns=["id"], query_rows=[(i,) for i in range(20)])
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, _ok_sql("SELECT id FROM products"), deadline_s=1.0)
    assert exc.value.reason == "timeout"


def test_deadline_hit_partway_through_fetching(monkeypatch):
    monkeypatch.setattr(runner, "_FETCH_BATCH", 2)
    clock = {"t": 0.0}

    def fake_mono() -> float:
        return clock["t"]

    def on_fetchmany(conn, size):
        # After the first batch is delivered, the wall clock jumps past the deadline.
        if len(conn.fetchmany_sizes) >= 1:
            clock["t"] = 50.0

    monkeypatch.setattr(runner, "_monotonic", fake_mono)
    conn = _FakeConn(
        columns=["id"],
        query_rows=[(i,) for i in range(20)],
        on_fetchmany=on_fetchmany,
    )
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, _ok_sql("SELECT id FROM products"), deadline_s=5.0)
    assert exc.value.reason == "timeout"
    assert len(conn.fetchmany_sizes) >= 1
    assert sum(min(s, 2) for s in conn.fetchmany_sizes) < 20


def test_db_error_detail_leaks_nothing(monkeypatch):
    err = psycopg.ProgrammingError('relation "products" does not exist')
    conn = _FakeConn(raise_on_query=err)
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn)
    assert exc.value.reason == "db_error"
    assert "products" not in exc.value.detail
    assert "does not exist" not in exc.value.detail
    assert "SELECT" not in exc.value.detail


def test_sqlstate_class_08_is_connection_not_db_error(monkeypatch):
    class Lost(psycopg.OperationalError):
        @property
        def sqlstate(self) -> str:
            return "08006"

    conn = _FakeConn(raise_on_query=Lost("could not connect to SECRET-HOST"))
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn)
    assert exc.value.reason == "connection"
    assert "SECRET-HOST" not in exc.value.detail
    assert "SECRET-HOST" not in str(exc.value)
    assert exc.value.__cause__ is None


def test_missing_connection_is_db_error(monkeypatch):
    monkeypatch.setattr(runner, "load_connection_params", lambda cid: None)

    @contextlib.contextmanager
    def boom(*a, **k):
        raise AssertionError("session must not open")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "client_session", boom)
    with pytest.raises(RunnerError) as exc:
        run_sql(_ok_sql(), company_id=COMPANY, allowed=ALLOWED)
    assert exc.value.reason == "db_error"


@pytest.mark.parametrize(
    "err",
    [
        psycopg.OperationalError("SECRET app db host refused"),
        KeyError("password"),
        PoolTimeout("SECRET pool timeout"),
        ValueError("SECRET bad dsn"),
    ],
    ids=["operational", "keyerror", "pool_timeout", "valueerror"],
)
def test_load_connection_failure_maps_to_db_error(monkeypatch, err):
    def boom(company_id):
        raise err

    @contextlib.contextmanager
    def no_session(*a, **k):
        raise AssertionError("session must not open")
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "load_connection_params", boom)
    monkeypatch.setattr(runner, "client_session", no_session)
    with pytest.raises(RunnerError) as exc:
        run_sql(_ok_sql(), company_id=COMPANY, allowed=ALLOWED)
    assert exc.value.reason == "db_error"
    assert exc.value.detail == runner._SAFE_DETAILS["db_error"]
    assert "SECRET" not in exc.value.detail
    assert "password" not in exc.value.detail
    assert "SECRET" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_load_connection_sqlstate_08_is_connection(monkeypatch):
    class Lost(psycopg.OperationalError):
        @property
        def sqlstate(self) -> str:
            return "08001"

    def boom(company_id):
        raise Lost("connection to SECRET-HOST failed")

    monkeypatch.setattr(runner, "load_connection_params", boom)
    with pytest.raises(RunnerError) as exc:
        run_sql(_ok_sql(), company_id=COMPANY, allowed=ALLOWED)
    assert exc.value.reason == "connection"
    assert "SECRET-HOST" not in str(exc.value)


def test_api_error_from_session_maps_to_db_error(monkeypatch, caplog):
    monkeypatch.setattr(runner, "load_connection_params", lambda cid: PARAMS)

    @contextlib.contextmanager
    def busy(*a, **k):
        raise ApiError("db_host_unresolvable", 422)
        yield  # pragma: no cover

    monkeypatch.setattr(runner, "client_session", busy)
    with caplog.at_level(logging.WARNING, logger="site_ai_widget"), pytest.raises(RunnerError) as exc:
        run_sql(_ok_sql(), company_id=COMPANY, allowed=ALLOWED)
    assert exc.value.reason == "db_error"
    assert exc.value.api_code == "db_host_unresolvable"
    assert exc.value.error_class == "ApiError"
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    text = " ".join(r.message for r in caplog.records)
    assert "api_code=db_host_unresolvable" in text
    assert "error_class=ApiError" in text
    assert "SECRET" not in text
    assert "clientdb" not in text
    assert "5436" not in text


# ---------- clamping ----------


@pytest.mark.parametrize("bad", ["abc", None, math.nan, math.inf, -math.inf, True, False])
def test_bad_limit_inputs_use_defaults_then_clamp(monkeypatch, bad):
    conn = _FakeConn(columns=["id"], query_rows=[(i,) for i in range(3)])
    result = _run(
        monkeypatch,
        conn,
        _ok_sql("SELECT id FROM products"),
        max_rows=bad,
        max_bytes=bad,
        statement_timeout_ms=bad,
        deadline_s=bad,
    )
    assert result.row_count == 3
    # Defaults: max_rows=500, timeout=5000, deadline unused on fast fake.
    assert "SET LOCAL statement_timeout TO 5000" in conn.executed


def test_negative_limits_clamp_to_minimum(monkeypatch):
    conn = _FakeConn(columns=["id"], query_rows=[(i,) for i in range(5)])
    result = _run(
        monkeypatch,
        conn,
        _ok_sql("SELECT id FROM products"),
        max_rows=-5,
        max_bytes=1_000_000,
        statement_timeout_ms=-9,
        deadline_s=-1.0,
    )
    assert result.row_count == 1
    assert result.truncated is True
    assert "SET LOCAL statement_timeout TO 100" in conn.executed

    # max_bytes floor: negative becomes 1 → any real row is too large.
    conn2 = _FakeConn(query_rows=[("ab",)])
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn2, max_bytes=-1)
    assert exc.value.reason == "result_too_large"


def test_deadline_zero_clamped_to_minimum(monkeypatch):
    seen: dict[str, float] = {}

    def capture_run(conn, sql, **kwargs):
        seen["deadline_span"] = kwargs["deadline_at"] - kwargs["started"]
        return RunResult(columns=["name"], rows=[["a"]], row_count=1, truncated=False, elapsed_ms=0)

    conn = _FakeConn(query_rows=[("a",)])
    opened = _patch_session(monkeypatch, conn)
    monkeypatch.setattr(runner, "_run_on_connection", capture_run)
    run_sql(_ok_sql(), company_id=COMPANY, allowed=ALLOWED, deadline_s=0.0)
    assert seen["deadline_span"] == pytest.approx(0.1)
    assert opened[0][1] == COMPANY


def test_clamping_of_numeric_limits(monkeypatch):
    conn = _FakeConn(columns=["id"], query_rows=[(i,) for i in range(600)])
    result = _run(
        monkeypatch,
        conn,
        _ok_sql("SELECT id FROM products"),
        max_rows=9999,
        statement_timeout_ms=1,
        deadline_s=0.0,
    )
    assert result.row_count == 500
    assert result.truncated is True
    assert "SET LOCAL statement_timeout TO 100" in conn.executed

    conn2 = _FakeConn(query_rows=[("a",)])
    _run(monkeypatch, conn2, statement_timeout_ms=99_999)
    assert "SET LOCAL statement_timeout TO 5000" in conn2.executed

    conn3 = _FakeConn(columns=["id"], query_rows=[(1,), (2,), (3,)])
    r = _run(monkeypatch, conn3, _ok_sql("SELECT id FROM products"), max_rows=0)
    assert r.row_count == 1
    assert r.truncated is True


# ---------- JSON-safe / rollback / chaining ----------


def test_json_safe_conversion(monkeypatch):
    uid = UUID("12345678-1234-5678-1234-567812345678")
    conn = _FakeConn(
        columns=["price", "ts", "d", "t", "id", "blob", "flag", "n"],
        query_rows=[
            (
                Decimal("8.49"),
                datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                date(2026, 3, 4),
                time(5, 6, 7),
                uid,
                b"\x00\x01",
                True,
                None,
            )
        ],
    )
    result = _run(monkeypatch, conn, _ok_sql("SELECT price FROM products"))
    row = result.rows[0]
    assert row[0] == "8.49"
    assert row[1] == "2026-01-02T03:04:05+00:00"
    assert row[2] == "2026-03-04"
    assert row[3] == "05:06:07"
    assert row[4] == str(uid)
    assert row[5] == "<binary>"
    assert row[6] is True
    assert row[7] is None
    json.dumps(result.rows, allow_nan=False)


def test_nan_inf_become_none_and_json_dumps(monkeypatch):
    conn = _FakeConn(
        columns=["a", "b", "c"],
        query_rows=[(math.nan, math.inf, -math.inf)],
    )
    result = _run(monkeypatch, conn, _ok_sql("SELECT price FROM products"))
    assert result.rows[0] == [None, None, None]
    json.dumps(result.rows, allow_nan=False)


def test_nested_list_dict_depth_limit(monkeypatch):
    deep = {"0": {"1": {"2": {"3": {"4": {"5": {"6": "too-deep"}}}}}}}
    mixed = [{"k": 1}, (2, 3), {1: [4, 5]}]
    conn = _FakeConn(columns=["deep", "mixed"], query_rows=[(deep, mixed)])
    result = _run(monkeypatch, conn, _ok_sql("SELECT price FROM products"))
    deep_out, mixed_out = result.rows[0]
    assert deep_out["0"]["1"]["2"]["3"]["4"]["5"] == "<nested>"
    assert mixed_out == [{"k": 1}, [2, 3], {"1": [4, 5]}]
    json.dumps(result.rows, allow_nan=False)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _FakeConn(raise_on_query=QueryCanceled()),
        lambda: _FakeConn(raise_on_query=psycopg.ProgrammingError("boom")),
        lambda: _FakeConn(columns=["name"], query_rows=[("x" * 200,) for _ in range(5)]),
    ],
    ids=["timeout", "db_error", "result_too_large"],
)
def test_exception_chaining_is_cleared(monkeypatch, factory):
    conn = factory()
    kwargs = {"max_bytes": 50} if conn.query_rows else {}
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, **kwargs)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_rollback_always_commit_never_on_all_paths(monkeypatch):
    # success
    conn = _FakeConn(query_rows=[("ok",)])
    _run(monkeypatch, conn)
    assert conn.rollback_calls >= 1
    assert conn.commit_calls == 0

    # timeout
    conn2 = _FakeConn(raise_on_query=QueryCanceled())
    with pytest.raises(RunnerError):
        _run(monkeypatch, conn2)
    assert conn2.rollback_calls >= 1
    assert conn2.commit_calls == 0

    # db_error
    conn3 = _FakeConn(raise_on_query=psycopg.OperationalError("down"))
    with pytest.raises(RunnerError):
        _run(monkeypatch, conn3)
    assert conn3.rollback_calls >= 1
    assert conn3.commit_calls == 0

    # result_too_large
    conn4 = _FakeConn(query_rows=[("z" * 300,) for _ in range(3)])
    with pytest.raises(RunnerError):
        _run(monkeypatch, conn4, max_bytes=40)
    assert conn4.rollback_calls >= 1
    assert conn4.commit_calls == 0


def test_rollback_failure_does_not_replace_runner_error(monkeypatch):
    conn = _FakeConn(
        raise_on_query=QueryCanceled(),
        rollback_error=psycopg.OperationalError("rollback failed"),
    )
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn)
    assert exc.value.reason == "timeout"
    assert conn.rollback_calls >= 1


def test_cursor_close_failure_keeps_result_too_large(monkeypatch):
    conn = _FakeConn(
        query_rows=[("z" * 300,) for _ in range(3)],
        close_error=psycopg.OperationalError("SECRET close failed"),
    )
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, max_bytes=40)
    assert exc.value.reason == "result_too_large"
    assert conn.close_calls == 1
    assert conn.rollback_calls >= 1
    assert exc.value.__context__ is None


def test_cursor_close_failure_keeps_timeout(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(runner, "_monotonic", lambda: clock["t"])

    def on_fetchmany(conn, size):
        clock["t"] = 50.0

    conn = _FakeConn(
        columns=["id"],
        query_rows=[(i,) for i in range(5)],
        close_error=psycopg.OperationalError("SECRET close failed"),
        on_fetchmany=on_fetchmany,
    )
    with pytest.raises(RunnerError) as exc:
        _run(monkeypatch, conn, _ok_sql("SELECT id FROM products"), deadline_s=5.0)
    assert exc.value.reason == "timeout"
    assert conn.close_calls == 1


def test_cursor_close_failure_after_success_still_returns_rows(monkeypatch):
    conn = _FakeConn(query_rows=[("ok",)], close_error=psycopg.OperationalError("close failed"))
    result = _run(monkeypatch, conn)
    assert result.rows == [["ok"]]
    assert conn.close_calls == 1


def test_logging_success_timeout_db_error_no_secrets(monkeypatch, caplog):
    sentinels = (SENTINEL_SKU, SENTINEL_ROW, SENTINEL_QUESTION, "fake-pw")

    # success
    conn = _FakeConn(query_rows=[(SENTINEL_ROW,)])
    with caplog.at_level(logging.INFO, logger="site_ai_widget"):
        _run(monkeypatch, conn, _ok_sql(f"SELECT name FROM products WHERE sku = '{SENTINEL_SKU}'"))
    text = " ".join(r.message for r in caplog.records)
    assert "row_count=" in text
    for s in sentinels:
        assert s not in text
    caplog.clear()

    # timeout
    conn2 = _FakeConn(raise_on_query=QueryCanceled(f"cancel {SENTINEL_SKU}"))
    with caplog.at_level(logging.WARNING, logger="site_ai_widget"), pytest.raises(RunnerError):
        _run(monkeypatch, conn2)
    text = " ".join(r.message for r in caplog.records)
    assert "reason=timeout" in text
    for s in sentinels:
        assert s not in text
    caplog.clear()

    # db_error
    conn3 = _FakeConn(raise_on_query=psycopg.ProgrammingError(f"bad {SENTINEL_ROW}"))
    with caplog.at_level(logging.WARNING, logger="site_ai_widget"), pytest.raises(RunnerError):
        _run(monkeypatch, conn3)
    text = " ".join(r.message for r in caplog.records)
    assert "reason=db_error" in text
    assert "error_class=ProgrammingError" in text
    assert "sqlstate_class=-" in text
    for s in sentinels:
        assert s not in text


def test_exports():
    from site_ai_widget import sqlqa

    assert sqlqa.RunResult is RunResult
    assert sqlqa.RunnerError is RunnerError
    assert sqlqa.run_sql is run_sql
