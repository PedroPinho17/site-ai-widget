"""Review fixes: quota fallback, "não sei" chips, contrast and clear chat.

Fictional data only; no test reaches the real LLM (answer_question gets fakes).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.ask_service import answer_question
from site_ai_widget.docs.prompt import map_citations
from site_ai_widget.sqlqa.pipeline import SqlAnswer

EMBED = Path(__file__).resolve().parents[2] / "src" / "site_ai_widget" / "widget" / "embed.js"

CHUNKS = [
    {"n": 1, "document_id": "aaa", "document_name": "horario.pdf", "page": 2, "content": "x"},
    {"n": 2, "document_id": "bbb", "document_name": "entregas.pdf", "page": 5, "content": "y"},
]


def _busy(*_a, **_k):
    raise ApiError("llm_unavailable", 503)


# --- chips ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "pages"),
    [
        ("Não dispomos de entregas ao domingo [2].", [5]),
        ("We do not have Sunday delivery [2].", [5]),
        ("Não temos parque de estacionamento [1].", [2]),
        ("O preço não é mencionado. A loja abre às 10h [1].", [2]),
    ],
    ids=["pt_negative_fact", "en_negative_fact", "pt_negative_fact_2", "mixed"],
)
def test_sourced_negative_and_mixed_answers_keep_their_chips(answer, pages):
    assert [c["page"] for c in map_citations(answer, CHUNKS)] == pages


@pytest.mark.parametrize(
    "answer",
    [
        "Não sei.",
        "Não encontrei essa informação nos documentos.",
        "Os documentos não indicam o preço.",
        "I don't know.",
        "The documents do not mention that [2].",
    ],
)
def test_no_information_answers_have_no_chips(answer):
    assert map_citations(answer, CHUNKS) == []


# --- quota fallback -----------------------------------------------------------


def test_sql_busy_and_no_document_match_is_busy_not_dont_know(caplog):
    with caplog.at_level("INFO", logger="site_ai_widget"):
        out = answer_question(
            uuid4(),
            "Qual o preço do azeite?",
            lang="pt",
            sql_fn=lambda *a, **k: SqlAnswer(status="error", reason="llm_busy", attempts=1),
            docs_fn=lambda **k: {"matched": False, "answer": "", "citations": []},
        )
    assert out.mode == "none"
    assert out.notice == "busy"
    assert out.answer.startswith("Estou com muitos pedidos")
    assert out.message is None
    text = caplog.text.lower()
    assert "ask reason=llm_busy" in text
    assert "azeite" not in text
    assert "quota" not in text and "429" not in text


def test_sql_stage_raising_llm_unavailable_counts_as_busy():
    out = answer_question(
        uuid4(),
        "How much is the olive oil?",
        lang="en",
        sql_fn=_busy,
        docs_fn=lambda **k: {"matched": False, "answer": "", "citations": []},
    )
    assert out.notice == "busy"
    assert out.answer == "I am receiving many requests right now. Please try again in a minute."


def test_busy_message_defaults_to_portuguese_when_language_is_unknown():
    from site_ai_widget import ask_service

    assert ask_service._busy_message(None).startswith("Estou com muitos pedidos")


def test_plain_no_match_without_quota_is_still_dont_know():
    out = answer_question(
        uuid4(),
        "Têm vinho do Porto?",
        lang="pt",
        sql_fn=lambda *a, **k: SqlAnswer(status="no_sql", attempts=1),
        docs_fn=lambda **k: {"matched": False, "answer": "", "citations": []},
    )
    assert out.mode == "none"
    assert out.notice is None


def test_greeting_and_sql_answers_unchanged_by_busy_handling():
    hello = answer_question(uuid4(), "Olá", lang="pt", sql_fn=_busy, docs_fn=_busy)
    assert hello.mode == "chat"
    assert hello.notice is None


def test_busy_turn_is_not_counted_as_unanswered(monkeypatch):
    from site_ai_widget.ask_service import AskResponse
    from site_ai_widget.widget import access

    captured: list[tuple] = []

    class _Conn:
        def execute(self, sql, params=None):
            captured.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(access, "connection", lambda: _Conn())
    busy = AskResponse(mode="none", answer="Estou com muitos pedidos…", notice="busy", lang="pt")
    gap = AskResponse(mode="none", message="I could not answer that.", lang="pt")
    access.append_turn("s1", "c1", "Pergunta?", busy, lang="pt")
    access.append_turn("s1", "c1", "Pergunta?", gap, lang="pt")
    flags = [params[5] for sql, params in captured if "'assistant'" in sql]
    assert flags == [False, True]


# --- embed.js -------------------------------------------------------------------


def _node_eval(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_embed_contrast_is_always_wcag_aa():
    source = EMBED.read_text(encoding="utf-8")
    start = source.index("  function srgbChannel")
    end = source.index("  function applyTheme")
    helpers = source[start:end]
    script = (
        helpers
        + "\nconst out = {};\n"
        + "for (let v = 0; v < 256; v += 5) {\n"
        + "  for (const hex of ['#' + [v, v, v].map(n => n.toString(16).padStart(2, '0')).join(''),\n"
        + "                     '#' + [v, 255 - v, 128].map(n => n.toString(16).padStart(2, '0')).join('')]) {\n"
        + "    const ink = contrastOn(hex);\n"
        + "    out[hex] = ratio(luminance(hex), luminance(ink));\n"
        + "  }\n"
        + "}\n"
        + "out.mid = contrastOn('#777777');\n"
        + "process.stdout.write(JSON.stringify(out));\n"
    )
    result = _node_eval(script)
    assert result.pop("mid") == "#000000"
    worst = min(result.values())
    assert worst >= 4.5, worst


def test_embed_clear_chat_is_in_widget_and_drops_in_flight_answers():
    source = EMBED.read_text(encoding="utf-8")
    assert "window.confirm(" not in source
    assert 'confirmBar.setAttribute("role", "group")' in source
    assert "this._gen += 1" in source
    # Both the history load and the ask guard on the generation.
    assert source.count("gen !== self._gen") >= 4
    assert "previousLang = null" in source
    assert "innerHTML" not in source
    assert 'attachShadow({ mode: "closed" })' in source
    assert "adoptedStyleSheets" in source


def test_embed_shows_localized_busy_copy_and_history_times():
    source = EMBED.read_text(encoding="utf-8")
    assert 'result.data.notice === "busy" ? copy.busy' in source
    assert source.count("busy: ") == 2  # pt and en
    assert "self._add(item.role, text, null, item.created_at)" in source
    assert "opacity: 0.7" not in source
