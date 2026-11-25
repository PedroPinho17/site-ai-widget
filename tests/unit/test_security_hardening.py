"""Security hardening: response headers, closed sign-up, per-account login limit,
TLS for public client databases, and the PDF text limit.

No LLM, no network, no database (every collaborator is a fake).
"""
from __future__ import annotations

import contextlib
import io

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.security_headers import secure_headers
from site_ai_widget.dbconn import guard
from site_ai_widget.dbconn.target import ConnectionParams, enforce_tls
from site_ai_widget.docs import process

PANEL_ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture
def app_client(monkeypatch):
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app

    clear_settings_cache()
    reset_limiters()
    with TestClient(create_app()) as client:
        yield client
    reset_limiters()
    clear_settings_cache()


def _settings(monkeypatch, **env):
    from site_ai_widget.api.deps import reset_limiters

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    clear_settings_cache()
    reset_limiters()


# ---------- security headers ----------


def test_json_errors_get_the_strict_headers(app_client):
    r = app_client.get("/no-such-route")
    assert r.status_code == 404
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_container_starts_uvicorn_without_a_server_header():
    from pathlib import Path

    entrypoint = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"
    assert "--no-server-header" in entrypoint.read_text(encoding="utf-8")


def test_embed_script_keeps_its_own_cache_and_is_not_locked_down_like_json(app_client):
    r = app_client.get("/embed.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=300"
    assert r.headers["cross-origin-resource-policy"] == "cross-origin"
    assert r.headers["x-content-type-options"] == "nosniff"
    # A script must still load on customer pages: no default-src 'none' here.
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_panel_preflight_answered_by_middleware_gets_headers_too(app_client):
    r = app_client.options(
        "/auth/login",
        headers={"Origin": PANEL_ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 204
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_existing_values_are_never_overwritten():
    headers = [
        (b"content-type", b"application/pdf"),
        (b"cache-control", b"private, no-store"),
        (b"x-content-type-options", b"nosniff"),
    ]
    out = dict(secure_headers(headers))
    assert out[b"cache-control"] == b"private, no-store"
    assert out[b"content-security-policy"] == b"frame-ancestors 'none'"
    assert out[b"referrer-policy"] == b"no-referrer"
    assert [name for name, _ in secure_headers(headers)].count(b"x-content-type-options") == 1


def test_json_with_charset_is_still_json():
    out = dict(secure_headers([(b"content-type", b"application/json; charset=utf-8")]))
    assert out[b"content-security-policy"].startswith(b"default-src 'none'")
    assert out[b"cache-control"] == b"no-store"


# ---------- sign-up is closed by default ----------

_REGISTER = {
    "company_name": "Loja Teste",
    "company_slug": "loja-teste",
    "email": "admin@loja-teste.example",
    "password": "uma-password-longa",
}


def test_registration_is_off_by_default():
    from site_ai_widget.api.config import Settings

    assert Settings.model_fields["allow_registration"].default is False


def test_closed_registration_answers_404_before_any_database_work(monkeypatch):
    _settings(monkeypatch, ALLOW_REGISTRATION="0")
    from site_ai_widget.api.main import create_app
    from site_ai_widget.api.routes import auth_routes

    def no_db():
        raise AssertionError("a closed sign-up must not touch the database")

    monkeypatch.setattr(auth_routes, "connection", no_db)
    with TestClient(create_app()) as client:
        r = client.post("/auth/register", json=_REGISTER)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"
    clear_settings_cache()


# ---------- per-account login limit ----------


def test_failed_logins_are_limited_per_account(monkeypatch):
    _settings(monkeypatch, AUTH_RATE_LIMIT="3", AUTH_RATE_LIMIT_WINDOW="300")
    from site_ai_widget.api import deps

    for _ in range(3):
        deps.require_account_auth_rate("Admin@Loja.example")
        deps.record_failed_login("Admin@Loja.example")
    with pytest.raises(ApiError) as exc:
        # Same account, any spelling or IP.
        deps.require_account_auth_rate("  admin@loja.EXAMPLE ")
    assert exc.value.status == 429
    assert exc.value.headers["Retry-After"]
    deps.require_account_auth_rate("outra@loja.example")  # another account is untouched
    keys = list(deps.auth_limiter._calls)
    assert keys and all("@" not in key for key in keys)  # only hashes are kept
    clear_settings_cache()


def test_checking_the_account_limit_alone_spends_nothing(monkeypatch):
    """A correct sign-in only checks: the owner can log in as often as needed."""
    _settings(monkeypatch, AUTH_RATE_LIMIT="2", AUTH_RATE_LIMIT_WINDOW="300")
    from site_ai_widget.api import deps

    for _ in range(10):
        deps.require_account_auth_rate("owner@loja.example")
    clear_settings_cache()


def test_login_route_counts_only_failures(app_client, monkeypatch):
    from site_ai_widget.api.routes import auth_routes

    checked: list[str] = []
    failed: list[str] = []
    monkeypatch.setattr(auth_routes, "require_account_auth_rate", lambda email: checked.append(email))
    monkeypatch.setattr(auth_routes, "record_failed_login", lambda email: failed.append(email))
    monkeypatch.setattr(auth_routes, "authenticate", lambda email, password: None)
    r = app_client.post("/auth/login", json={"email": "x@loja.example", "password": "wrong-password"})
    assert r.status_code == 401
    assert checked == ["x@loja.example"]
    assert failed == ["x@loja.example"]

    user = {
        "id": "11111111-1111-1111-1111-111111111111",
        "company_id": "22222222-2222-2222-2222-222222222222",
        "email": "x@loja.example",
        "role": "admin",
        "token_version": 0,
        "panel_locale": "pt",
    }
    monkeypatch.setattr(auth_routes, "authenticate", lambda email, password: user)
    r = app_client.post("/auth/login", json={"email": "x@loja.example", "password": "right-password"})
    assert r.status_code == 200
    assert failed == ["x@loja.example"]  # the success was not counted


def test_change_password_uses_the_strict_auth_limit():
    from site_ai_widget.api.deps import require_api_rate, require_auth_rate
    from site_ai_widget.api.routes.auth_routes import router

    route = next(r for r in router.routes if r.path == "/auth/change-password")
    calls = {dep.call for dep in route.dependant.dependencies}
    assert require_auth_rate in calls
    assert require_api_rate not in calls


# ---------- TLS for public client databases ----------


def _params(host: str, sslmode: str, port: int = 5432) -> ConnectionParams:
    return ConnectionParams(host=host, port=port, database="shop", username="reader", password="pw", sslmode=sslmode)


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "bogus"])
def test_public_hosts_get_at_least_require(monkeypatch, mode):
    _settings(monkeypatch, CLIENTDB_ALLOWED_HOSTS="clientdb:5432")
    assert enforce_tls(_params("db.example.com", mode)).sslmode == "require"
    clear_settings_cache()


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_strong_modes_are_kept(monkeypatch, mode):
    _settings(monkeypatch, CLIENTDB_ALLOWED_HOSTS="")
    assert enforce_tls(_params("db.example.com", mode)).sslmode == mode
    clear_settings_cache()


def test_allowlisted_private_host_keeps_its_mode(monkeypatch):
    _settings(monkeypatch, CLIENTDB_ALLOWED_HOSTS="clientdb:5432")
    assert enforce_tls(_params("ClientDB", "prefer")).sslmode == "prefer"
    # Same host on another port is not the allowlisted entry.
    assert enforce_tls(_params("clientdb", "prefer", port=5433)).sslmode == "require"
    clear_settings_cache()


def test_every_client_session_goes_through_the_tls_rule(monkeypatch):
    _settings(monkeypatch, CLIENTDB_ALLOWED_HOSTS="")
    used: list[str] = []
    monkeypatch.setattr(guard, "resolve_target", lambda host, port: "203.0.113.10")

    def fake_session(params, hostaddr):
        used.append(params.sslmode)
        yield object()

    monkeypatch.setattr(guard, "_session", fake_session)
    with guard.client_session(_params("db.example.com", "prefer")):
        pass
    assert used == ["require"]
    clear_settings_cache()


# ---------- PDF text limit ----------


def _pdf(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(0, 5, text)
    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()


def test_extraction_stops_past_the_text_limit(tmp_path):
    path = tmp_path / "big.pdf"
    path.write_bytes(_pdf("Atlas " * 400))
    assert process.extract_pages(path)  # under the default limit
    with pytest.raises(process.TooMuchText):
        process.extract_pages(path, max_chars=100)


def test_too_much_text_marks_the_document_failed(monkeypatch, tmp_path):
    path = tmp_path / "x.pdf"
    path.write_bytes(_pdf("texto"))
    statuses: list[tuple] = []

    class _Conn:
        def execute(self, *_a, **_k):
            return self

        def fetchone(self):
            return ("x.pdf",)

    @contextlib.contextmanager
    def fake_connection():
        yield _Conn()

    def too_much(_path, **_k):
        raise process.TooMuchText

    monkeypatch.setattr(process, "connection", fake_connection)
    monkeypatch.setattr(process, "storage_path", lambda _name: path)
    monkeypatch.setattr(process, "extract_pages", too_much)
    monkeypatch.setattr(
        process, "_set_status", lambda doc, co, status, **kw: statuses.append((status, kw.get("error")))
    )
    process.process_document("doc-1", "company-1")
    assert statuses == [("failed", process.TOO_MUCH_TEXT_ERROR)]
