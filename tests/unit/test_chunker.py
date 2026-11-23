"""Chunker: word-aligned, overlap, no page crossing, empty pages skipped."""
from __future__ import annotations

from site_ai_widget.docs.chunk import chunk_pages, chunk_text


def test_empty_text():
    assert chunk_text("   ") == []


def test_short_text_one_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_overlap_and_word_aligned():
    words = " ".join(f"w{i:03d}" for i in range(80))  # ~400+ chars
    chunks = chunk_text(words, size=80, overlap=20)
    assert len(chunks) >= 2
    for c in chunks:
        assert not c.startswith(" ")
        assert not c.endswith(" ")
        # never mid-word from our synthetic tokens
        assert all(tok.startswith("w") for tok in c.split())


def test_pages_never_cross_and_skip_empty():
    pages = [
        "First page content about apples and oranges.",
        "   ",
        "Third page with more fruit names and delivery zones.",
    ]
    chunks = chunk_pages(pages, size=40, overlap=10)
    assert chunks
    assert all(c.page in (1, 3) for c in chunks)
    assert 2 not in {c.page for c in chunks}
    # indices are contiguous across the document
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
