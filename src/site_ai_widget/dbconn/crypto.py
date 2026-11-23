"""Encrypt and decrypt client database DSNs with Fernet.

The DSN contains the read-only user's password, so it is only stored
encrypted. The key (CONNECTIONS_KEY) lives in .env, never in the database.
"""
from cryptography.fernet import Fernet, InvalidToken

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError


def _fernet() -> Fernet:
    key = get_settings().connections_key
    if not key:
        raise ApiError("connections_not_configured", 503)
    return Fernet(key.encode())


def encrypt_dsn(dsn: str) -> str:
    """Return a Fernet token (text) to store in db_connections.dsn_encrypted."""
    return _fernet().encrypt(dsn.encode()).decode()


def decrypt_dsn(token: str) -> str:
    """Return the original DSN. A wrong or changed key gives connection_unreadable."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ApiError("connection_unreadable", 503) from None
