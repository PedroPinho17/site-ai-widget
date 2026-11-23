"""Optional retrieval quality with the real e5 model (skipped unless RUN_MODEL_TESTS=1)."""
from __future__ import annotations

import os
import uuid

import pytest

from tests.conftest import postgres_available

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        os.environ.get("RUN_MODEL_TESTS", "").strip() not in ("1", "true", "yes"),
        reason="Set RUN_MODEL_TESTS=1 to download the embedding model",
    ),
    pytest.mark.skipif(not postgres_available(), reason="Postgres is not reachable"),
]


def test_atlas_keyword_and_paraphrase_retrieve_sunday_hours(tmp_path, monkeypatch):
    monkeypatch.delenv("SITE_AI_WIDGET_FAKE_EMBEDDER", raising=False)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))

    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.db.migrate import migrate
    from site_ai_widget.db.pool import close_pool, connection
    from site_ai_widget.docs.embed import E5Embedder, reset_embedder, set_embedder
    from site_ai_widget.docs.process import process_document
    from site_ai_widget.docs.retrieve import hybrid_search
    from site_ai_widget.docs.storage import new_storage_name, storage_path, upload_root

    clear_settings_cache()
    reset_embedder()
    set_embedder(E5Embedder())
    migrate()
    close_pool()

    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(
        0,
        8,
        "Horario Mercearia Atlas. Domingo: 10:00-14:00. Aberto ao domingo de manha.",
    )
    pdf.add_page()
    pdf.multi_cell(0, 8, "Politica de devolucoes: 14 dias com talao.")
    raw = pdf.output()

    upload_root()
    storage_name = new_storage_name()
    path = storage_path(storage_name)
    path.write_bytes(raw)

    company_id = None
    doc_id = None
    with connection() as conn:
        co = conn.execute(
            """
            INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id
            """,
            ("Atlas Model Test", f"atlas-model-{uuid.uuid4().hex[:8]}"),
        ).fetchone()
        company_id = co[0]
        doc = conn.execute(
            """
            INSERT INTO documents (
                company_id, original_name, storage_name, size_bytes, sha256,
                page_count, status
            ) VALUES (%s, %s, %s, %s, %s, 2, 'pending')
            RETURNING id
            """,
            (
                str(company_id),
                "atlas.pdf",
                storage_name,
                path.stat().st_size,
                uuid.uuid4().hex,
            ),
        ).fetchone()
        doc_id = doc[0]

    process_document(doc_id, company_id)

    for question in ("Domingo 10:00", "estão abertos ao domingo?"):
        hits = hybrid_search(company_id, question, top_n=3)
        assert hits, f"no hits for {question!r}"
        assert any(h["page"] == 1 for h in hits), f"expected page 1 for {question!r}"

    reset_embedder()
    clear_settings_cache()
    close_pool()
