"""Resolve the language used for visitor-facing answers.

Deterministic and local: no network call, no model. The server is the only place
this rule lives; the widget and the panel send back the ``lang`` of the previous
answer as ``previous_lang`` instead of guessing on their own.
"""
from __future__ import annotations

import re
from typing import Literal

Lang = Literal["pt", "en"]

_SHORT = re.compile(
    r"^(?:ok|okay|sim|n[aã]o|yes|no|ol[aá]|oi|hey|hi|hello|"
    r"bom dia|boa tarde|boa noite|good morning|good afternoon|good evening|"
    r"obrigad[oa]|thanks|thank you|thx)[!.?…\s]*$",
    re.IGNORECASE,
)

# Function words only. Product names ("azeite", "pão", "queijo", "stock") are not
# language evidence: an English visitor in a Portuguese shop still writes
# "Do you have pão de milho?" and must get an English answer.
_PT_WORDS = frozenset(
    {
        "que", "qual", "quais", "quanto", "quanta", "quantos", "quantas", "onde",
        "como", "quando", "porque", "têm", "tem", "tens", "temos", "é", "são",
        "está", "estão", "vocês", "voces", "o", "os", "as", "um", "uma", "uns",
        "umas", "do", "da", "dos", "das", "no", "na", "nos", "nas", "para", "com",
        "sem", "não", "nao", "custa", "custam", "há", "existe", "existem", "vendem",
        "posso", "pode", "podem", "obrigado", "obrigada", "aberto", "abertos",
        "fecha", "abre", "hoje", "amanhã", "morada", "preço", "horário",
    }
)
_EN_WORDS = frozenset(
    {
        "what", "which", "where", "how", "when", "why", "who", "do", "does", "did",
        "you", "your", "have", "has", "is", "are", "the", "an", "of", "for", "with",
        "without", "much", "many", "cost", "costs", "sell", "open", "opening",
        "close", "closes", "today", "tomorrow", "there", "can", "could", "price",
        "hours", "address", "thanks", "thank", "please", "any", "in", "on", "at",
    }
)
# "do", "no" and "a" exist in both languages; they only count with a neighbour.
_AMBIGUOUS = frozenset({"do", "no", "a", "in"})
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_ACCENT = re.compile(r"[áàãâéêíóôõúç]", re.IGNORECASE)


def is_short_utterance(question: str) -> bool:
    """Greetings, thanks and tiny replies like ``ok`` / ``sim``."""
    text = " ".join((question or "").strip().split())
    return bool(text) and len(text) <= 40 and _SHORT.match(text) is not None


def detect_question_lang(question: str) -> Lang | None:
    """Language of a substantial visitor question, or None when unclear.

    Counts Portuguese and English function words; the larger count wins. Accents
    only break a tie (a product name like "Pão" must not outvote "Do you have").
    """
    words = [w.casefold() for w in _WORD.findall(question or "")]
    if not words:
        return None
    pt = sum(1 for w in words if w in _PT_WORDS and w not in _AMBIGUOUS)
    en = sum(1 for w in words if w in _EN_WORDS and w not in _AMBIGUOUS)
    if pt > en:
        return "pt"
    if en > pt:
        return "en"
    if pt and _ACCENT.search(question or ""):
        return "pt"
    return None


def normalize_lang(lang: str | None) -> Lang:
    if lang and str(lang).lower().startswith("pt"):
        return "pt"
    if lang and str(lang).lower().startswith("en"):
        return "en"
    return "en"


def resolve_answer_lang(
    question: str,
    *,
    request_lang: str | None = None,
    previous_lang: str | None = None,
    company_locale: str | None = None,
) -> Lang:
    """Language for the answer.

    1. Short utterances (``Olá``, ``ok``) -> previous answer language, then
       request ``data-lang``, then company widget locale.
    2. Otherwise -> language of the question when detectable, then the same
       fallbacks.
    """
    if is_short_utterance(question):
        return normalize_lang(previous_lang or request_lang or company_locale)
    detected = detect_question_lang(question)
    if detected is not None:
        return detected
    return normalize_lang(previous_lang or request_lang or company_locale)
