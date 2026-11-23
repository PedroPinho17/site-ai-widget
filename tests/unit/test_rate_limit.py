"""In-memory rate limiter behaviour."""
from __future__ import annotations

import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.ratelimit import RateLimiter


def test_allows_under_limit():
    limiter = RateLimiter(max_calls=3, per_seconds=60)
    for _ in range(3):
        limiter.check("ip:1")


def test_blocks_over_limit():
    limiter = RateLimiter(max_calls=2, per_seconds=60)
    limiter.check("ip:a")
    limiter.check("ip:a")
    with pytest.raises(ApiError) as exc:
        limiter.check("ip:a")
    assert exc.value.status == 429
    assert exc.value.code == "rate_limited"
    assert exc.value.headers and "Retry-After" in exc.value.headers


def test_keys_are_independent():
    limiter = RateLimiter(max_calls=1, per_seconds=60)
    limiter.check("a")
    limiter.check("b")
    with pytest.raises(ApiError):
        limiter.check("a")


class _Clock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    from site_ai_widget.api import ratelimit

    c = _Clock()
    monkeypatch.setattr(ratelimit.time, "monotonic", c)
    return c


def test_window_expiry_allows_again(clock):
    limiter = RateLimiter(max_calls=1, per_seconds=10)
    limiter.check("a")
    with pytest.raises(ApiError):
        limiter.check("a")
    clock.now += 11
    limiter.check("a")


def test_idle_keys_are_evicted(clock):
    limiter = RateLimiter(max_calls=5, per_seconds=10)
    for i in range(100):
        limiter.check(f"ip:{i}")
    assert len(limiter) == 100
    clock.now += 11
    limiter.check("ip:new")  # triggers the periodic sweep
    assert len(limiter) == 1
    assert "ip:0" not in limiter._calls


def test_key_with_empty_deque_is_deleted(clock):
    limiter = RateLimiter(max_calls=5, per_seconds=10)
    limiter.check("a")
    clock.now += 11
    limiter._sweep(clock.now)
    assert "a" not in limiter._calls


def test_max_keys_cap_evicts_least_recent(clock):
    limiter = RateLimiter(max_calls=5, per_seconds=60, max_keys=3)
    for key in ("a", "b", "c"):
        limiter.check(key)
        clock.now += 1
    limiter.check("d")
    assert len(limiter) == 3
    assert set(limiter._calls) == {"b", "c", "d"}
    # an existing key below the cap is never evicted
    limiter.check("b")
    assert set(limiter._calls) == {"b", "c", "d"}


def test_check_many_records_in_every_bucket():
    limiter = RateLimiter(max_calls=2, per_seconds=60)
    limiter.check_many(("company:a", "ip:1"))
    limiter.check_many(("company:a", "ip:1"))
    with pytest.raises(ApiError):
        limiter.check("company:a")
    with pytest.raises(ApiError):
        limiter.check("ip:1")


def test_check_many_refused_by_one_bucket_spends_none():
    """The IP bucket is full: the company bucket must not lose budget for that request."""
    limiter = RateLimiter(max_calls=1, per_seconds=60)
    limiter.check_many(("company:a", "ip:shared"))
    with pytest.raises(ApiError) as exc:
        limiter.check_many(("company:b", "ip:shared"))  # refused by the IP bucket
    assert exc.value.status == 429
    assert "company:b" not in limiter._calls
    # Company b still has its whole budget from another IP.
    limiter.check_many(("company:b", "ip:other"))


def test_check_many_order_does_not_matter():
    limiter = RateLimiter(max_calls=1, per_seconds=60)
    limiter.check("ip:shared")
    with pytest.raises(ApiError):
        limiter.check_many(("ip:shared", "company:c"))  # full bucket listed first
    assert "company:c" not in limiter._calls
