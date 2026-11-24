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
    assert 'typeof data.answer === "string" && data.answer' in text
    assert 'item.mode === "none"' in text
    assert "return copy.emptyAnswer;" in text


def test_history_prefers_stored_assistant_text():
    text = _js()
    assert 'typeof item.text === "string" && item.text' in text
    assert 'item.mode === "sql"' in text
    assert "rowsMany" in text and "rowsOne" in text
    assert "self._add(item.role, text);" in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_history_text_runs_like_the_live_answer():
    """Runs the real historyText from embed.js in node with fictional history rows."""
    import json

    source = _js()
    start = source.index("  function historyText(item) {")
    end = source.index("\n  }\n", start) + 4
    function = source[start:end]
    cases = [
        {"role": "assistant", "mode": "sql", "text": "Azeite: 8,90", "row_count": 1},
        {"role": "assistant", "mode": "none", "text": "I could not answer that from the database."},
        {"role": "assistant", "mode": "sql", "text": None, "row_count": 3},
        {"role": "user", "mode": None, "text": "qual o preco do azeite?"},
    ]
    script = (
        'var copy = {emptyAnswer: "Não consegui responder a isso.", '
        'rowsOne: "Resultado com 1 linha.", rowsMany: "Resultado com {n} linhas."};\n'
        + function
        + "\nprocess.stdout.write(JSON.stringify("
        + json.dumps(cases)
        + ".map(historyText)));"
    )
    result = subprocess.run(
        [shutil.which("node"), "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "Azeite: 8,90",  # same text the visitor saw live
        "Não consegui responder a isso.",  # localized, never the stored English fallback
        "Resultado com 3 linhas.",  # older rows saved before answers were stored
        "qual o preco do azeite?",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_source_chips_expand_and_typing_in_embed():
    """Runs sourceChips from embed.js: localized document chips, never table names."""
    import json

    source = _js()
    start = source.index("  function sourceChipItems(data) {")
    chips_fn = source.index("  function sourceChips(data) {")
    end = source.index("\n  }\n", chips_fn) + 4
    function = source[start:end]
    cell_start = source.index("  function cellText(value) {")
    cell_end = source.index("\n  }\n", cell_start) + 4
    cell = source[cell_start:cell_end]
    cases = [
        {"mode": "docs", "sources": [{"document_name": "Manual.pdf", "page": 2}], "tables": []},
        {"mode": "sql", "sources": [], "tables": ["products"]},
        {"mode": "chat", "answer": "Olá!", "sources": [], "tables": []},
    ]
    results = {}
    for lang, labels in {
        "pt": '{sourceDoc: "Documento", sourcePage: "p."}',
        "en": '{sourceDoc: "Document", sourcePage: "p."}',
    }.items():
        script = (
            f"var copy = {labels};\n"
            + cell
            + "\n"
            + function
            + "\nprocess.stdout.write(JSON.stringify("
            + json.dumps(cases)
            + ".map(sourceChips)));"
        )
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        results[lang] = json.loads(result.stdout)
    assert results["pt"] == [["Documento: Manual.pdf p. 2"], [], []]
    assert results["en"] == [["Document: Manual.pdf p. 2"], [], []]
    assert "Base de dados" not in source
    assert "data.tables" not in source
    assert "is-full" in source
    assert "copy.expand" in source
    assert "copy.typing" in source
    assert "_trapFocus" in source
    assert 'attachShadow({ mode: "closed" })' in source


def test_widget_suggestions_are_generic_for_any_company():
    """The same script serves every company: no product-specific suggestions."""
    source = _js()
    start = source.index("suggestions: [")
    pt = source[start : source.index("]", start)]
    start_en = source.index("suggestions: [", start + 1)
    en = source[start_en : source.index("]", start_en)]
    for text in (pt, en):
        assert "azeite" not in text.lower()
        assert "olive" not in text.lower()
        assert "stock" not in text.lower()


def test_styles_are_csp_safe_with_a_style_fallback():
    source = _js()
    assert "new CSSStyleSheet()" in source
    assert "adoptedStyleSheets = [sheet]" in source
    # The <style> element is only the fallback inside applyStyles.
    start = source.index("  function applyStyles(root) {")
    end = source.index("\n  }\n", start)
    assert source.count('createElement("style")') == 1
    assert start < source.index('createElement("style")') < end
    assert "innerHTML" not in source
    assert "setAttribute(\"style\"" not in source


def test_focus_trap_only_cycles_inside_the_panel():
    source = _js()
    start = source.index("    _trapFocus(event) {")
    end = source.index("\n    }\n", start)
    body = source[start:end]
    assert 'this._panel.querySelectorAll("button, input, textarea")' in body
    assert "this._root.querySelectorAll" not in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_safe_markdown_strips_citations_and_renders_bold():
    import json

    source = _js()
    assert "function stripCitations(text)" in source
    assert "function renderSafeMarkdown(text)" in source
    assert "appendInline" in source
    start = source.index("  function stripCitations(text) {")
    end = source.index("\n  function formatAnswer(data) {")
    helpers = source[start:end]
    assert ".innerHTML" not in helpers
    assert "innerHTML =" not in helpers
    script = (
        "function el(tag) {\n"
        "  var node = { tagName: String(tag).toUpperCase(), childNodes: [], className: '', _text: null };\n"
        "  Object.defineProperty(node, 'textContent', {\n"
        "    get: function () {\n"
        "      if (this._text != null) return this._text;\n"
        "      return this.childNodes.map(function (n) {\n"
        "        return n.nodeValue != null ? n.nodeValue : n.textContent;\n"
        "      }).join('');\n"
        "    },\n"
        "    set: function (v) { this._text = String(v); this.childNodes = []; }\n"
        "  });\n"
        "  Object.defineProperty(node, 'innerHTML', {\n"
        "    get: function () {\n"
        "      if (this._text != null) return this._text;\n"
        "      return this.childNodes.map(function (n) {\n"
        "        if (n.nodeValue != null) return n.nodeValue;\n"
        "        return '<' + n.tagName.toLowerCase() + '>' + n.innerHTML + '</' + n.tagName.toLowerCase() + '>';\n"
        "      }).join('');\n"
        "    }\n"
        "  });\n"
        "  node.appendChild = function (n) { this.childNodes.push(n); return n; };\n"
        "  return node;\n"
        "}\n"
        "var document = { createElement: el, createTextNode: function (t) { return { nodeValue: t }; } };\n"
        + helpers
        + "\nvar out = renderSafeMarkdown('**Contacte-nos**\\n- Rua\\n【1】');\n"
        + "process.stdout.write(JSON.stringify({html: out.innerHTML, text: out.textContent, "
        + "stripped: stripCitations('Abrimos 【1】 e [2].')}));"
    )
    result = subprocess.run(
        [shutil.which("node"), "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert "Contacte-nos" in body["text"]
    assert "Rua" in body["text"]
    assert "【1】" not in body["text"]
    assert "<strong>" in body["html"]
    assert "【1】" not in body["stripped"]
    assert "[2]" not in body["stripped"]


def test_format_answer_prefers_server_answer_for_sql():
    text = _js()
    assert 'typeof data.answer === "string" && data.answer' in text
    assert "columns.map" not in text
    assert 'mode === "sql"' in text


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_markdown_renders_html_in_replies_as_plain_text_and_tidies_citations():
    """Runs renderSafeMarkdown with a recording DOM stub: only p/ul/li/strong/br nodes."""
    import json

    source = _js()
    start = source.index("  function stripCitations(text) {")
    end = source.index("\n  function formatAnswer(data) {")
    helpers = source[start:end]
    script = (
        "var made = [];\n"
        "function el(tag) {\n"
        "  made.push(String(tag).toLowerCase());\n"
        "  return { tagName: String(tag).toUpperCase(), childNodes: [], className: '',\n"
        "    appendChild: function (n) { this.childNodes.push(n); return n; },\n"
        "    set textContent(v) { this.childNodes = [{ nodeValue: String(v) }]; } };\n"
        "}\n"
        "function text(node) {\n"
        "  if (node.nodeValue != null) return node.nodeValue;\n"
        "  return node.childNodes.map(text).join('');\n"
        "}\n"
        "var document = { createElement: el, createTextNode: function (t) { return { nodeValue: t }; } };\n"
        + helpers
        + "\nvar evil = '<img src=x onerror=alert(1)> **<script>x</script>**\\n- <b>Rua</b>';\n"
        + "var out = renderSafeMarkdown(evil);\n"
        + "process.stdout.write(JSON.stringify({made: made, text: text(out), "
        + "tidy: stripCitations('Abrimos às 9h 【1】 e aos sábados [2].'), "
        + "address: stripCitations('Rua das Amendoeiras [1]48, Lisboa')}));"
    )
    result = subprocess.run(
        [shutil.which("node"), "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert set(body["made"]) <= {"div", "p", "ul", "li", "strong", "br"}
    # The markup is visible as text, never parsed into elements.
    assert "<img src=x onerror=alert(1)>" in body["text"]
    assert "<script>x</script>" in body["text"]
    assert "<b>Rua</b>" in body["text"]
    assert body["tidy"] == "Abrimos às 9h e aos sábados."
    assert body["address"] == "Rua das Amendoeiras 48, Lisboa"


def test_widget_never_guesses_the_language_and_echoes_the_server_lang():
    """The server decides the answer language; the widget only sends it back."""
    source = _js()
    for name in ("guessQuestionLang", "isShortUtterance", "rememberQuestionLang"):
        assert name not in source
    assert "var previousLang = null;" in source
    assert 'if (result.data.lang === "pt" || result.data.lang === "en") {' in source
    assert "previousLang = result.data.lang;" in source
    assert "payload.previous_lang = previousLang;" in source
