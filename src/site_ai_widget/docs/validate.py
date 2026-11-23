"""PDF upload validation: magic bytes, size, encryption, page limit."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from site_ai_widget.api.errors import ApiError

PDF_MAGIC = b"%PDF-"
CHUNK = 64 * 1024


@dataclass(frozen=True)
class ValidatedPdf:
    path: Path
    size_bytes: int
    sha256: str
    page_count: int
    original_name: str


def sanitize_original_name(name: str | None, max_len: int = 200) -> str:
    """Display-only filename: strip path components and control characters."""
    raw = (name or "document.pdf").replace("\\", "/").split("/")[-1].strip()
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw)
    raw = raw.strip(" .") or "document.pdf"
    if not raw.lower().endswith(".pdf"):
        raw = f"{raw}.pdf"
    return raw[:max_len]


def assert_pdf_magic(header: bytes) -> None:
    if not header.startswith(PDF_MAGIC):
        raise ApiError("not_pdf", 400)


def stream_upload_to_path(
    file_obj,
    dest: Path,
    *,
    max_bytes: int,
) -> tuple[int, str]:
    """Copy ``file_obj`` to ``dest`` while enforcing size and PDF magic.

    Aborts as soon as the limit is exceeded; never buffers the whole body.
    Returns ``(size_bytes, sha256_hex)``.
    """
    hasher = hashlib.sha256()
    total = 0
    first = True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as out:
            while True:
                block = file_obj.read(CHUNK)
                if not block:
                    break
                if first:
                    assert_pdf_magic(block[:5] if len(block) >= 5 else block)
                    first = False
                total += len(block)
                if total > max_bytes:
                    raise ApiError("upload_too_large", 413, n=max_bytes // (1024 * 1024))
                hasher.update(block)
                out.write(block)
        if total == 0:
            raise ApiError("not_pdf", 400)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return total, hasher.hexdigest()


def inspect_pdf(path: Path, *, max_pages: int) -> int:
    """Return page count; reject encrypted PDFs and those over ``max_pages``."""
    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise ApiError("pdf_corrupt", 400) from exc
    if reader.is_encrypted:
        # Try empty password; still treat as encrypted if locked
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise ApiError("pdf_encrypted", 400)
        # Some readers flip is_encrypted after decrypt; re-check usable pages
    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise ApiError("pdf_corrupt", 400) from exc
    if page_count < 1:
        raise ApiError("pdf_corrupt", 400)
    if page_count > max_pages:
        raise ApiError("pdf_too_many_pages", 400, n=max_pages)
    return page_count
