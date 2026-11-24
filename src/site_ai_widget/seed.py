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
from site_ai_widget.api.schemas import normalize_origin
from site_ai_widget.db.pool import close_pool, connection
from site_ai_widget.docs.process import process_document
from site_ai_widget.docs.storage import new_storage_name, storage_path, upload_root
from site_ai_widget.docs.validate import inspect_pdf

load_dotenv()
log = logging.getLogger("site_ai_widget")

ROOT = Path(__file__).resolve().parents[2]
DEMO_DIR = ROOT / "demo"

# Public embed id for examples/demo-site.html. Not a password: the browser ships
# it, and the API only answers when Origin is on the allowlist.
DEMO_WIDGET_SLUG = "mercearia-atlas"
DEMO_WIDGET_KEY = "wk_demo_mercearia_atlas"
DEMO_WIDGET_ORIGIN = "http://127.0.0.1:5500"

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
        "currency": "EUR",
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


def ensure_demo_widget_key(conn, company_id) -> None:
    """Allow the local demo page to call the Mercearia Atlas widget.

    Always (re)assigns ``DEMO_WIDGET_KEY`` to ``company_id`` so a previous run
    that attached it to a throwaway company cannot hide it from the Atlas
    admin. Adds ``DEMO_WIDGET_ORIGIN`` when missing and leaves every other
    allowlisted origin in place. The key stays active so the demo page works
    after a re-seed. The raw value is a public embed id, not a credential.
    """
    origin = normalize_origin(DEMO_WIDGET_ORIGIN)
    row = conn.execute(
        "SELECT company_id, allowed_origins FROM widget_keys WHERE public_key = %s",
        (DEMO_WIDGET_KEY,),
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO widget_keys (company_id, public_key, allowed_origins, is_active)
            VALUES (%s, %s, %s, true)
            """,
            (str(company_id), DEMO_WIDGET_KEY, [origin]),
        )
    else:
        origins = list(row[1] or [])
        if origin not in origins:
            origins.append(origin)
        conn.execute(
            """
            UPDATE widget_keys
            SET company_id = %s, allowed_origins = %s, is_active = true
            WHERE public_key = %s
            """,
            (str(company_id), origins, DEMO_WIDGET_KEY),
        )
        if str(row[0]) != str(company_id):
            log.info(
                "Moved demo widget key onto company %s (was on another company)",
                DEMO_WIDGET_SLUG,
            )
    log.info("Allowlisted demo widget origin for %s", DEMO_WIDGET_SLUG)


def _ensure_admin_and_settings(conn, company_id, demo: dict, password: str) -> None:
    """Create the demo admin and widget settings when the company has none."""
    has_admin = conn.execute(
        "SELECT 1 FROM users WHERE company_id = %s AND role = 'admin' LIMIT 1",
        (str(company_id),),
    ).fetchone()
    if not has_admin:
        added = conn.execute(
            """
            INSERT INTO users (company_id, email, password_hash, role)
            VALUES (%s, %s, %s, 'admin')
            ON CONFLICT (email) DO NOTHING
            RETURNING id
            """,
            (str(company_id), demo["email"], hash_password(password)),
        ).fetchone()
        if added:
            log.info("Added missing admin for %s", demo["slug"])
    conn.execute(
        """
        INSERT INTO widget_settings
            (company_id, primary_color, greeting, locale, assistant_name, currency)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id) DO UPDATE
            SET currency = COALESCE(widget_settings.currency, EXCLUDED.currency)
        """,
        (
            str(company_id),
            demo["primary_color"],
            demo["greeting"],
            demo["locale"],
            demo["assistant_name"],
            demo.get("currency"),
        ),
    )


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
                # A bare row (e.g. left by a test or an interrupted run) must
                # not lock the demo admin out: add whatever is missing.
                _ensure_admin_and_settings(conn, company_id, demo, password)
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
                        (company_id, primary_color, greeting, locale, assistant_name, currency)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        demo["primary_color"],
                        demo["greeting"],
                        demo["locale"],
                        demo["assistant_name"],
                        demo.get("currency"),
                    ),
                )
                created.append(demo["slug"])
                log.info("Created company %s (%s)", demo["name"], demo["email"])

            admin = conn.execute(
                "SELECT id FROM users WHERE company_id = %s AND role = 'admin' LIMIT 1",
                (str(company_id),),
            ).fetchone()
            admin_id = admin[0] if admin else None
            if demo["slug"] == DEMO_WIDGET_SLUG:
                ensure_demo_widget_key(conn, company_id)
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
