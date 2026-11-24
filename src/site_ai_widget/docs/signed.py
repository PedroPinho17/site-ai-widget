"""Short-lived HMAC tokens for visitor PDF access (company-scoped)."""
from __future__ import annotations

import hashlib
import hmac
import time
from uuid import UUID

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError

DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900


def sign_document_access(
    *,
    company_id: UUID | str,
    document_id: UUID | str,
    exp: int | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[str, int]:
    """Return ``(hex_signature, unix_exp)``."""
    expires = int(exp if exp is not None else time.time() + ttl_seconds)
    sig = _digest(str(company_id), str(document_id), expires)
    return sig, expires


def verify_document_access(
    *,
    company_id: UUID | str,
    document_id: UUID | str,
    exp: int | str | None,
    signature: str | None,
) -> None:
    try:
        expires = int(exp) if exp is not None else 0
    except (TypeError, ValueError):
        raise ApiError("signed_url_invalid", 403) from None
    if not signature or expires <= 0:
        raise ApiError("signed_url_invalid", 403)
    now = int(time.time())
    if expires < now:
        raise ApiError("signed_url_expired", 403)
    if expires > now + MAX_TTL_SECONDS:
        # Never honour a link that claims a lifetime we would not issue.
        raise ApiError("signed_url_invalid", 403)
    expected = _digest(str(company_id), str(document_id), expires)
    if not hmac.compare_digest(expected, str(signature).lower()):
        raise ApiError("signed_url_invalid", 403)


_KEY_LABEL = b"site-ai-widget:signed-document:v1"


def _signing_key() -> bytes:
    # Derived, not the raw JWT secret: a signature from this scheme can never
    # be confused with (or help forge) a token from another one.
    secret = get_settings().jwt_secret.encode("utf-8")
    return hmac.new(secret, _KEY_LABEL, hashlib.sha256).digest()


def _digest(company_id: str, document_id: str, exp: int) -> str:
    msg = f"doc.{company_id}.{document_id}.{exp}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
