"""Answer language follows the visitor question."""
from __future__ import annotations

import pytest

from site_ai_widget.reply_lang import (
    detect_question_lang,
    is_short_utterance,
    resolve_answer_lang,
)


def test_detects_portuguese_catalog_and_price_questions():
    assert detect_question_lang("Que queijos têm?") == "pt"
    assert detect_question_lang("Qual o preço do azeite?") == "pt"
    assert detect_question_lang("What cheeses do you have?") == "en"


def test_short_utterances_use_previous_then_request_then_company():
    assert is_short_utterance("Olá")
    assert is_short_utterance("ok")
    assert is_short_utterance("Obrigado")
    assert not is_short_utterance("Qual o preço do azeite?")

    assert resolve_answer_lang("ok", previous_lang="pt", request_lang="en", company_locale="en") == "pt"
    assert resolve_answer_lang("ok", request_lang="en", company_locale="pt") == "en"
    assert resolve_answer_lang("ok", company_locale="pt") == "pt"


def test_substantial_question_language_beats_widget_locale():
    assert (
        resolve_answer_lang(
            "Qual o preço do azeite?",
            request_lang="en",
            company_locale="en",
        )
        == "pt"
    )
    assert (
        resolve_answer_lang(
            "How much is the olive oil?",
            request_lang="pt",
            company_locale="pt",
        )
        == "en"
    )


# --- function words decide, product names do not -------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Do you have pão de milho?", "en"),
        ("How much is the azeite?", "en"),
        ("What cheeses do you have?", "en"),
        ("Where is the store?", "en"),
        ("Que queijos têm?", "pt"),
        ("Têm queijos?", "pt"),
        ("Qual o preço do azeite?", "pt"),
        ("Onde fica a loja?", "pt"),
        ("Quanto custa o queijo de ovelha curado 300 g?", "pt"),
    ],
)
def test_question_language_comes_from_function_words(question, expected):
    assert detect_question_lang(question) == expected


@pytest.mark.parametrize("question", ["Pão de milho?", "azeite", "Azeite Atlas Premium"])
def test_product_names_alone_are_not_language_evidence(question):
    assert detect_question_lang(question) is None
    assert resolve_answer_lang(question, previous_lang="en", request_lang="pt") == "en"
    assert resolve_answer_lang(question, request_lang="pt") == "pt"


def test_detection_is_deterministic_and_local(monkeypatch):
    import socket

    def no_network(*a, **k):
        raise AssertionError("language detection must not use the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    results = {detect_question_lang("Do you have pão de milho?") for _ in range(20)}
    assert results == {"en"}
