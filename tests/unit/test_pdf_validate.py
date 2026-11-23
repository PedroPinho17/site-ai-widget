"""PDF validation: magic bytes, size, encryption, page limit."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from pypdf import PdfWriter

from site_ai_widget.api.errors import ApiError
from site_ai_widget.docs.validate import (
    assert_pdf_magic,
    inspect_pdf,
    sanitize_original_name,
    stream_upload_to_path,
)


def _minimal_pdf(pages: int = 1) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_assert_pdf_magic_ok():
    assert_pdf_magic(b"%PDF-1.4 rest")


def test_assert_pdf_magic_rejects_non_pdf():
    with pytest.raises(ApiError) as exc:
        assert_pdf_magic(b"%PNG....")
    assert exc.value.code == "not_pdf"


def test_stream_upload_enforces_size(tmp_path: Path):
    dest = tmp_path / "x.pdf"
    data = b"%PDF-" + b"x" * 1000
    with pytest.raises(ApiError) as exc:
        stream_upload_to_path(io.BytesIO(data), dest, max_bytes=100)
    assert exc.value.code == "upload_too_large"
    assert not dest.exists()


def test_stream_upload_ok(tmp_path: Path):
    dest = tmp_path / "ok.pdf"
    raw = _minimal_pdf()
    size, digest = stream_upload_to_path(io.BytesIO(raw), dest, max_bytes=10_000_000)
    assert size == len(raw)
    assert len(digest) == 64
    assert dest.read_bytes().startswith(b"%PDF-")


def test_inspect_rejects_too_many_pages(tmp_path: Path):
    dest = tmp_path / "many.pdf"
    dest.write_bytes(_minimal_pdf(pages=5))
    with pytest.raises(ApiError) as exc:
        inspect_pdf(dest, max_pages=3)
    assert exc.value.code == "pdf_too_many_pages"


def test_inspect_rejects_encrypted(tmp_path: Path):
    dest = tmp_path / "enc.pdf"
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("secret-password")
    buf = io.BytesIO()
    w.write(buf)
    dest.write_bytes(buf.getvalue())
    with pytest.raises(ApiError) as exc:
        inspect_pdf(dest, max_pages=300)
    assert exc.value.code == "pdf_encrypted"


def test_sanitize_original_name():
    assert sanitize_original_name("../../evil\\path.pdf") == "path.pdf"
    assert sanitize_original_name("notes") == "notes.pdf"
