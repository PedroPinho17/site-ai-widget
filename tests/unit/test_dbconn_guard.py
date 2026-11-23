"""Our own deadline and session slots for client-DB connections (no Docker).

A fake PostgreSQL server on loopback plays the hostile client database.
"""
from __future__ import annotations

import threading
import time

import pytest

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn import guard, introspect
from site_ai_widget.dbconn.probe import probe_connection
from site_ai_widget.dbconn.target import ConnectionParams
from tests.unit.fake_pg import FakePostgres


def _fake_server(settings_env, mode: str):
    server = FakePostgres(mode)
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", f"127.0.0.1:{server.port}")
    clear_settings_cache()
    return server, ConnectionParams("127.0.0.1", server.port, "atlas", "reader", "not-used")


@pytest.fixture
def silent_server(settings_env):
    server, params = _fake_server(settings_env, "silent")
    yield params
    server.close()


@pytest.fixture
def endless_server(settings_env):
    server, params = _fake_server(settings_env, "endless_rows")
    yield server, params
    server.close()


@pytest.fixture
def one_slot(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(guard, "_slots", slots)
    return slots


def test_silent_server_is_cut_by_our_deadline(silent_server, monkeypatch, one_slot):
    monkeypatch.setattr(guard, "SESSION_DEADLINE_SECONDS", 1.0)

    started = time.monotonic()
    with pytest.raises(ApiError) as exc:
        probe_connection(silent_server)
    assert (exc.value.code, exc.value.status) == ("db_timeout", 422)
    assert time.monotonic() - started < 5
    # The slot was given back, so the next session can start.
    assert one_slot.acquire(blocking=False)


def test_too_many_sessions_answers_busy(silent_server, monkeypatch):
    busy = threading.BoundedSemaphore(1)
    busy.acquire()  # the only slot is taken
    monkeypatch.setattr(guard, "_slots", busy)
    monkeypatch.setattr(guard, "WAIT_FOR_SLOT_SECONDS", 0.1)

    with pytest.raises(ApiError) as exc:
        probe_connection(silent_server)
    assert (exc.value.code, exc.value.status) == ("db_busy", 503)


def test_one_session_per_company(silent_server):
    """A second session for the same company is refused; another company still gets one."""
    with guard.client_session(silent_server, company_id="company-a"):
        with pytest.raises(ApiError) as exc, guard.client_session(silent_server, company_id="company-a"):
            pass
        assert (exc.value.code, exc.value.status) == ("db_busy", 503)

        with guard.client_session(silent_server, company_id="company-b"):
            pass  # a different company is not blocked

    # Closing the session frees the company again.
    assert "company-a" not in guard._active_companies
    with guard.client_session(silent_server, company_id="company-a"):
        pass
    assert not guard._active_companies


def test_company_slot_is_released_when_the_global_slot_is_busy(silent_server, monkeypatch):
    busy = threading.BoundedSemaphore(1)
    busy.acquire()
    monkeypatch.setattr(guard, "_slots", busy)
    monkeypatch.setattr(guard, "WAIT_FOR_SLOT_SECONDS", 0.1)

    with pytest.raises(ApiError), guard.client_session(silent_server, company_id="company-c"):
        pass
    assert "company-c" not in guard._active_companies


def test_endless_rows_stop_early_with_schema_too_large(endless_server, monkeypatch, one_slot):
    """The server ignores LIMIT and never ends the result: we must stop on our own.

    stream() hands rows over one by one, so collect_rows() refuses the
    (MAX_COLUMNS + 1)-th row long before the 15 s session deadline.
    """
    server, params = endless_server
    monkeypatch.setattr(introspect, "MAX_COLUMNS", 500)
    monkeypatch.setattr(introspect, "check_connection", lambda conn: None)

    started = time.monotonic()
    with pytest.raises(ApiError) as exc:
        introspect.read_schema(params, company_id="company-endless")
    elapsed = time.monotonic() - started

    assert (exc.value.code, exc.value.status) == ("db_schema_too_large", 422)
    assert elapsed < 5, elapsed  # the deadline is 15 s
    assert server.stats["logins"] == 1
    assert server.stats["cancel_requests"] == 0  # closed, not cancelled-and-drained
    # Both the global slot and the company slot were given back.
    assert one_slot.acquire(blocking=False)
    assert "company-endless" not in guard._active_companies
