"""Panel auth: bcrypt passwords + JWT (HS256) with ``token_version`` revocation."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError
from site_ai_widget.db.pool import connection

ALGORITHM = "HS256"
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 72  # bcrypt limit
bearer = HTTPBearer(auto_error=False)

# Constant-time login: always run a bcrypt check even when the email is unknown
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())

USER_COLUMNS = "id, company_id, email, role, token_version, panel_locale"
BUMP_TOKEN_VERSION = "token_version = token_version + 1"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str | bytes) -> bool:
    try:
        raw = hashed.encode() if isinstance(hashed, str) else hashed
        return bcrypt.checkpw(password.encode(), raw)
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ApiError("pw_too_short", 400, n=MIN_PASSWORD_LEN)
    if len(password.encode()) > MAX_PASSWORD_LEN:
        raise ApiError("pw_too_long", 400)


def _row_to_user(row) -> dict[str, Any]:
    uid, company_id, email, role, token_version, panel_locale = row
    return {
        "id": uid,
        "company_id": company_id,
        "email": email,
        "role": role,
        "token_version": int(token_version),
        "panel_locale": panel_locale or "pt",
    }


def authenticate(email: str, password: str) -> dict[str, Any] | None:
    """Validate credentials. Timing is similar whether or not the email exists."""
    email_norm = email.strip().lower()
    with connection() as conn:
        row = conn.execute(
            f"SELECT {USER_COLUMNS}, password_hash FROM users WHERE lower(email) = %s",
            (email_norm,),
        ).fetchone()
        if not row:
            check_password(password, _DUMMY_HASH)
            return None
        *user_cols, password_hash = row
        if not check_password(password, password_hash):
            return None
        return _row_to_user(user_cols)


def load_user_by_id(user_id: UUID | str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            f"SELECT {USER_COLUMNS} FROM users WHERE id = %s",
            (str(user_id),),
        ).fetchone()
        return _row_to_user(row) if row else None


def create_token(user: dict[str, Any], hours: int | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "company_id": str(user["company_id"]),
        "role": user["role"],
        "tv": int(user["token_version"]),
        "iat": now,
        "exp": now + timedelta(hours=hours or settings.jwt_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def _is_version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def token_version_matches(claim: Any, current: Any) -> bool:
    return _is_version(claim) and _is_version(current) and claim == current


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[ALGORITHM],
        options={"require": ["exp", "iat", "sub", "tv", "company_id", "role", "email"]},
    )


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if creds is None or not creds.credentials:
        raise ApiError("session_invalid", 401)
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise ApiError("session_invalid", 401) from None
    user = load_user_by_id(payload["sub"])
    if not user:
        raise ApiError("session_invalid", 401)
    if not token_version_matches(payload.get("tv"), user.get("token_version")):
        raise ApiError("session_invalid", 401)
    if str(user["company_id"]) != str(payload.get("company_id")):
        raise ApiError("session_invalid", 401)
    return user


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "company_id": str(user["company_id"]),
        "panel_locale": user.get("panel_locale") or "pt",
    }


def new_widget_public_key() -> str:
    return "wk_" + secrets.token_urlsafe(24)
