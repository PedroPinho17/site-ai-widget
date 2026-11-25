"""Detect "no information" replies (pt-PT / en), shared by SQL and document answers.

Kept to wording that says the data or documents do not hold the answer. A
factual negative that is itself sourced ("Não dispomos de entregas ao domingo
[2]", "We do not have Sunday delivery [1]") is a real answer and is not matched.
"""
from __future__ import annotations

import re

NO_INFORMATION = re.compile(
    r"(não sei\b|"
    r"não (?:tenho|tem|temos|encontrei|encontramos|há|existe|existem|consta|disponho de|dispomos de) "
    r"(?:essa |esta |qualquer |nenhuma |mais |informações |dados )?(?:informaç|dados\b|registo)|"
    r"não (?:consta|constam) (?:nos|nas|dos|das|no|na|em|que)\b|"
    r"não (?:há|existe|existem|consta|constam) (?:qualquer |nenhuma |nenhum )?"
    r"(?:referência|menção|indicação|registo)|"
    r"não (?:consigo|é possível) (?:responder|encontrar|confirmar)|"
    r"não (?:foi possível|consegui) (?:encontrar|responder|confirmar)|"
    r"(?:os |nos )?(?:documentos|excertos|dados) não (?:indicam|mencionam|referem|contêm|incluem)|"
    r"não (?:é|está|são|estão) (?:mencionad|indicad|referid|especificad)|"
    r"(?:do not|don't|does not|doesn't) have (?:that |this |any |enough )?information|"
    r"\bno information\b|"
    r"i (?:do not|don't) know|"
    r"could not find|couldn't find|"
    r"not (?:mentioned|specified|covered|stated|listed) in the (?:documents|excerpts|data)|"
    r"the (?:provided |uploaded |available |given )?(?:documents|excerpts|data) "
    r"(?:do not|don't|does not|doesn't) (?:mention|say|specify|contain|include)|"
    # Refusals: the model declines instead of answering ("I'm sorry, but I can't
    # help with that"). Only the decline itself, never "can't" about a fact.
    r"\bi (?:can't|can’t|cannot|can not|am unable to|'m unable to) "
    r"(?:help|assist) with (?:that|this)\b|"
    r"\bi (?:can't|can’t|cannot|can not) (?:share|provide|show|disclose|give) (?:that|this|those|these)\b|"
    r"não (?:posso|consigo) ajudar com (?:isso|isto)\b|"
    r"não (?:posso|consigo) (?:partilhar|fornecer|mostrar|revelar|divulgar|dar) "
    r"(?:essa|esta|essas|estas|esse|este|isso|isto|tal)\b)",
    re.IGNORECASE,
)

# Sentence ends: . ! ? or a line break (markers like "[2]." stay with their sentence).
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def sentence_has_information(sentence: str) -> bool:
    """False for a sentence that only says the information is not available."""
    return not NO_INFORMATION.search(sentence or "")


_MARKERS_ONLY = re.compile(r"^[\s\[\]\d,.;:!?()]*$")


def split_sentences(text: str) -> list[str]:
    """Sentences; a fragment of only citation markers ("Porto. [4]") joins the
    sentence before it, so it never counts as information on its own."""
    out: list[str] = []
    for part in _SENTENCE.split(text or ""):
        if not part.strip():
            continue
        if out and _MARKERS_ONLY.match(part):
            out[-1] = f"{out[-1]} {part.strip()}"
            continue
        out.append(part)
    return out


def is_no_information(text: str) -> bool:
    """True when no sentence of ``text`` carries information (or it is empty)."""
    sentences = split_sentences(text)
    return not any(sentence_has_information(s) for s in sentences)
