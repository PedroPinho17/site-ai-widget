"""Post-commit embedding refresh: races and failures (needs the platform DB only)."""
from __future__ import annotations

import logging
import uuid

import psycopg
import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn import approval
from site_ai_widget.docs.embed import FakeEmbedder, set_embedder
from tests.conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres is not reachable (docker compose up -d db)",
)


@pytest.fixture(scope="module")
def migrated():
    from site_ai_widget.db.migrate import migrate

    migrate()


@pytest.fixture
def db(migrated, settings_env):
    from site_ai_widget.db.pool import close_pool

    close_pool()
    set_embedder(FakeEmbedder())
    yield
    set_embedder(None)
    close_pool()


def _company() -> str:
    from site_ai_widget.db.pool import connection

    slug = f"emb-{uuid.uuid4().hex[:10]}"
    with connection() as conn:
        (company_id,) = conn.execute(
            "INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id",
            (f"Loja Exemplo {slug}", slug),
        ).fetchone()
    return str(company_id)


def _approved_table(company_id: str, name: str = "products") -> str:
    """An approved table with two approved columns, and no embedding yet."""
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        (table_id,) = conn.execute(
            "INSERT INTO schema_tables (company_id, schema_name, table_name, kind, status) "
            "VALUES (%s, 'public', %s, 'table', 'approved') RETURNING id",
            (company_id, name),
        ).fetchone()
        for ordinal, column in enumerate(("sku", "price"), start=1):
            conn.execute(
                "INSERT INTO schema_columns "
                "(company_id, table_id, column_name, data_type, is_nullable, ordinal, status) "
                "VALUES (%s, %s, %s, 'text', false, %s, 'approved')",
                (company_id, table_id, column, ordinal),
            )
    return str(table_id)


def _embedding(table_id: str) -> str | None:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        return conn.execute(
            "SELECT embedding::text FROM schema_tables WHERE id = %s", (table_id,)
        ).fetchone()[0]


def _literal(text: str) -> str:
    return approval._vector_literal(FakeEmbedder().embed_passages([text])[0])


class HookEmbedder(FakeEmbedder):
    """Runs `hook` while "the model is busy": the moment a parallel PATCH could land."""

    def __init__(self, hook):
        self._hook = hook

    def embed_passages(self, texts):
        self._hook()
        return super().embed_passages(texts)


class FailingEmbedder:
    def __init__(self, fail_on: str | None = None):
        self.fail_on = fail_on

    def embed_passages(self, texts):
        if self.fail_on is None or self.fail_on in texts[0]:
            raise RuntimeError(f"model crashed on: {texts[0]}")
        return FakeEmbedder().embed_passages(texts)

    def embed_query(self, text):
        return FakeEmbedder().embed_query(text)


def test_unchanged_table_gets_its_embedding(db):
    company_id = _company()
    table_id = _approved_table(company_id)
    assert approval.refresh_table_embedding_safely(company_id, table_id) == "written"
    assert _embedding(table_id) is not None


def test_table_excluded_while_computing_never_gets_an_embedding(db):
    from site_ai_widget.db.pool import connection

    company_id = _company()
    table_id = _approved_table(company_id)

    def exclude_meanwhile():
        with connection() as conn:  # a parallel PATCH: exclude + clear its embedding
            conn.execute(
                "UPDATE schema_tables SET status = 'excluded', embedding = NULL WHERE id = %s",
                (table_id,),
            )

    set_embedder(HookEmbedder(exclude_meanwhile))
    assert approval.refresh_table_embedding_safely(company_id, table_id) == "cleared"
    assert _embedding(table_id) is None


def test_table_changed_while_computing_keeps_the_newer_embedding(db):
    from site_ai_widget.db.pool import connection

    company_id = _company()
    table_id = _approved_table(company_id)
    newer = _literal("Table public.products\nNewer description.\nColumns: sku, price")

    def patch_meanwhile():
        with connection() as conn:  # a parallel PATCH wrote its own, newer embedding
            conn.execute(
                "UPDATE schema_tables SET description = 'Newer description.', embedding = %s::vector "
                "WHERE id = %s",
                (newer, table_id),
            )

    set_embedder(HookEmbedder(patch_meanwhile))
    assert approval.refresh_table_embedding_safely(company_id, table_id) == "skipped"
    assert _embedding(table_id) == _as_pg_text(newer)


