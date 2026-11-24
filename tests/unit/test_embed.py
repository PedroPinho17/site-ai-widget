"""Embed script delivery and the demo page origin allowlist."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.conftest import postgres_available

ROOT = Path(__file__).resolve().parents[2]
EMBED_JS = ROOT / "src" / "site_ai_widget" / "widget" / "embed.js"

_SECRET_MARKERS = (
    "wk_",
    "sk-",
    "sk_",
    "JWT_SECRET",
    "DATABASE_URL",
    "GROQ",
    "BEGIN PRIVATE",
    "BEGIN RSA",
    "api_key",
    "api-key",
    "password",
)


def test_embed_js_is_javascript_without_secrets():
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.pool import close_pool

    clear_settings_cache()
    close_pool()
    with TestClient(create_app()) as client:
        response = client.get("/embed.js")
    close_pool()
    clear_settings_cache()

    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert content_type.startswith("application/javascript")
    assert "charset=utf-8" in content_type.lower()
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    cache = response.headers["cache-control"]
    match = re.search(r"max-age=(\d+)", cache)
    assert match is not None
    assert int(match.group(1)) <= 3600
    assert response.content == EMBED_JS.read_bytes()
    body = response.content.decode("utf-8")
    assert "wk_demo_mercearia_atlas" not in body
    for marker in _SECRET_MARKERS:
        assert marker not in body


def test_embed_js_has_no_unsafe_calls():
    text = EMBED_JS.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", "", text)
    assert "innerHTML" not in text
    assert "innerhtml" not in text.lower()
    assert "eval" not in text
    assert "document.write" not in text
    assert 'credentials:"include"' not in compact
    assert "credentials:'include'" not in compact


def _js() -> str:
    return EMBED_JS.read_text(encoding="utf-8")


def test_shadow_root_is_closed():
    text = _js()
    assert 'attachShadow({ mode: "closed" })' in text
    assert 'mode: "open"' not in text


def test_server_fallback_text_is_never_shown():
    """mode "none" uses the localized copy, never the server's English message."""
    text = _js()
    assert not re.search(r"data\.message\b", text)
    assert 'mode === "docs" && typeof data.answer === "string"' in text
    assert 'item.mode === "none"' in text
    assert "return copy.emptyAnswer;" in text


def test_history_shows_sql_turns_by_row_count():
    text = _js()
    assert 'item.mode === "sql"' in text
    assert "rowsMany" in text and "rowsOne" in text
    assert "self._add(item.role, text);" in text


def test_no_relative_api_fallback():
    """Without a script src the widget stays blocked instead of posting to the host page."""
    text = _js()
    assert re.search(r'if \(!src\) \{\s*return "";', text)
    assert "if (!key || !apiBase) {" in text
    assert "if (this._busy || this._blocked || !apiBase) {" in text


def test_script_guards_against_double_load():
    text = _js()
    assert "window.__siteAiWidgetLoaded" in text
    assert 'window.customElements.get("site-ai-widget")' in text
    assert 'document.querySelector("site-ai-widget")' in text


def test_token_storage_is_per_widget_key():
    text = _js()
    assert 'var storageKey = "site-ai-widget:" + key + ":session";' in text
    assert "document.cookie" not in text
    assert "sessionStorage" not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_embed_js_parses():
    result = subprocess.run(
        [shutil.which("node"), "--check", str(EMBED_JS)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not postgres_available(), reason="Postgres is not reachable (docker compose up -d db)")
def test_demo_origin_allowed_and_other_origin_refused(monkeypatch, demo_key_restored):
    """The demo page origin may ask; any other origin is refused before the question runs."""
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.migrate import migrate
    from site_ai_widget.db.pool import close_pool, connection
    from site_ai_widget.seed import DEMO_WIDGET_KEY, DEMO_WIDGET_ORIGIN, ensure_demo_widget_key
    from site_ai_widget.sqlqa.pipeline import SqlAnswer

    monkeypatch.setenv("AUTH_RATE_LIMIT", "1000")
    monkeypatch.setenv("API_RATE_LIMIT", "1000")
    monkeypatch.setenv("ASK_RATE_LIMIT", "1000")
    monkeypatch.setenv("WIDGET_KEY_RATE_LIMIT", "1000")
    clear_settings_cache()
    reset_limiters()
    close_pool()
    migrate()

    calls: list[int] = []

    def fake_sql(*_args, **_kwargs):
        calls.append(1)
        return SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[["Azeite"]],
            row_count=1,
            truncated=False,
            attempts=1,
        )

    monkeypatch.setattr("site_ai_widget.sqlqa.pipeline.answer_with_sql", fake_sql)

    with connection() as conn:
        # A throwaway fictional company. demo_key_restored puts the key back on
        # its real owner afterwards; the test never creates Mercearia Atlas.
        company_id = conn.execute(
            """
            INSERT INTO companies (name, slug, default_locale)
            VALUES (%s, %s, 'pt')
            RETURNING id
            """,
            ("Embed Test Shop", f"embed-test-{uuid4().hex[:8]}"),
        ).fetchone()[0]
        ensure_demo_widget_key(conn, company_id)

    close_pool()
    app = create_app()
    with TestClient(app) as client:
        refused = client.post(
            f"/v1/widget/{DEMO_WIDGET_KEY}/ask",
            headers={"Origin": "https://not-allowed.example"},
            json={"question": "qual o preco do azeite?"},
        )
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "origin_not_allowed"
        assert calls == []

        allowed = client.post(
            f"/v1/widget/{DEMO_WIDGET_KEY}/ask",
            headers={"Origin": DEMO_WIDGET_ORIGIN},
            json={"question": "qual o preco do azeite?"},
        )
    close_pool()
    reset_limiters()
    clear_settings_cache()

    assert allowed.status_code == 200
    assert allowed.json()["mode"] == "sql"
    assert calls == [1]
