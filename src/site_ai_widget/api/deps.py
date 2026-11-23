"""Shared FastAPI dependencies: rate limits and authenticated user."""
from __future__ import annotations

from fastapi import Depends, Request

from site_ai_widget.api.auth import current_user
from site_ai_widget.api.client_ip import client_ip
from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.ratelimit import RateLimiter

_settings = None
auth_limiter: RateLimiter | None = None
api_limiter: RateLimiter | None = None


def _ensure_limiters() -> tuple[RateLimiter, RateLimiter]:
    global _settings, auth_limiter, api_limiter
    settings = get_settings()
    if (
        auth_limiter is None
        or api_limiter is None
        or _settings is None
        or _settings.auth_rate_limit != settings.auth_rate_limit
        or _settings.api_rate_limit != settings.api_rate_limit
    ):
        auth_limiter = RateLimiter(settings.auth_rate_limit, settings.auth_rate_limit_window)
        api_limiter = RateLimiter(settings.api_rate_limit, settings.api_rate_limit_window)
        _settings = settings
    return auth_limiter, api_limiter


def reset_limiters() -> None:
    """Clear in-memory counters (tests)."""
    global auth_limiter, api_limiter, _settings
    auth_limiter = None
    api_limiter = None
    _settings = None


def resolve_client_ip(request: Request) -> str:
    settings = get_settings()
    raw = settings.trusted_proxy_ips or None
    return client_ip(request, trusted_raw=raw)


def require_auth_rate(request: Request) -> str:
    """Strict limit on login/register (per client IP)."""
    limiter, _ = _ensure_limiters()
    ip = resolve_client_ip(request)
    limiter.check(f"auth:{ip}")
    return ip


def require_api_rate(request: Request) -> str:
    """General API rate limit per client IP."""
    _, limiter = _ensure_limiters()
    ip = resolve_client_ip(request)
    limiter.check(f"api:{ip}")
    return ip


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
