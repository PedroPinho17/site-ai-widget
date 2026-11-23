"""Fernet encryption of client DSNs (no database needed)."""
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from site_ai_widget.api.config import Settings, clear_settings_cache
from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn.crypto import decrypt_dsn, encrypt_dsn

DSN = "postgresql://atlas_readonly:s3cret-Pa55@127.0.0.1:5436/atlas"


@pytest.fixture
def with_key(settings_env):
    settings_env.setenv("CONNECTIONS_KEY", Fernet.generate_key().decode())
    clear_settings_cache()
    return settings_env


def test_round_trip(with_key):
    token = encrypt_dsn(DSN)
    assert decrypt_dsn(token) == DSN


def test_token_hides_password_and_user(with_key):
    token = encrypt_dsn(DSN)
    assert "s3cret-Pa55" not in token
    assert "atlas_readonly" not in token


def test_same_dsn_gives_different_tokens(with_key):
    assert encrypt_dsn(DSN) != encrypt_dsn(DSN)


def test_wrong_key_gives_connection_unreadable(with_key):
    token = encrypt_dsn(DSN)
    with_key.setenv("CONNECTIONS_KEY", Fernet.generate_key().decode())
    clear_settings_cache()
    with pytest.raises(ApiError) as exc:
        decrypt_dsn(token)
    assert exc.value.code == "connection_unreadable"
    assert "s3cret" not in str(exc.value)


def test_malformed_token_gives_connection_unreadable(with_key):
    with pytest.raises(ApiError) as exc:
        decrypt_dsn("abc")
    assert exc.value.code == "connection_unreadable"


def test_missing_key_is_503_without_dsn(settings_env):
    settings_env.setenv("CONNECTIONS_KEY", "")
    clear_settings_cache()
    with pytest.raises(ApiError) as exc:
        encrypt_dsn(DSN)
    assert exc.value.code == "connections_not_configured"
    assert exc.value.status == 503
    assert "s3cret" not in str(exc.value)
    assert "atlas_readonly" not in str(exc.value.message)


def test_invalid_key_is_refused_without_echo(settings_env):
    settings_env.setenv("CONNECTIONS_KEY", "not-a-real-key")
    with pytest.raises(ValidationError) as exc:
        Settings()  # type: ignore[call-arg]
    assert "not-a-real-key" not in str(exc.value)
