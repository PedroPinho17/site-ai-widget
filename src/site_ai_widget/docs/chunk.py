"""Per-page text chunking with word-aligned overlap (never crosses pages)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    page: int  # 1-based
    chunk_index: int  # 0-based within the document
    content: str


def chunk_text(text: str, size: int = 1000, overlap: int = 200) -> list[str]:
    """Split ``text`` into overlapping chunks snapped to whitespace."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    def is_space(i: int) -> bool:
        return i < len(cleaned) and cleaned[i].isspace()

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        if end < len(cleaned):
            minimum = start + size * 4 // 5
            e = end
            while e > minimum and not is_space(e):
                e -= 1
            if e > minimum:
                end = e
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        nxt = max(end - overlap, start + 1)
        while nxt < end and not is_space(nxt - 1):
            nxt += 1
        start = nxt
    return chunks


def chunk_pages(
    pages: list[str],
    *,
    size: int = 1000,
    overlap: int = 200,
) -> list[TextChunk]:
    """Chunk each page separately; empty pages are skipped."""
    out: list[TextChunk] = []
    index = 0
    for page_no, text in enumerate(pages, start=1):
        for piece in chunk_text(text, size=size, overlap=overlap):
            out.append(TextChunk(page=page_no, chunk_index=index, content=piece))
            index += 1
    return out
