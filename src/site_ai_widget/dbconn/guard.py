"""Sessions on a client database with our own time limit, a few at a time.

connect_timeout only covers the login, and statement_timeout is applied by the
client's server. A hostile server can log us in and then never answer, which
would hold one of our threads forever. So we keep our own clock: when it runs
out, we cut the socket and the query that is waiting fails at once.
"""
from __future__ import annotations

import contextlib
import logging
import re
import socket
import threading
from collections.abc import Iterator

import psycopg

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.target import ConnectionParams, build_conninfo, resolve_target

log = logging.getLogger("site_ai_widget")

SESSION_DEADLINE_SECONDS = 15.0  # whole session after the login (checks + schema read)
MAX_SESSIONS = 4  # client-DB sessions open at the same time, all companies together
WAIT_FOR_SLOT_SECONDS = 2.0

_slots = threading.BoundedSemaphore(MAX_SESSIONS)

# One session per company at a time, so one tenant with a slow or hostile server
# cannot hold every slot above and leave the other companies with "db_busy".
_active_companies: set[str] = set()
_active_lock = threading.Lock()

_ROLE_MISSING = re.compile(r'role "[^"]*" does not exist')
_DB_MISSING = re.compile(r'database "[^"]*" does not exist')


class _Watchdog:
    """Cut the connection's socket if the session is still running after `seconds`."""

    def __init__(self, conn: psycopg.Connection, seconds: float) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._stopped = False
        self.fired = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self.fired = True
            with contextlib.suppress(OSError, psycopg.Error):
                # A copy of the socket: shutdown() wakes up the thread waiting for an answer.
                sock = socket.fromfd(self._conn.pgconn.socket, socket.AF_INET, socket.SOCK_STREAM)
                with sock:
                    sock.shutdown(socket.SHUT_RDWR)

    def stop(self) -> None:
        # After stop() returns, _fire() can no longer touch the socket.
        with self._lock:
            self._stopped = True
        self._timer.cancel()


@contextlib.contextmanager
def _company_slot(company_id: str | None) -> Iterator[None]:
    """At most one open session per company. None (internal tools, tests) skips the check."""
    if company_id is None:
        yield
        return
    with _active_lock:
        if company_id in _active_companies:
            raise ApiError("db_busy", 503)
        _active_companies.add(company_id)
    try:
        yield
    finally:
        with _active_lock:
            _active_companies.discard(company_id)


@contextlib.contextmanager
def client_session(
    params: ConnectionParams, *, company_id: str | None = None
) -> Iterator[psycopg.Connection]:
    """Open a read-only session on the client database, with our own deadline.

    Raises ApiError: 503 "db_busy" when this company already has a session open
    or too many sessions are open overall, 422 with a stable code for any
    connection or query problem. libpq messages are never returned or logged,
    because they can contain parts of the connection string.
    """
    hostaddr = resolve_target(params.host, params.port)
    # Company slot first: a second session of the same company never waits for a global slot.
    with _company_slot(company_id), _global_slot():
        yield from _session(params, hostaddr)


@contextlib.contextmanager
def _global_slot() -> Iterator[None]:
    if not _slots.acquire(timeout=WAIT_FOR_SLOT_SECONDS):
        raise ApiError("db_busy", 503)
    try:
        yield
    finally:
        _slots.release()


def _session(params: ConnectionParams, hostaddr: str) -> Iterator[psycopg.Connection]:
    conn: psycopg.Connection | None = None
    watchdog: _Watchdog | None = None
    try:
        conn = psycopg.connect(build_conninfo(params, hostaddr=hostaddr))
        conn.read_only = True
        watchdog = _Watchdog(conn, SESSION_DEADLINE_SECONDS)
        yield conn
    except psycopg.Error as exc:
        timed_out = watchdog is not None and watchdog.fired
        code = "db_timeout" if timed_out or exc.sqlstate == "57014" else map_connect_error(exc)
        log.warning("Client DB session failed: %s (%s)", code, type(exc).__name__)
        raise ApiError(code, 422) from None
    finally:
        if watchdog is not None:
            watchdog.stop()
        if conn is not None:
            conn.close()  # no COMMIT: a read-only session has nothing to save


def map_connect_error(exc: psycopg.Error) -> str:
    """Turn a psycopg/libpq error into a stable code. The text is only inspected, never kept."""
    if exc.sqlstate in ("28P01", "28000"):
        return "db_auth_failed"
    if exc.sqlstate == "3D000":
        return "db_not_found"
    text = str(exc).lower()
    if (
        "password authentication failed" in text
        or "no password supplied" in text
        or "pg_hba.conf" in text
        or _ROLE_MISSING.search(text)
    ):
        return "db_auth_failed"
    if _DB_MISSING.search(text):
        return "db_not_found"
    if isinstance(exc, psycopg.OperationalError):
        return "db_unreachable"
    return "db_connection_failed"
