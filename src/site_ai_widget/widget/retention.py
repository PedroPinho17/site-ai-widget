"""Delete visitor sessions older than each company's retention window."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from site_ai_widget.db.pool import connection

log = logging.getLogger("site_ai_widget")

DEFAULT_DAYS = 30
_stop = threading.Event()
_thread: threading.Thread | None = None


def cleanup_expired_conversations(*, now: datetime | None = None) -> int:
    """Remove sessions (and cascaded messages) past the company retention.

    Returns the number of deleted sessions. Never logs message text.
    """
    clock = now or datetime.now(UTC)
    with connection() as conn:
        # One set-based statement; per-company window from widget_settings
        # (default when a company has no settings row). Messages cascade.
        result = conn.execute(
            """
            DELETE FROM visitor_sessions s
            USING companies c
            LEFT JOIN widget_settings w ON w.company_id = c.id
            WHERE s.company_id = c.id
              AND s.last_seen < %s - make_interval(
                    days => COALESCE(w.conversation_retention_days, %s)
                  )
            """,
            (clock, DEFAULT_DAYS),
        )
        deleted = int(result.rowcount or 0)
    if deleted:
        log.info("conversation retention deleted_sessions=%s", deleted)
    return deleted


def _run_once() -> None:
    try:
        cleanup_expired_conversations()
    except Exception:
        # No exc_info: a DB error message may quote row values.
        log.warning("conversation retention job failed")


def _loop(interval_seconds: float) -> None:
    # Run at start, then every interval: a process restarted more often than
    # the interval must still clean up.
    _run_once()
    while not _stop.wait(interval_seconds):
        _run_once()


def start_retention_job(interval_seconds: float = 3600.0) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_loop,
        args=(interval_seconds,),
        name="conversation-retention",
        daemon=True,
    )
    _thread.start()


def stop_retention_job() -> None:
    global _thread
    _stop.set()
    _thread = None
