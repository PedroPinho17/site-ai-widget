"""Shared FastAPI dependencies: rate limits and authenticated user."""
from __future__ import annotations

from threading import Lock

from fastapi import Depends, Request

from site_ai_widget.api.auth import current_user
from site_ai_widget.api.client_ip import client_ip
from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.ratelimit import RateLimiter

_settings = None
auth_limiter: RateLimiter | None = None
api_limiter: RateLimiter | None = None
ask_limiter: RateLimiter | None = None
widget_key_limiter: RateLimiter | None = None
widget_lookup_limiter: RateLimiter | None = None
assistant_test_limiter: RateLimiter | None = None
_widget_charge_lock = Lock()


def _ensure_limiters() -> tuple[RateLimiter, RateLimiter, RateLimiter]:
    global _settings, auth_limiter, api_limiter, ask_limiter, widget_key_limiter
    global widget_lookup_limiter, assistant_test_limiter
    settings = get_settings()
    if (
        auth_limiter is None
        or api_limiter is None
        or ask_limiter is None
        or widget_key_limiter is None
        or widget_lookup_limiter is None
        or assistant_test_limiter is None
        or _settings is None
        or _settings.assistant_test_rate_limit != settings.assistant_test_rate_limit
        or _settings.assistant_test_rate_limit_window != settings.assistant_test_rate_limit_window
        or _settings.widget_lookup_rate_limit != settings.widget_lookup_rate_limit
        or _settings.widget_lookup_rate_limit_window != settings.widget_lookup_rate_limit_window
        or _settings.auth_rate_limit != settings.auth_rate_limit
        or _settings.api_rate_limit != settings.api_rate_limit
        or _settings.ask_rate_limit != settings.ask_rate_limit
        or _settings.ask_rate_limit_window != settings.ask_rate_limit_window
        or _settings.widget_key_rate_limit != settings.widget_key_rate_limit
        or _settings.widget_key_rate_limit_window != settings.widget_key_rate_limit_window
    ):
        auth_limiter = RateLimiter(settings.auth_rate_limit, settings.auth_rate_limit_window)
        api_limiter = RateLimiter(settings.api_rate_limit, settings.api_rate_limit_window)
        ask_limiter = RateLimiter(settings.ask_rate_limit, settings.ask_rate_limit_window)
        widget_key_limiter = RateLimiter(
            settings.widget_key_rate_limit, settings.widget_key_rate_limit_window
        )
        widget_lookup_limiter = RateLimiter(
            settings.widget_lookup_rate_limit, settings.widget_lookup_rate_limit_window
        )
        assistant_test_limiter = RateLimiter(
            settings.assistant_test_rate_limit, settings.assistant_test_rate_limit_window
        )
        _settings = settings
    return auth_limiter, api_limiter, ask_limiter


def reset_limiters() -> None:
    """Clear in-memory counters (tests)."""
    global auth_limiter, api_limiter, ask_limiter, widget_key_limiter, _settings
    global widget_lookup_limiter, assistant_test_limiter
    auth_limiter = None
    api_limiter = None
    ask_limiter = None
    widget_key_limiter = None
    widget_lookup_limiter = None
    assistant_test_limiter = None
    _settings = None
    from site_ai_widget.api.routes.db_connection import reset_db_connection_limiters

    reset_db_connection_limiters()


def resolve_client_ip(request: Request) -> str:
    settings = get_settings()
    raw = settings.trusted_proxy_ips or None
    return client_ip(request, trusted_raw=raw)


def require_auth_rate(request: Request) -> str:
    """Strict limit on login/register (per client IP)."""
    limiter, _, _ = _ensure_limiters()
    ip = resolve_client_ip(request)
    limiter.check(f"auth:{ip}")
    return ip


def require_api_rate(request: Request) -> str:
    """General API rate limit per client IP."""
    _, limiter, _ = _ensure_limiters()
    ip = resolve_client_ip(request)
    limiter.check(f"api:{ip}")
    return ip


def require_ask_rate(request: Request) -> str:
    """Document Q&A rate limit per client IP (one slot — ``/ask/docs``)."""
    return _ask_rate(request, weight=1)


def require_ask_rate_triple(request: Request) -> str:
    """``/ask`` rate limit: three slots (SQL generate + retry + docs LLM)."""
    return _ask_rate(request, weight=3)


def _ask_rate(request: Request, *, weight: int) -> str:
    _, _, limiter = _ensure_limiters()
    ip = resolve_client_ip(request)
    limiter.check(f"ask:{ip}", n=weight)
    return ip


def charge_widget_ask(request: Request, key_id: str) -> str:
    """Charge the per-IP ask bucket (weight 3) and the per-key bucket (weight 1).

    Neither bucket is spent unless both have room. ``key_id`` is the key's uuid,
    not the public key.
    """
    _, _, ask = _ensure_limiters()
    per_key = widget_key_limiter
    if per_key is None:
        raise RuntimeError("widget key limiter was not initialised")
    ip = resolve_client_ip(request)
    ip_key = f"ask:{ip}"
    wk = f"widget-key:{key_id}"
    with _widget_charge_lock:
        if not ask.fits(ip_key, n=3):
            ask.check(ip_key, n=3)
        if not per_key.fits(wk, n=1):
            per_key.check(wk, n=1)
        ask.check(ip_key, n=3)
        per_key.check(wk, n=1)
    return ip


def charge_widget_lookup(request: Request) -> str:
    """One slot per /v1/widget/* request, taken before the key/origin lookup.

    A separate bucket from ``api:``, so a foreign page cannot spend a visitor's
    general API budget, but random keys still cannot flood the database.
    """
    _ensure_limiters()
    limiter = widget_lookup_limiter
    if limiter is None:
        raise RuntimeError("widget lookup limiter was not initialised")
    ip = resolve_client_ip(request)
    limiter.check(f"widget-lookup:{ip}")
    return ip


def require_assistant_test_rate(user: dict = Depends(current_user)) -> dict:
    """Panel Test assistant: one slot per message in a per-user bucket.

    Keyed by the signed-in user id, so an admin testing the assistant never
    spends (or is blocked by) the per-IP ask bucket that widget visitors use,
    nor the introspect or probe buckets.
    """
    _ensure_limiters()
    limiter = assistant_test_limiter
    if limiter is None:
        raise RuntimeError("assistant test limiter was not initialised")
    limiter.check(f"assistant-test:{user['id']}")
    return user


def charge_widget_history(request: Request) -> str:
    """History reads take one slot in the general per-IP API bucket.

    No LLM runs, so the heavier ask bucket is not spent; but a forged Origin
    from a non-browser client must not get unlimited database reads.
    """
    return require_api_rate(request)


def panel_user(
    user: dict = Depends(current_user),
    _ip: str = Depends(require_api_rate),
) -> dict:
    """Authenticated panel user, company-scoped, within API rate limit."""
    return user


ADMIN_ROLE = "admin"


def require_admin(user: dict = Depends(panel_user)) -> dict:
    """Panel user whose (DB-loaded) role is ``admin``; members get 403.

    The role comes from the users row loaded in ``current_user``, not from the
    JWT claim, so demoting a user takes effect immediately.
    """
    if user.get("role") != ADMIN_ROLE:
        raise ApiError("forbidden", 403)
    return user
