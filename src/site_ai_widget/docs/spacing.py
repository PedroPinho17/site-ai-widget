"""Restore spaces the model dropped between words and numbers from source text."""
from __future__ import annotations

import re
from typing import Any

# "Amendoeiras 48", "Flores 12", "Aqueduto 7" — word then house/street number.
_WORD_NUM = re.compile(
    r"([A-Za-zÀ-ÖØ-öø-ÿ]{3,})\s+(\d{1,5})\b",
    re.UNICODE,
)


def restore_source_spacing(answer: str, chunks: list[dict[str, Any]]) -> str:
    """If the reply glued a source ``Word N`` into ``WordN``, put the space back.

    Only restores pairs that appear spaced in a retrieved excerpt, so invented
    digits are never rewritten. Citations and other markup are left alone.
    """
    text = answer or ""
    if not text or not chunks:
        return text
    for chunk in chunks:
        content = str(chunk.get("content") or "")
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
