"""JWT claims and token_version revocation (no Postgres)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from site_ai_widget.api import auth
from site_ai_widget.api.config import clear_settings_cache, get_settings
from site_ai_widget.api.errors import ApiError


@pytest.fixture(autouse=True)
def _fresh_settings():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _user(**overrides):
    base = {
        "id": uuid4(),
        "company_id": uuid4(),
        "email": "admin@example.com",
        "role": "admin",
        "token_version": 0,
    }
    base.update(overrides)
    return base


def test_create_and_decode_token_has_required_claims():
    user = _user()
    token = auth.create_token(user, hours=1)
    payload = auth.decode_token(token)
    assert payload["sub"] == str(user["id"])
    assert payload["email"] == user["email"]
    assert payload["company_id"] == str(user["company_id"])
    assert payload["role"] == "admin"
    assert payload["tv"] == 0
    assert "exp" in payload and "iat" in payload


def test_token_version_matches():
    assert auth.token_version_matches(1, 1)
    assert not auth.token_version_matches(0, 1)
    assert not auth.token_version_matches(True, 1)  # bool is not a version
    assert not auth.token_version_matches("1", 1)
    assert not auth.token_version_matches(None, 0)


def test_decode_rejects_missing_tv_claim():
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "email": "a@b.c",
            "company_id": str(uuid4()),
            "role": "admin",
            "iat": now,
            "exp": now + timedelta(hours=1),
            # no tv
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        auth.decode_token(token)


def test_current_user_rejects_stale_token_version(monkeypatch):
    user = _user(token_version=2)
    stale = {**user, "token_version": 1}
    token = auth.create_token(stale, hours=1)

    monkeypatch.setattr(auth, "load_user_by_id", lambda _uid: user)

    from fastapi.security import HTTPAuthorizationCredentials

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(ApiError) as exc:
        auth.current_user(creds)
    assert exc.value.status == 401
    assert exc.value.code == "session_invalid"


def test_current_user_accepts_matching_version(monkeypatch):
    user = _user(token_version=3)
    token = auth.create_token(user, hours=1)
    monkeypatch.setattr(auth, "load_user_by_id", lambda _uid: user)

    from fastapi.security import HTTPAuthorizationCredentials

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    got = auth.current_user(creds)
    assert got["email"] == user["email"]
    assert got["token_version"] == 3


def test_settings_refuse_short_jwt_secret(monkeypatch):
    from pydantic import ValidationError

    monkeypatch.setenv("JWT_SECRET", "too-short")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:5435/db")
    clear_settings_cache()
    with pytest.raises(ValidationError):
        get_settings()
