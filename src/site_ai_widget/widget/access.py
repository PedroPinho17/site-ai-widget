"""Widget key lookup, origin allowlist and visitor sessions.

The raw session token is returned to the browser once. Only its SHA-256 hex
digest is stored. Generated SQL is never written.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.schemas import normalize_origin
from site_ai_widget.ask_service import AskResponse
from site_ai_widget.db.pool import connection

# Fixed stand-in so a missing key still pays for a compare, like the dummy
# bcrypt check on an unknown login.
_DUMMY_KEY = "wk_dummy-constant-time-placeholder-value"
_MAX_KEY_LEN = 128
_SESSION_IDLE = "24 hours"
_HISTORY_LIMIT = 50


@dataclass(frozen=True)
class WidgetKey:
    id: str
    company_id: str
    allowed_origins: tuple[str, ...]


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _matches(presented: str, stored: str | None) -> bool:
    """Constant-time equality. A missing row is compared with a dummy key."""
    candidate = _DUMMY_KEY if stored is None else stored
    return hmac.compare_digest(_digest(presented), _digest(candidate))


def hash_session_token(token: str) -> str:
    """SHA-256 hex digest of a visitor session token. The raw token is not stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_active_key(presented: str) -> WidgetKey | None:
    """Active key for ``presented``, or None if it is missing or inactive.

    Both cases take the compare path so callers can return one 401.
    """
    row = None
    if presented and len(presented) <= _MAX_KEY_LEN:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT id::text, company_id::text, public_key, allowed_origins, is_active
                FROM widget_keys
                WHERE public_key = %s
                """,
                (presented,),
            ).fetchone()
    stored = row[2] if row else None
    active = bool(row[4]) if row else False
    matched = _matches(presented or "", stored)
    if row is None or not matched or not active:
        return None
    return WidgetKey(
        id=row[0],
        company_id=row[1],
        allowed_origins=tuple(row[3] or ()),
    )


def origin_is_allowed(origin: str | None, allowed: tuple[str, ...] | list[str]) -> bool:
    """True when ``origin`` is exactly one allowlist entry.

    Allowlist entries are stored canonical (``scheme://host[:port]``, host
    lowercased, no trailing slash, default ports dropped), which is also what
    browsers send. The header must already be in that form: a variant such as
    ``https://Shop.Example/`` is refused rather than canonicalised, so the value
    reflected in ``Access-Control-Allow-Origin`` is always an allowlist entry.
    Callers must pass the Origin header only — never Referer.
    """
    if not origin:
        return False
    try:
        canonical = normalize_origin(origin)
    except ValueError:
        return False
    return canonical == origin and origin in allowed


def authorize_widget(public_key: str, origin: str | None) -> WidgetKey:
    """Active key whose allowlist contains ``origin``.

    Unknown and inactive keys raise the same 401. A missing or foreign origin
    raises 403. This does not read Referer and does not run a question.
    """
    record = load_active_key(public_key)
    if record is None:
        raise ApiError("widget_key_invalid", 401)
    if not origin_is_allowed(origin, record.allowed_origins):
        raise ApiError("origin_not_allowed", 403)
    return record


def resolve_session(company_id: str, presented: str | None) -> tuple[str, str]:
    """Return ``(raw_token, session_id)`` for this company.

    A null, unknown, expired (24h idle) or other-company token starts a new
    session. The raw token is only returned to the caller; the row stores the
    hash. A session is never reused across companies.
    """
    token = (presented or "").strip()
    digest = hash_session_token(token) if token else None
    with connection() as conn:
        row = None
        if digest is not None:
            row = conn.execute(
                """
                SELECT id::text
                FROM visitor_sessions
                WHERE company_id = %s
                  AND token_hash = %s
                  AND last_seen > now() - %s::interval
                """,
                (str(company_id), digest, _SESSION_IDLE),
            ).fetchone()
        if row:
            return token, row[0]
        fresh = secrets.token_urlsafe(32)
        created = conn.execute(
            """
            INSERT INTO visitor_sessions (company_id, token_hash)
            VALUES (%s, %s)
            RETURNING id::text
            """,
            (str(company_id), hash_session_token(fresh)),
        ).fetchone()
    return fresh, created[0]


def append_turn(session_id: str, company_id: str, question: str, result: AskResponse) -> None:
    """Store the visitor question and the public answer. Never stores SQL."""
    assistant_text = result.answer if result.answer is not None else result.message
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO visitor_messages (session_id, company_id, role, text)
            VALUES (%s, %s, 'user', %s)
            """,
            (session_id, str(company_id), question),
        )
        conn.execute(
            """
            INSERT INTO visitor_messages
                (session_id, company_id, role, text, mode, row_count)
            VALUES (%s, %s, 'assistant', %s, %s, %s)
            """,
            (session_id, str(company_id), assistant_text, result.mode, result.row_count),
        )
        conn.execute(
            """
            UPDATE visitor_sessions
            SET last_seen = clock_timestamp()
            WHERE id = %s AND company_id = %s
            """,
            (session_id, str(company_id)),
        )


def load_history(company_id: str, token: str) -> list[dict]:
    """Last 50 messages for this company's session, oldest first.

    An unknown token, another company's token or an expired session (24h idle,
    same rule as :func:`resolve_session`) is an empty list.
    """
    token = (token or "").strip()
    if not token:
        return []
    digest = hash_session_token(token)
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id::text
            FROM visitor_sessions
            WHERE company_id = %s
              AND token_hash = %s
              AND last_seen > now() - %s::interval
            """,
            (str(company_id), digest, _SESSION_IDLE),
        ).fetchone()
        if not row:
            return []
        rows = conn.execute(
            """
            SELECT role, text, mode, row_count, created_at
            FROM (
                SELECT role, text, mode, row_count, created_at, seq
                FROM visitor_messages
                WHERE session_id = %s AND company_id = %s
                ORDER BY seq DESC
                LIMIT %s
            ) recent
            ORDER BY seq ASC
            """,
            (row[0], str(company_id), _HISTORY_LIMIT),
        ).fetchall()
    return [
        {
            "role": item[0],
            "text": item[1],
            "mode": item[2],
            "row_count": item[3],
            "created_at": item[4],
        }
        for item in rows
    ]
