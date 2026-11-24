"""Pipeline integration: fake LLM + real client DB (atlas)."""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from cryptography.fernet import Fernet

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.dbconn.crypto import encrypt_dsn
from site_ai_widget.dbconn.target import ConnectionParams, build_conninfo
from site_ai_widget.docs.embed import FakeEmbedder, set_embedder
from site_ai_widget.sqlqa.pipeline import answer_with_sql
from site_ai_widget.sqlqa.retrieval import ColumnInfo, SchemaSlice, TableInfo
from tests.conftest import postgres_available

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not postgres_available(),
        reason="Postgres is not reachable (docker compose up -d db)",
    ),
]

HOST, PORT, DB = "127.0.0.1", 5436, "atlas"

ALLOWED = {
    ("public", "products"): frozenset(
        {"id", "category_id", "sku", "name", "unit", "price", "is_active"}
    ),
}

FIXED_SELECT = "SELECT name, price FROM products ORDER BY id LIMIT 5"

PRODUCTS_SLICE = SchemaSlice(
    tables=(
        TableInfo(
            schema_name="public",
            table_name="products",
            kind="table",
            description="Demo products",
            columns=(
                ColumnInfo("id", "integer", None),
                ColumnInfo("name", "text", None),
                ColumnInfo("price", "numeric", None),
                ColumnInfo("sku", "text", None),
            ),
        ),
    ),
    allowed=ALLOWED,
)


@pytest.fixture(scope="module")
def clientdb_up():
    ro_pw = os.environ.get("ATLAS_READONLY_PASSWORD")
    if not ro_pw:
        pytest.skip("ATLAS_READONLY_PASSWORD is not set")
    try:
        with psycopg.connect(
            host=HOST,
            port=PORT,
            dbname=DB,
            user="atlas_readonly",
            password=ro_pw,
            connect_timeout=1,
        ):
            pass
    except psycopg.Error:
        pytest.skip("clientdb is not running (docker compose up -d clientdb)")


@pytest.fixture(scope="module")
def migrated(clientdb_up):
    from site_ai_widget.db.migrate import migrate

    migrate()


@pytest.fixture
def company_ready(settings_env, clientdb_up, migrated):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", f"{HOST}:{PORT}")
    settings_env.setenv("CONNECTIONS_KEY", Fernet.generate_key().decode())
    settings_env.setenv("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
    clear_settings_cache()
    from site_ai_widget.db.pool import close_pool, connection

    close_pool()
    set_embedder(FakeEmbedder())

    ro_pw = os.environ["ATLAS_READONLY_PASSWORD"]
    params = ConnectionParams(HOST, PORT, DB, "atlas_readonly", ro_pw)
    token = encrypt_dsn(build_conninfo(params))
    slug = f"pipe-{uuid.uuid4().hex[:10]}"
    company_id = None
    try:
        with connection() as conn:
            (company_id,) = conn.execute(
                "INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id",
                (f"Loja {slug}", slug),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO db_connections
                    (company_id, dsn_encrypted, host, port, database_name, username, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'ok')
                """,
                (company_id, token, HOST, PORT, DB, "atlas_readonly"),
            )
        yield str(company_id)
    finally:
        if company_id is not None:
            with connection() as conn:
                conn.execute("DELETE FROM companies WHERE id = %s", (company_id,))
        set_embedder(None)
        close_pool()
        clear_settings_cache()


def _product_count() -> int:
    owner = os.environ.get("CLIENTDB_PASSWORD")
    if not owner:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    with psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user="atlas_owner", password=owner, connect_timeout=2
    ) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0])


def test_pipeline_ok_with_fake_llm_and_clientdb(company_ready, monkeypatch):
    from site_ai_widget.sqlqa import pipeline

    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: PRODUCTS_SLICE)

    def fake_llm(messages, *, temperature=0):
        return FIXED_SELECT

    out = answer_with_sql(company_ready, "quais produtos?", llm_complete=fake_llm)
    assert out.status == "ok"
    assert out.attempts == 1
    assert out.row_count >= 1
    assert "name" in out.columns
    assert out.rows
    assert out.reason is None
    assert not hasattr(out, "sql")
    assert FIXED_SELECT not in repr(out)


def test_delete_from_llm_does_not_change_product_count(company_ready, monkeypatch):
    from site_ai_widget.sqlqa import pipeline

    monkeypatch.setattr(pipeline, "retrieve_schema", lambda *a, **k: PRODUCTS_SLICE)
    before = _product_count()

    def fake_llm(messages, *, temperature=0):
        return "DELETE FROM products"

    out = answer_with_sql(company_ready, "apaga tudo", llm_complete=fake_llm)
    assert out.status == "refused"
    assert out.attempts == 2
    assert _product_count() == before
