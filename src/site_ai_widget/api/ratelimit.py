"""In-memory sliding-window rate limiter (single uvicorn process)."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock

from site_ai_widget.api.errors import ApiError

DEFAULT_MAX_KEYS = 10_000


class RateLimiter:
    """Sliding window per key.

    Idle keys are evicted so memory stays bounded: a key whose deque empties is
    deleted, a periodic sweep drops keys idle for a full window, and when
    ``max_keys`` is still reached the least-recently-used key is evicted.
    """

    def __init__(self, max_calls: int, per_seconds: int, max_keys: int = DEFAULT_MAX_KEYS):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self.max_keys = max(1, max_keys)
        self._calls: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._last_sweep = time.monotonic()

    def __len__(self) -> int:
        with self._lock:
            return len(self._calls)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()

    def _prune(self, calls: deque[float], now: float) -> None:
        while calls and now - calls[0] > self.per_seconds:
            calls.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._calls):
            calls = self._calls[key]
            self._prune(calls, now)
            if not calls:
                del self._calls[key]
        self._last_sweep = now

    def check(self, key: str) -> None:
        """Record a call; raise 429 if the key has exhausted its budget."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_sweep > self.per_seconds or (
                key not in self._calls and len(self._calls) >= self.max_keys
            ):
                self._sweep(now)

            calls = self._calls.get(key)
            if calls is not None:
                self._prune(calls, now)
                if not calls:
                    del self._calls[key]
                    calls = None

            if calls is None:
                if len(self._calls) >= self.max_keys:
                    # Still full after the sweep: evict the least recently used key.
                    lru = min(self._calls, key=lambda k: self._calls[k][-1])
                    del self._calls[lru]
                calls = deque()
                self._calls[key] = calls

            if len(calls) >= self.max_calls:
                retry = int(self.per_seconds - (now - calls[0])) + 1
                err = ApiError("rate_limited", 429)
                err.headers = {"Retry-After": str(max(retry, 1))}
                raise err
            calls.append(now)
