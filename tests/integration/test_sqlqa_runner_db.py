"""SQL runner against the demo client DB (docker compose up -d db + clientdb)."""
from __future__ import annotations

import contextlib
import os
import uuid
from decimal import Decimal

import psycopg
import pytest
from cryptography.fernet import Fernet

from site_ai_widget.api.config import clear_settings_cache
from site_ai_widget.dbconn.crypto import encrypt_dsn
from site_ai_widget.dbconn.target import ConnectionParams, build_conninfo
from site_ai_widget.sqlqa import runner
from site_ai_widget.sqlqa.runner import RunnerError, run_sql
from site_ai_widget.sqlqa.validator import validate_sql
from tests.conftest import postgres_available

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not postgres_available(),
        reason="Postgres is not reachable (docker compose up -d db)",
    ),
]

HOST, PORT = "127.0.0.1", 5436
DB_A, DB_B = "atlas", "atlas_b"
MARKER_A = "Alpha-Company-A-Widget"
MARKER_B = "Beta-Company-B-Gadget"

PRODUCTS_ALLOWED = {
    ("public", "products"): frozenset(
        {"id", "category_id", "sku", "name", "unit", "price", "is_active"}
    ),
}

_B_SCHEMA = f"""
CREATE TABLE categories (
    id   serial PRIMARY KEY,
    name text NOT NULL UNIQUE
);
CREATE TABLE products (
    id          serial PRIMARY KEY,
    category_id integer NOT NULL REFERENCES categories(id),
    sku         text NOT NULL UNIQUE,
    name        text NOT NULL,
    unit        text NOT NULL,
    price       numeric(10,2) NOT NULL,
    is_active   boolean NOT NULL DEFAULT true
);
INSERT INTO categories (name) VALUES ('Demo');
INSERT INTO products (category_id, sku, name, unit, price) VALUES
    (1, 'B-0001', '{MARKER_B}', 'un', 1.23);
"""


def _ro_password() -> str:
    pw = os.environ.get("ATLAS_READONLY_PASSWORD")
    if not pw:
        pytest.skip("ATLAS_READONLY_PASSWORD is not set")
    return pw


def _owner_password() -> str:
    pw = os.environ.get("CLIENTDB_PASSWORD")
    if not pw:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    return pw


@pytest.fixture(scope="module")
def clientdb_up():
    ro_pw = os.environ.get("ATLAS_READONLY_PASSWORD")
    if not ro_pw:
        pytest.skip("ATLAS_READONLY_PASSWORD is not set")
    try:
        with psycopg.connect(
            host=HOST, port=PORT, dbname=DB_A, user="atlas_readonly", password=ro_pw, connect_timeout=1
        ):
            pass
    except psycopg.Error:
        pytest.skip("clientdb is not running (docker compose up -d clientdb)")


@pytest.fixture(scope="module")
def migrated(clientdb_up):
    from site_ai_widget.db.migrate import migrate

    migrate()


@pytest.fixture
def allow_demo(settings_env, clientdb_up, migrated):
    settings_env.setenv("CLIENTDB_ALLOWED_HOSTS", f"{HOST}:{PORT}")
    settings_env.setenv("CONNECTIONS_KEY", Fernet.generate_key().decode())
    clear_settings_cache()
    from site_ai_widget.db.pool import close_pool

    close_pool()
    yield settings_env
    close_pool()
    clear_settings_cache()


def _params(database: str) -> ConnectionParams:
    return ConnectionParams(HOST, PORT, database, "atlas_readonly", _ro_password())


def _owner_connect(dbname: str) -> psycopg.Connection:
    return psycopg.connect(
        host=HOST, port=PORT, dbname=dbname, user="atlas_owner", password=_owner_password(), autocommit=True
    )


@pytest.fixture(scope="module")
def atlas_b_db(clientdb_up):
    """Second fictional client DB; dropped afterwards only if this fixture created it."""
    with _owner_connect(DB_A) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_B,)).fetchone()
        created = not exists
        if created:
            conn.execute(f"CREATE DATABASE {DB_B} OWNER atlas_owner")
        conn.execute(f"GRANT CONNECT ON DATABASE {DB_B} TO atlas_readonly")

    with _owner_connect(DB_B) as conn:
        conn.execute("DROP TABLE IF EXISTS products CASCADE")
        conn.execute("DROP TABLE IF EXISTS categories CASCADE")
        for stmt in [s.strip() for s in _B_SCHEMA.split(";") if s.strip()]:
            conn.execute(stmt)
        conn.execute("GRANT USAGE ON SCHEMA public TO atlas_readonly")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlas_readonly")

    try:
        yield DB_B
    finally:
        if created:
            with _owner_connect(DB_A) as conn:
                conn.execute(f"DROP DATABASE IF EXISTS {DB_B} WITH (FORCE)")