def _as_pg_text(literal: str) -> str:
    """How Postgres prints this vector (to compare with embedding::text)."""
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        return conn.execute("SELECT %s::vector::text", (literal,)).fetchone()[0]


def test_embedder_failure_fails_closed_and_logs_only_the_class(db, caplog):
    company_id = _company()
    table_id = _approved_table(company_id, name="secret_ledger")
    assert approval.refresh_table_embedding_safely(company_id, table_id) == "written"
    assert _embedding(table_id) is not None

    set_embedder(FailingEmbedder())
    with caplog.at_level(logging.WARNING, logger="site_ai_widget"):
        assert approval.refresh_table_embedding_safely(company_id, table_id) == "failed"
    assert _embedding(table_id) is None  # a stale vector is worse than none
    assert "RuntimeError" in caplog.text
    assert "secret_ledger" not in caplog.text  # never the embedded text


def test_after_commit_refresh_continues_past_a_failure(db):
    company_id = _company()
    bad = _approved_table(company_id, name="aaa_broken")
    good = _approved_table(company_id, name="zzz_fine")

    set_embedder(FailingEmbedder(fail_on="aaa_broken"))
    outcomes = approval.refresh_embeddings_after_commit(company_id, {good, bad})
    assert outcomes == {bad: "failed", good: "written"}
    assert list(outcomes) == sorted([bad, good])  # sorted order, one transaction each
    assert _embedding(bad) is None
    assert _embedding(good) is not None


def test_after_commit_conflict_is_retry_but_others_still_refresh(db, monkeypatch):
    company_id = _company()
    first = _approved_table(company_id, name="t_first")
    second = _approved_table(company_id, name="t_second")
    a, b = sorted([first, second])
    real = approval.refresh_table_embedding_safely

    def deadlock_on_a(cid, tid):
        if tid == a:
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        return real(cid, tid)

    monkeypatch.setattr(approval, "refresh_table_embedding_safely", deadlock_on_a)
    with pytest.raises(ApiError) as exc:
        approval.refresh_embeddings_after_commit(company_id, [first, second])
    assert (exc.value.code, exc.value.status) == ("db_conflict_retry", 409)
    assert _embedding(b) is not None  # the other table was still handled


class HookThenFailEmbedder:
    """A parallel PATCH lands while the model runs, then the model fails."""

    def __init__(self, hook):
        self._hook = hook

    def embed_passages(self, texts):
        self._hook()
        raise RuntimeError("model crashed")

    def embed_query(self, text):
        raise RuntimeError("model crashed")


def test_failure_keeps_a_newer_embedding_written_meanwhile(db):
    from site_ai_widget.db.pool import connection

    company_id = _company()
    table_id = _approved_table(company_id)
    newer = _literal("Table public.products\nNewer description.\nColumns: sku, price")

    def patch_meanwhile():
        with connection() as conn:
            conn.execute(
                "UPDATE schema_tables SET description = 'Newer description.', embedding = %s::vector "
                "WHERE id = %s",
                (newer, table_id),
            )

    set_embedder(HookThenFailEmbedder(patch_meanwhile))
    assert approval.refresh_table_embedding_safely(company_id, table_id) == "skipped"
    assert _embedding(table_id) == _as_pg_text(newer)  # not wiped by our failure


def test_failure_on_a_table_excluded_meanwhile_leaves_no_embedding(db):
    from site_ai_widget.db.pool import connection

    company_id = _company()
    table_id = _approved_table(company_id)

    def exclude_meanwhile():
        with connection() as conn:
            conn.execute("UPDATE schema_tables SET status = 'excluded' WHERE id = %s", (table_id,))

    set_embedder(HookThenFailEmbedder(exclude_meanwhile))
    assert approval.refresh_table_embedding_safely(company_id, table_id) == "cleared"
    assert _embedding(table_id) is None
