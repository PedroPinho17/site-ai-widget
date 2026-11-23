"""Seed fictional demo companies and optional demo PDFs."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from site_ai_widget.api.auth import hash_password
from site_ai_widget.api.config import get_settings
from site_ai_widget.db.pool import close_pool, connection
from site_ai_widget.docs.process import process_document
from site_ai_widget.docs.storage import new_storage_name, storage_path, upload_root
from site_ai_widget.docs.validate import inspect_pdf

load_dotenv()
log = logging.getLogger("site_ai_widget")

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "demo"

DEMOS = (
    {
        "name": "Mercearia Atlas",
        "slug": "mercearia-atlas",
        "email": "admin@mercearia-atlas.example",
        "locale": "pt",
        "password_env": "DEMO_PASSWORD_ATLAS",
        "greeting": "Bem-vindo à Mercearia Atlas! Em que posso ajudar?",
        "assistant_name": "Atlas",
        "primary_color": "#0f766e",
        "pdf": "mercearia-atlas-manual.pdf",
        "pdf_display": "Mercearia Atlas - Manual do Cliente.pdf",
    },
    {
        "name": "Oficina Lima",
        "slug": "oficina-lima",
        "email": "admin@oficina-lima.example",
        "locale": "pt",
        "password_env": "DEMO_PASSWORD_LIMA",
        "greeting": "Olá, aqui é a Oficina Lima. Como podemos ajudar?",
        "assistant_name": "Lima",
        "primary_color": "#b45309",
        "pdf": "oficina-lima-manual.pdf",
        "pdf_display": "Oficina Lima - Guia rapido.pdf",
    },
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _insert_demo_pdf(company_id, admin_id, demo: dict, database_url: str | None):
    """Copy the demo PDF and COMMIT its ``documents`` row.

    Returns ``(document_id, status)`` for the company's copy of this PDF (new or
    existing), or ``None`` when the demo file is missing.
    """
    src = DEMO_DIR / demo["pdf"]
    if not src.is_file():
        log.warning("Demo PDF missing: %s (run scripts/make_demo_pdfs.py)", src)
        return None
    digest = _sha256_file(src)
    with connection(database_url) as conn:
        existing = conn.execute(
            "SELECT id, status FROM documents WHERE company_id = %s AND sha256 = %s",
            (str(company_id), digest),
        ).fetchone()
    if existing:
        log.info("Demo PDF already present for %s (status=%s)", demo["slug"], existing[1])
        return existing[0], existing[1]

    upload_root()
    storage_name = new_storage_name()
    dest = storage_path(storage_name)
    shutil.copy2(src, dest)
    try:
        page_count = inspect_pdf(dest, max_pages=get_settings().max_pdf_pages)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    try:
        # Own short transaction: the row is committed (visible to any worker,
        # including a running API) before processing starts.
        with connection(database_url) as conn:
            row = conn.execute(
                """
                INSERT INTO documents (
                    company_id, original_name, storage_name, size_bytes, sha256,
                    page_count, status, created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id
                """,
                (
                    str(company_id),
                    demo["pdf_display"],
                    storage_name,
                    dest.stat().st_size,
                    digest,
                    page_count,
                    str(admin_id) if admin_id else None,
                ),
            ).fetchone()
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    log.info("Added demo PDF for %s (%s)", demo["slug"], demo["pdf"])
    return row[0], "pending"


def _document_status(document_id, database_url: str | None) -> str | None:
    with connection(database_url) as conn:
        row = conn.execute(
            "SELECT status FROM documents WHERE id = %s", (str(document_id),)
        ).fetchone()
    return row[0] if row else None


def reset_stuck_processing_document(
    document_id,
    company_id,
    *,
    database_url: str | None = None,
    stuck_after_seconds: int | None = None,
) -> bool:
    """Reset a demo document from ``processing`` → ``pending`` only if stuck.

    A row is stuck when ``updated_at`` is older than ``stuck_after_seconds``
    (default from ``DOCUMENT_STUCK_PROCESSING_SECONDS``). Recently updated
    ``processing`` rows are left alone so a live API worker is not interrupted.

    Returns True when the row was reset to ``pending``.
    """
    seconds = (
        stuck_after_seconds
        if stuck_after_seconds is not None
        else get_settings().document_stuck_processing_seconds
    )
    with connection(database_url) as conn:
        row = conn.execute(
            """
            UPDATE documents
            SET status = 'pending', error = NULL, updated_at = now()
            WHERE id = %s
              AND company_id = %s
              AND status = 'processing'
              AND updated_at < now() - (%s * interval '1 second')
            RETURNING id
            """,
            (str(document_id), str(company_id), int(seconds)),
        ).fetchone()
    return row is not None


def _seed_pdf(company_id, admin_id, demo: dict, database_url: str | None = None) -> str | None:
    """Insert (if needed) and process the demo PDF synchronously.

    Runs in the seed process and returns only once the document is ``ready``
    or ``failed`` — except when it is ``processing`` and still fresh (likely a
    live API worker), in which case it is left alone.
    """
    found = _insert_demo_pdf(company_id, admin_id, demo, database_url)
    if found is None:
        return None
    doc_id, status = found

    if status == "processing":
        if reset_stuck_processing_document(doc_id, company_id, database_url=database_url):
            log.info(
                "Reset stuck processing demo PDF for %s → pending",
                demo["slug"],
            )
            status = "pending"
        else:
            log.info(
                "Demo PDF for %s still processing (updated recently); leaving alone",
                demo["slug"],
            )
            return status

    if status == "pending":
        log.info("Processing demo PDF for %s ...", demo["slug"])
        process_document(doc_id, company_id)
        status = _document_status(doc_id, database_url)
    log.info("Demo PDF for %s: %s", demo["slug"], status)
    return status


def seed(database_url: str | None = None, demos: tuple[dict, ...] = DEMOS) -> list[str]:
    created: list[str] = []
    for demo in demos:
        with connection(database_url) as conn:
            password = os.environ.get(demo["password_env"], "").strip()
            if not password:
                raise SystemExit(
                    f"Missing {demo['password_env']} in the environment "
                    "(no default passwords in code)."
                )
            existing = conn.execute(
                "SELECT id FROM companies WHERE slug = %s",
                (demo["slug"],),
            ).fetchone()
            if existing:
                company_id = existing[0]
                log.info("Skip existing company %s", demo["slug"])
            else:
                company = conn.execute(
                    """
                    INSERT INTO companies (name, slug, default_locale)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (demo["name"], demo["slug"], demo["locale"]),
                ).fetchone()
                company_id = company[0]
                conn.execute(
                    """
                    INSERT INTO users (company_id, email, password_hash, role)
                    VALUES (%s, %s, %s, 'admin')
                    """,
                    (company_id, demo["email"], hash_password(password)),
                )
                conn.execute(
                    """
                    INSERT INTO widget_settings
                        (company_id, primary_color, greeting, locale, assistant_name)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        demo["primary_color"],
                        demo["greeting"],
                        demo["locale"],
                        demo["assistant_name"],
                    ),
                )
                created.append(demo["slug"])
                log.info("Created company %s (%s)", demo["name"], demo["email"])

            admin = conn.execute(
                "SELECT id FROM users WHERE company_id = %s AND role = 'admin' LIMIT 1",
                (str(company_id),),
            ).fetchone()
            admin_id = admin[0] if admin else None
        # Company transaction is committed here; the PDF gets its own.
        _seed_pdf(company_id, admin_id, demo, database_url)
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        raise SystemExit(1)
    created = seed(url)
    print(
        f"OK seed: {len(created)} company(ies)"
        + (f" ({', '.join(created)})" if created else " (all existed)")
    )
    close_pool()


if __name__ == "__main__":
    main()