@pytest.fixture(scope="module")
def atlas_a_marker(clientdb_up):
    """Unique fictional product on company A's DB; removed afterwards."""
    with _owner_connect(DB_A) as conn:
        conn.execute("DELETE FROM products WHERE sku = 'A-RUN-1'")
        cat = conn.execute("SELECT id FROM categories ORDER BY id LIMIT 1").fetchone()
        if not cat:
            pytest.skip("demo atlas schema missing categories")
        conn.execute(
            """
            INSERT INTO products (category_id, sku, name, unit, price)
            VALUES (%s, 'A-RUN-1', %s, 'un', 9.99)
            """,
            (cat[0], MARKER_A),
        )
    try:
        yield MARKER_A
    finally:
        with _owner_connect(DB_A) as conn:
            conn.execute("DELETE FROM products WHERE sku = 'A-RUN-1'")


def _company_with_connection(database: str) -> str:
    from site_ai_widget.db.pool import connection

    slug = f"run-{uuid.uuid4().hex[:10]}"
    params = _params(database)
    token = encrypt_dsn(build_conninfo(params))
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
            (company_id, token, params.host, params.port, params.database, params.username),
        )
    return str(company_id)


@pytest.fixture
def two_companies(allow_demo, atlas_b_db, atlas_a_marker):
    from site_ai_widget.db.pool import connection

    created: list[str] = []
    try:
        created.append(_company_with_connection(DB_A))
        created.append(_company_with_connection(DB_B))
        yield created[0], created[1]
    finally:
        if created:
            # db_connections rows go with the companies (ON DELETE CASCADE).
            with connection() as conn:
                conn.execute("DELETE FROM companies WHERE id = ANY(%s::uuid[])", (created,))


def test_select_products_returns_json_safe_decimals(allow_demo, two_companies):
    company_a, _ = two_companies
    sql = validate_sql("SELECT name, price FROM products ORDER BY id LIMIT 2", PRODUCTS_ALLOWED)
    result = run_sql(sql, company_id=company_a, allowed=PRODUCTS_ALLOWED, max_rows=10)
    assert result.row_count >= 1
    assert result.truncated is False
    for row in result.rows:
        assert isinstance(row[1], str)
        Decimal(row[1])


def test_truncation_on_real_rows(allow_demo, two_companies):
    company_a, _ = two_companies
    sql = validate_sql("SELECT id, name FROM products ORDER BY id", PRODUCTS_ALLOWED)
    result = run_sql(sql, company_id=company_a, allowed=PRODUCTS_ALLOWED, max_rows=2)
    assert result.truncated is True
    assert result.row_count == 2


def test_write_rejected_before_db(allow_demo, two_companies):
    company_a, _ = two_companies
    with pytest.raises(RunnerError) as exc:
        run_sql(
            "UPDATE products SET price = 0 WHERE id = 1",
            company_id=company_a,
            allowed=PRODUCTS_ALLOWED,
        )
    assert exc.value.reason == "not_validated"


def test_runner_transaction_is_read_only(allow_demo, two_companies, monkeypatch):
    """Asks Postgres directly, so it fails if the READ ONLY setting is ever dropped."""
    company_a, _ = two_companies
    monkeypatch.setattr(runner, "validate_sql", lambda sql, allowed: sql)
    result = run_sql(
        "SELECT current_setting('transaction_read_only') AS ro",
        company_id=company_a,
        allowed=PRODUCTS_ALLOWED,
    )
    assert result.columns == ["ro"]
    assert result.rows == [["on"]]


def test_company_isolation_unfiltered_select_stays_on_own_db(allow_demo, two_companies):
    company_a, company_b = two_companies
    sql = validate_sql("SELECT name FROM products", PRODUCTS_ALLOWED)

    result_a = run_sql(sql, company_id=company_a, allowed=PRODUCTS_ALLOWED)
    names_a = {row[0] for row in result_a.rows}
    assert MARKER_A in names_a
    assert MARKER_B not in names_a

    result_b = run_sql(sql, company_id=company_b, allowed=PRODUCTS_ALLOWED)
    names_b = {row[0] for row in result_b.rows}
    assert MARKER_B in names_b
    assert MARKER_A not in names_b


def test_company_b_never_reaches_company_a_db(allow_demo, two_companies, monkeypatch):
    company_a, company_b = two_companies
    seen: list[tuple[str, str]] = []
    real_session = runner.client_session

    @contextlib.contextmanager
    def spy(params, *, company_id=None):
        seen.append((str(company_id), params.database))
        with real_session(params, company_id=company_id) as conn:
            yield conn

    monkeypatch.setattr(runner, "client_session", spy)
    sql = validate_sql("SELECT name FROM products", PRODUCTS_ALLOWED)
    run_sql(sql, company_id=company_b, allowed=PRODUCTS_ALLOWED)
    assert seen == [(company_b, DB_B)]
    assert (company_b, DB_A) not in seen
    assert (company_a, DB_A) not in seen
