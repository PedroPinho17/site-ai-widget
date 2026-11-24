"""Restore spaces the model dropped between words and numbers from source text."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# "Amendoeiras 48", "Flores 12", "Aqueduto 7" — word then house/street number.
_WORD_NUM = re.compile(
    r"([A-Za-zÀ-ÖØ-öø-ÿ]{3,})\s+(\d{1,5})\b",
    re.UNICODE,
)

# Exotic spaces (NBSP, NNBSP, thin, hair, …) that PDFs and models insert between
# a street name and its number — treat as a normal space so "Amendoeiras\u202f48"
# matches the spaced source pair and is never left glued for the visitor.
_UNICODE_SPACE = re.compile(
    r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]",
)


# Invisible characters a model may put between "Amendoeiras" and "48": they make
# the pair look glued and dodge the spacing check. Removed before any matching.
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
# Non-breaking / figure hyphens the model uses in "1170\u2011000" or "contactar\u2011nos".
_ODD_HYPHEN = re.compile(r"[\u2010\u2011\u2012]")


def normalize_answer_spaces(text: str) -> str:
    """Model reply -> plain characters the checks and the visitor expect.

    NBSP, narrow NBSP (U+202F), thin and other Unicode spaces become an ASCII
    space; zero-width characters are removed; non-breaking hyphens become "-".
    Runs of spaces collapse. Nothing else changes.
    """
    if not text:
        return text
    text = _ZERO_WIDTH.sub("", text)
    text = _ODD_HYPHEN.sub("-", text)
    # Also catch any other Zs category char the regex might miss.
    flat = "".join(
        " " if ch != " " and unicodedata.category(ch) == "Zs" else ch for ch in text
    )
    flat = _UNICODE_SPACE.sub(" ", flat)
    flat = re.sub(r" {2,}", " ", flat)
    # No trailing space before a line break ("9,90 €. \nTemos" -> "9,90 €.\nTemos").
    return re.sub(r" +\n", "\n", flat)


def restore_source_spacing(answer: str, chunks: list[dict[str, Any]]) -> str:
    """If the reply glued a source ``Word N`` into ``WordN``, put the space back.

    Only restores pairs that appear spaced in a retrieved excerpt, so invented
    digits are never rewritten. Citations and other markup are left alone.
    Unicode spaces between word and number are normalized first so NBSP/NNBSP
    from PDF extracts do not bypass the glue check.
    """
    text = normalize_answer_spaces(answer or "")
    if not text or not chunks:
        return text
    for chunk in chunks:
        content = normalize_answer_spaces(str(chunk.get("content") or ""))
        for match in _WORD_NUM.finditer(content):
            word, num = match.group(1), match.group(2)
            spaced = f"{word} {num}"
            # Whole token only: "Lote12" inside "Lote123" or "XLote12" is a
            # different value and must not be split.
            glued = re.compile(rf"(?<![^\W\d_]){re.escape(word)}{re.escape(num)}(?!\d)")
            if spaced in text or not glued.search(text):
                continue
            text = glued.sub(spaced, text)
    return text
