"""Seeding demo PDFs must finish processing in the seed process itself.

Regression: the seed used to enqueue the PDF on a background thread while its
INSERT was still uncommitted, so with the API already running the document
could stay ``pending`` until the next API restart.
"""
from __future__ import annotations

import uuid

import pytest

from tests.conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres is not reachable (docker compose up -d db)",
)


@pytest.fixture
def seed_env(tmp_path, monkeypatch):
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.db.migrate import migrate
    from site_ai_widget.db.pool import close_pool
    from site_ai_widget.docs.embed import FakeEmbedder, set_embedder

    monkeypatch.setenv("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("DEMO_PASSWORD_SEEDTEST", "seed-test-pass-12")
    clear_settings_cache()
    close_pool()
    set_embedder(FakeEmbedder())
    migrate()
    yield
    close_pool()
    set_embedder(None)
    clear_settings_cache()


def _demo() -> dict:
    from site_ai_widget.seed import DEMOS

    slug = f"seedtest-{uuid.uuid4().hex[:8]}"
    return {
        **DEMOS[0],
        "name": f"Seed Test {slug}",
        "slug": slug,
        "email": f"admin@{slug}.example",
        "password_env": "DEMO_PASSWORD_SEEDTEST",
    }


def _doc_for(slug: str) -> tuple:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        return conn.execute(
            """
            SELECT d.id, d.status, d.company_id,
                   (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id)
            FROM documents d JOIN companies co ON co.id = d.company_id
            WHERE co.slug = %s
            """,
            (slug,),
        ).fetchone()


def test_seeded_pdf_is_ready_when_seed_returns(seed_env):
    from site_ai_widget.docs import worker
    from site_ai_widget.seed import seed

    demo = _demo()
    assert seed(demos=(demo,)) == [demo["slug"]]

    # Same process, no API, no restart, no background worker involved
    doc_id, status, _, chunks = _doc_for(demo["slug"])
    assert status == "ready"
    assert chunks >= 1
    assert worker._executor is None


def test_reseed_finishes_a_stuck_pending_pdf(seed_env):
    from site_ai_widget.db.pool import connection
    from site_ai_widget.seed import seed

    demo = _demo()
    seed(demos=(demo,))
    doc_id, _, _, _ = _doc_for(demo["slug"])
    with connection() as conn:  # simulate a seed killed mid-way (old behaviour)
        conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        conn.execute("UPDATE documents SET status = 'pending' WHERE id = %s", (doc_id,))

    assert seed(demos=(demo,)) == []  # company exists; PDF not duplicated
    again_id, status, _, chunks = _doc_for(demo["slug"])
    assert again_id == doc_id
    assert status == "ready"
    assert chunks >= 1


def test_reseed_recovers_stuck_processing_past_threshold(seed_env):
    """Crash mid-processing leaves status=processing; seed resets if old enough."""
    from site_ai_widget.db.pool import connection
    from site_ai_widget.seed import seed

    demo = _demo()
    seed(demos=(demo,))
    doc_id, _, _, _ = _doc_for(demo["slug"])
    with connection() as conn:
        conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        conn.execute(
            """
            UPDATE documents
            SET status = 'processing',
                updated_at = now() - interval '10 minutes'
            WHERE id = %s
            """,
            (doc_id,),
        )

    assert seed(demos=(demo,)) == []
    again_id, status, _, chunks = _doc_for(demo["slug"])
    assert again_id == doc_id
    assert status == "ready"
    assert chunks >= 1


def test_reseed_leaves_fresh_processing_alone(seed_env):
    """A recently updated processing row is treated as an active worker job."""
    from site_ai_widget.db.pool import connection
    from site_ai_widget.seed import seed

    demo = _demo()
    seed(demos=(demo,))
    doc_id, _, company_id, chunks_before = _doc_for(demo["slug"])
    with connection() as conn:
        conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        conn.execute(
            """
            UPDATE documents
            SET status = 'processing', updated_at = now()
            WHERE id = %s
            """,
            (doc_id,),
        )

    assert seed(demos=(demo,)) == []
    again_id, status, again_co, chunks = _doc_for(demo["slug"])
    assert again_id == doc_id
    assert again_co == company_id
    assert status == "processing"
    assert chunks == 0
    assert chunks_before >= 1  # was ready before we simulated an active job
