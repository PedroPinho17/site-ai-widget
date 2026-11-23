"""Introspection against the demo client DB (docker compose up -d clientdb)."""
import os

import psycopg
import pytest

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.api.errors import ApiError
from site_ai_widget.dbconn import introspect
from site_ai_widget.dbconn.introspect import read_schema
from site_ai_widget.dbconn.target import ConnectionParams

pytestmark = pytest.mark.integration

HOST, PORT, DB = "127.0.0.1", 5436, "atlas"
DEMO_TABLES = {"categories", "customers", "opening_hours", "products", "stock", "stores"}

# Objects created by the owner only for this test, then dropped.
_EXTRA_SQL = """
CREATE TABLE zz_intro_events (id int, happened date) PARTITION BY RANGE (happened);
CREATE TABLE zz_intro_events_2026 PARTITION OF zz_intro_events
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE VIEW zz_intro_cheap AS SELECT sku, price FROM products WHERE price < 2;
CREATE SEQUENCE zz_intro_seq;
CREATE TABLE zz_intro_secret (id int, token text);
REVOKE SELECT ON zz_intro_secret FROM atlas_readonly;
CREATE TABLE zz_intro_people (id int, name text, nif text);
REVOKE SELECT ON zz_intro_people FROM atlas_readonly;
GRANT SELECT (id, name) ON zz_intro_people TO atlas_readonly;
"""
_DROP_SQL = """
DROP VIEW IF EXISTS zz_intro_cheap;
DROP TABLE IF EXISTS zz_intro_events, zz_intro_secret, zz_intro_people;
DROP SEQUENCE IF EXISTS zz_intro_seq;
"""


def readonly_params() -> ConnectionParams:
    return ConnectionParams(HOST, PORT, DB, "atlas_readonly", os.environ.get("ATLAS_READONLY_PASSWORD", ""))


@pytest.fixture(scope="module")
def clientdb_up():
    ro_pw = os.environ.get("ATLAS_READONLY_PASSWORD")
    if not ro_pw:
        pytest.skip("ATLAS_READONLY_PASSWORD is not set")
    try:
        with psycopg.connect(
            host=HOST, port=PORT, dbname=DB, user="atlas_readonly", password=ro_pw, connect_timeout=1
        ):
            pass
    except psycopg.Error:
        pytest.skip("clientdb is not running (docker compose up -d clientdb)")


@pytest.fixture
def allow_demo(settings_env, clientdb_up):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", f"{HOST}:{PORT}")
    clear_settings_cache()
    return settings_env


@pytest.fixture
def extra_objects(clientdb_up):
    owner_pw = os.environ.get("CLIENTDB_PASSWORD")
    if not owner_pw:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    with psycopg.connect(
        host=HOST, port=PORT, dbname=DB, user="atlas_owner", password=owner_pw, autocommit=True
    ) as conn:
        conn.execute(_DROP_SQL)
        conn.execute(_EXTRA_SQL)
        try:
            yield
        finally:
            conn.execute(_DROP_SQL)


def test_reads_the_demo_tables(allow_demo):
    tables = {t.name: t for t in read_schema(readonly_params())}
    assert set(tables) >= DEMO_TABLES
    products = tables["products"]
    assert products.kind == "table"
    assert [c.name for c in products.columns] == ["id", "category_id", "sku", "name", "unit", "price", "is_active"]
    price = next(c for c in products.columns if c.name == "price")
    assert price.data_type == "numeric(10,2)"
    assert price.is_nullable is False


def test_only_tables_views_and_readable_columns(allow_demo, extra_objects):
    tables = {t.name: t for t in read_schema(readonly_params())}
    assert tables["zz_intro_events"].kind == "table"  # partitioned parent
    assert "zz_intro_events_2026" not in tables  # partition child (L6)
    assert tables["zz_intro_cheap"].kind == "view"
    assert "zz_intro_seq" not in tables  # sequences are not data
    assert "zz_intro_secret" not in tables  # no SELECT at all
    assert [c.name for c in tables["zz_intro_people"].columns] == ["id", "name"]  # nif not granted


def test_too_many_columns_stops_and_the_next_read_works(allow_demo, monkeypatch):
    real_limit = introspect.MAX_COLUMNS
    monkeypatch.setattr(introspect, "MAX_COLUMNS", 5)
    with pytest.raises(ApiError) as exc:
        read_schema(readonly_params())
    assert exc.value.code == "db_schema_too_large"

    # The session was closed and its slot given back: a normal read still works.
    monkeypatch.setattr(introspect, "MAX_COLUMNS", real_limit)
    assert {t.name for t in read_schema(readonly_params())} >= DEMO_TABLES
