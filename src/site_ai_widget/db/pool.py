"""Shared psycopg connection pool for the API."""
from __future__ import annotations

import atexit
import threading

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None
_lock = threading.Lock()


def get_pool(database_url: str | None = None, max_size: int = 10) -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _lock:
        if _pool is not None:
            return _pool
        if not database_url:
            from site_ai_widget.api.config import get_settings

            database_url = get_settings().database_url
        _pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=max_size,
            timeout=10,
            check=ConnectionPool.check_connection,
            name="site-ai-widget",
            open=True,
        )
        return _pool


def connection(database_url: str | None = None):
    """Context manager: ``with connection() as conn:``."""
    return get_pool(database_url).connection()


def close_pool() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(close_pool)
