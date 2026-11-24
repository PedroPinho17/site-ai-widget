"""HTTP routes for the company client DB connection (needs db + clientdb)."""
from __future__ import annotations

import json
import os
import time
import uuid

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from site_ai_widget.api.config import clear_settings_cache
from tests.conftest import postgres_available

URL = "/db-connection"
HOST, PORT, DB = "127.0.0.1", 5436, "atlas"


def _clientdb_available() -> bool:
    ro_pw = os.environ.get("ATLAS_READONLY_PASSWORD")
    if not ro_pw:
        return False
    try:
        with psycopg.connect(
            host=HOST,
            port=PORT,
            dbname=DB,
            user="atlas_readonly",
            password=ro_pw,
            connect_timeout=1,
        ):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not postgres_available(),
        reason="Postgres is not reachable (docker compose up -d db)",
    ),
    pytest.mark.skipif(
        not _clientdb_available(),
        reason="clientdb is not running (docker compose up -d clientdb)",
    ),
]


def body(**overrides) -> dict:
    data = {
        "host": HOST,
        "port": PORT,
        "database": DB,
        "username": "atlas_readonly",
        "password": os.environ["ATLAS_READONLY_PASSWORD"],
    }
    data.update(overrides)
    return data


def register(client: TestClient) -> tuple[dict, str]:
    slug = f"co-{uuid.uuid4().hex[:10]}"
    r = client.post(
        "/auth/register",
        json={
            "company_name": f"Company {slug}",
            "company_slug": slug,
            "email": f"{slug}@example.com",
            "password": "test-pass-12",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    return headers, data["user"]["company_id"]


def stored_dsn(company_id: str) -> str | None:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        row = conn.execute(
            "SELECT dsn_encrypted FROM db_connections WHERE company_id = %s",
            (company_id,),
        ).fetchone()
    return row[0] if row else None


def add_schema_table(company_id: str) -> None:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        conn.execute(
            """
            INSERT INTO schema_tables (company_id, schema_name, table_name, kind, status)
            VALUES (%s, 'public', 'products', 'table', 'approved')
            """,
            (company_id,),
        )


def count_schema_tables(company_id: str) -> int:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM schema_tables WHERE company_id = %s",
                (company_id,),
            ).fetchone()[0]
        )


@pytest.fixture(scope="module")
def migrated():
    from site_ai_widget.db.migrate import migrate

    migrate()


@pytest.fixture
def client(migrated, tmp_path, monkeypatch):
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.pool import close_pool
    from site_ai_widget.docs.embed import FakeEmbedder, set_embedder

    monkeypatch.setenv("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CONNECTIONS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("CLIENTDB_ALLOWED_HOSTS", f"{HOST}:{PORT}")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    clear_settings_cache()
    reset_limiters()
    close_pool()
    set_embedder(FakeEmbedder())

    app = create_app()
    with TestClient(app) as c:
        yield c

    close_pool()
    reset_limiters()
    set_embedder(None)
    clear_settings_cache()


def test_save_get_test_round_trip(client):
    headers, company_id = register(client)
    saved = client.put(URL, json=body(), headers=headers)
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["status"] == "ok"
    assert data["host"] == HOST
    assert data["username"] == "atlas_readonly"
    assert data["readable_tables"] == 6
    assert "password" not in data
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in saved.text
    dsn = stored_dsn(company_id)
    assert dsn is not None
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in dsn

    got = client.get(URL, headers=headers)
    assert got.status_code == 200
    assert got.json()["database"] == DB
    assert "password" not in got.json()

    tested = client.post(f"{URL}/test", headers=headers)
    assert tested.status_code == 200
    assert tested.json()["status"] == "ok"
    assert tested.json()["readable_tables"] == 6

    assert client.delete(URL, headers=headers).status_code == 204
    assert client.get(URL, headers=headers).status_code == 404


def test_test_marks_failed_when_host_blocked(client, monkeypatch):
    headers, _ = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    monkeypatch.setenv("CLIENTDB_ALLOWED_HOSTS", "")
    clear_settings_cache()
    failed = client.post(f"{URL}/test", headers=headers)
    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    assert failed.json()["last_error"] == "db_host_not_allowed"
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in failed.text


def test_other_company_cannot_see_or_delete(client):
    headers_a, company_a = register(client)
    headers_b, _ = register(client)
    assert client.put(URL, json=body(), headers=headers_a).status_code == 200
    assert client.get(URL, headers=headers_b).status_code == 404
    assert client.delete(URL, headers=headers_b).status_code == 204
    assert stored_dsn(company_a) is not None  # B's delete only touched B


def test_same_database_keeps_schema_and_delete_clears_it(client):
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    add_schema_table(company_id)
    # Saving the same database again (e.g. a new password) keeps the approved schema.
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    assert count_schema_tables(company_id) == 1
    assert client.delete(URL, headers=headers).status_code == 204
    assert count_schema_tables(company_id) == 0


def test_missing_key_is_503_and_saves_nothing(client, monkeypatch):
    headers, company_id = register(client)
    monkeypatch.setenv("CONNECTIONS_KEY", "")
    clear_settings_cache()
    r = client.put(URL, json=body(), headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "connections_not_configured"
    assert stored_dsn(company_id) is None


def test_control_characters_are_rejected(client):
    headers, _ = register(client)
    r = client.put(URL, json=body(database="atlas\x00"), headers=headers)
    assert r.status_code == 422


def test_failed_test_saves_nothing(client):
    headers, company_id = register(client)
    r = client.put(URL, json=body(password="wrong-secretpart"), headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "db_auth_failed"
    assert "secretpart" not in r.text
    assert stored_dsn(company_id) is None


def test_widget_database_is_refused(client):
    headers, _ = register(client)
    r = client.put(URL, json=body(port=5435, username="sitewidget"), headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "db_host_not_allowed"


def test_different_database_clears_old_schema(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    add_schema_table(company_id)
    # Pretend the saved connection pointed to another database before.
    with connection() as conn:
        conn.execute(
            "UPDATE db_connections SET database_name = 'old_db', last_introspected_at = now() "
            "WHERE company_id = %s",
            (company_id,),
        )
    saved = client.put(URL, json=body(), headers=headers)
    assert saved.status_code == 200
    assert saved.json()["last_introspected_at"] is None
    assert count_schema_tables(company_id) == 0


def test_probe_is_rate_limited(client):
    headers, _ = register(client)
    for _ in range(10):
        # Refused before any network call, but it still counts towards the limit.
        r = client.put(URL, json=body(port=5435), headers=headers)
        assert r.status_code == 422
    assert client.put(URL, json=body(port=5435), headers=headers).status_code == 429


def test_probe_rate_limit_is_also_per_ip(client):
    headers_a, _ = register(client)
    headers_b, _ = register(client)
    for _ in range(10):
        assert client.put(URL, json=body(port=5435), headers=headers_a).status_code == 422
    # Same client IP already spent the probe budget; company B is blocked too.
    assert client.put(URL, json=body(port=5435), headers=headers_b).status_code == 429


def test_introspect_has_its_own_limit_separate_from_probe(client):
    headers, _ = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    # Exhaust the probe bucket with failing saves.
    for _ in range(9):
        assert client.put(URL, json=body(port=5435), headers=headers).status_code == 422
    assert client.put(URL, json=body(port=5435), headers=headers).status_code == 429
    # Introspect still works: it does not share the probe budget.
    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.status_code == 200, r.text


def test_resave_without_password_keeps_stored_password(client):
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    before = stored_dsn(company_id)
    assert before is not None

    # Omit password entirely (panel re-save). Must keep the old secret and succeed.
    payload = {
        "host": HOST,
        "port": PORT,
        "database": DB,
        "username": "atlas_readonly",
        "sslmode": "prefer",
    }
    r = client.put(URL, json=payload, headers=headers)
    assert r.status_code == 200, r.text
    assert "password" not in r.json()
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in r.text
    # DSN ciphertext may rotate (new Fernet token) but decrypts to the same password.
    from site_ai_widget.dbconn.crypto import decrypt_dsn
    from site_ai_widget.dbconn.target import params_from_conninfo

    after = stored_dsn(company_id)
    assert after is not None
    kept = params_from_conninfo(decrypt_dsn(after))
    assert kept.password == os.environ["ATLAS_READONLY_PASSWORD"]
    assert kept.host == HOST

    # Empty string is treated the same as omit.
    r = client.put(URL, json={**payload, "password": ""}, headers=headers)
    assert r.status_code == 200, r.text
    assert "password" not in r.json()


def test_first_save_without_password_is_422(client):
    headers, _ = register(client)
    r = client.put(
        URL,
        json={
            "host": HOST,
            "port": PORT,
            "database": DB,
            "username": "atlas_readonly",
            "sslmode": "prefer",
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "db_password_required"


@pytest.mark.parametrize(
    "change",
    [
        {"host": "localhost"},
        {"port": 5435},
        {"database": "other_db"},
        {"username": "someone_else"},
        {"sslmode": "disable"},
        {"sslmode": "allow"},
    ],
    ids=["host", "port", "database", "username", "sslmode-disable", "sslmode-allow"],
)
def test_resave_without_password_refuses_a_new_target(client, change):
    """The stored password is never sent to another server or over weaker TLS."""
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    before = stored_dsn(company_id)
    payload = {k: v for k, v in body(**change).items() if k != "password"}
    r = client.put(URL, json=payload, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "db_password_required"
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in r.text
    assert stored_dsn(company_id) == before


def test_resave_without_password_may_strengthen_sslmode_and_echoes_it(client):
    headers, company_id = register(client)
    assert client.put(URL, json=body(sslmode="disable"), headers=headers).status_code == 200
    got = client.get(URL, headers=headers).json()
    assert got["sslmode"] == "disable"
    payload = {k: v for k, v in body(sslmode="prefer").items() if k != "password"}
    r = client.put(URL, json=payload, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["sslmode"] == "prefer"
    assert "password" not in r.json()
    from site_ai_widget.dbconn.crypto import decrypt_dsn
    from site_ai_widget.dbconn.target import params_from_conninfo

    assert params_from_conninfo(decrypt_dsn(stored_dsn(company_id))).sslmode == "prefer"


def test_password_required_does_not_spend_the_probe_budget(client):
    headers, _ = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    payload = {k: v for k, v in body(host="localhost").items() if k != "password"}
    for _ in range(15):
        assert client.put(URL, json=payload, headers=headers).status_code == 422
    assert client.put(URL, json=body(), headers=headers).status_code == 200


def test_introspect_limit_is_per_company(client):
    from site_ai_widget.api.routes import db_connection

    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    for _ in range(30):
        assert client.post(f"{URL}/introspect", headers=headers).status_code == 200
    blocked = client.post(f"{URL}/introspect", headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in blocked.headers
    # The bucket is keyed by company id (plus IP), never shared across companies by name.
    assert any(company_id in key for key in db_connection._introspect_limiter._calls)


def test_introspect_saves_schema_and_is_idempotent(client):
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200

    first = client.post(f"{URL}/introspect", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"tables": 6, "columns": 29, "removed_tables": 0, "needs_review": 0}
    assert count_schema_tables(company_id) == 6

    second = client.post(f"{URL}/introspect", headers=headers)
    assert second.json() == first.json()
    assert count_schema_tables(company_id) == 6  # no duplicates
    assert client.get(URL, headers=headers).json()["last_introspected_at"] is not None


def test_introspect_marks_missing_table_and_unapproves_it(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    with connection() as conn:
        conn.execute(
            "INSERT INTO schema_tables (company_id, schema_name, table_name, kind, status) "
            "VALUES (%s, 'public', 'ghost', 'table', 'approved')",
            (company_id,),
        )
    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["removed_tables"] == 1
    with connection() as conn:
        row = conn.execute(
            "SELECT is_present, status FROM schema_tables WHERE company_id = %s AND table_name = 'ghost'",
            (company_id,),
        ).fetchone()
    assert row == (False, "pending")


def test_introspect_without_connection_is_404(client):
    headers, _ = register(client)
    assert client.post(f"{URL}/introspect", headers=headers).status_code == 404


def fake_llm(calls: list):
    """Pretends to be the LLM: every table is useful except customers."""

    def complete(messages, *, temperature=0):
        calls.append(len(messages))
        tables = json.loads(messages[1]["content"])["tables"]
        return json.dumps(
            {
                "tables": [
                    {
                        "ref": t["ref"],
                        "useful": not t["table"].endswith(".customers"),
                        "description": f"Rows of {t['table']}.",
                    }
                    for t in tables
                ]
            }
        )

    return complete


def schema_rows(company_id: str) -> dict:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        rows = conn.execute(
            "SELECT table_name, status, ai_suggested, description FROM schema_tables WHERE company_id = %s",
            (company_id,),
        ).fetchall()
    return {row[0]: tuple(row[1:]) for row in rows}


def test_suggest_in_batches_and_never_approves(client, monkeypatch):
    calls: list = []
    monkeypatch.setattr("site_ai_widget.dbconn.suggest.complete_chat", fake_llm(calls))
    monkeypatch.setattr("site_ai_widget.dbconn.suggest.BATCH_SIZE", 4)
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    assert client.post(f"{URL}/introspect", headers=headers).status_code == 200

    first = client.post(f"{URL}/suggest", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"suggested": 4, "remaining": 2}
    assert client.post(f"{URL}/suggest", headers=headers).json() == {"suggested": 2, "remaining": 0}
    assert client.post(f"{URL}/suggest", headers=headers).json() == {"suggested": 0, "remaining": 0}
    assert len(calls) == 2  # nothing left, so no third LLM call

    rows = schema_rows(company_id)
    assert rows["products"] == ("pending", True, "Rows of public.products.")
    assert rows["customers"][:2] == ("pending", False)
    assert all(status == "pending" for status, _, _ in rows.values())  # the AI never approves


def test_suggest_bad_llm_answer_is_502_and_saves_nothing(client, monkeypatch):
    monkeypatch.setattr(
        "site_ai_widget.dbconn.suggest.complete_chat",
        lambda messages, **kwargs: "Sorry, I can't help with that.",
    )
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    assert client.post(f"{URL}/introspect", headers=headers).status_code == 200

    r = client.post(f"{URL}/suggest", headers=headers)
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "schema_suggest_failed"
    assert all(description is None for _, _, description in schema_rows(company_id).values())


def test_suggest_without_llm_key_is_503(client):
    headers, _ = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    assert client.post(f"{URL}/introspect", headers=headers).status_code == 200

    r = client.post(f"{URL}/suggest", headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "llm_not_configured"


def test_suggest_without_connection_is_404(client):
    headers, _ = register(client)
    assert client.post(f"{URL}/suggest", headers=headers).status_code == 404


def connected_and_introspected(client: TestClient) -> tuple[dict, str]:
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    assert client.post(f"{URL}/introspect", headers=headers).status_code == 200
    return headers, company_id


def column_of(schema: dict, table: str, column: str) -> dict:
    table_data = next(t for t in schema["tables"] if t["name"] == table)
    return next(c for c in table_data["columns"] if c["name"] == column)


def test_schema_lists_tables_and_marks_sensitive_columns(client):
    headers, _ = connected_and_introspected(client)
    r = client.get(f"{URL}/schema", headers=headers)
    assert r.status_code == 200, r.text
    schema = r.json()
    assert [t["name"] for t in schema["tables"]] == [
        "categories",
        "customers",
        "opening_hours",
        "products",
        "stock",
        "stores",
    ]
    assert sum(len(t["columns"]) for t in schema["tables"]) == 29
    for table, column in [("customers", "email"), ("customers", "nif"), ("stores", "phone"), ("stores", "address")]:
        col = column_of(schema, table, column)
        assert (col["is_sensitive"], col["status"]) == (True, "excluded")
    price = column_of(schema, "products", "price")
    assert (price["is_sensitive"], price["status"]) == (False, "pending")
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in r.text


def test_reintrospect_keeps_admin_sensitive_decision(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    # The store phone is public: the admin unmarks it (step 8b will do this through the API).
    with connection() as conn:
        conn.execute(
            "UPDATE schema_columns SET is_sensitive = false, status = 'pending' "
            "WHERE company_id = %s AND column_name = 'phone'",
            (company_id,),
        )
    assert client.post(f"{URL}/introspect", headers=headers).status_code == 200
    phone = column_of(client.get(f"{URL}/schema", headers=headers).json(), "stores", "phone")
    assert (phone["is_sensitive"], phone["status"]) == (False, "pending")


def test_schema_of_other_company_is_empty(client):
    connected_and_introspected(client)
    headers_b, _ = register(client)
    r = client.get(f"{URL}/schema", headers=headers_b)
    assert r.status_code == 200
    assert r.json() == {"tables": []}


def test_schema_requires_login(client):
    assert client.get(f"{URL}/schema").status_code == 401


def test_schema_approval_requires_login(client):
    assert client.put(f"{URL}/schema/approval", json={"approved_table_ids": []}).status_code == 401


def test_schema_approval_bulk_and_isolation(client):
    headers, _ = connected_and_introspected(client)
    schema = schema_of(client, headers)
    products = table_of(schema, "products")["id"]
    stores = table_of(schema, "stores")["id"]

    r = client.put(
        f"{URL}/schema/approval",
        json={"approved_table_ids": [products, stores]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    by_name = {t["name"]: t for t in r.json()["tables"]}
    assert by_name["products"]["status"] == "approved"
    assert by_name["stores"]["status"] == "approved"
    assert "password" not in r.text

    r = client.put(
        f"{URL}/schema/approval",
        json={"approved_table_ids": [products]},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    by_name = {t["name"]: t for t in r.json()["tables"]}
    assert by_name["products"]["status"] == "approved"
    assert by_name["stores"]["status"] == "pending"

    headers_b, _ = register(client)
    foreign = client.put(
        f"{URL}/schema/approval",
        json={"approved_table_ids": [products]},
        headers=headers_b,
    )
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "not_found"
    # Original company still has its approval.
    assert table_of(schema_of(client, headers), "products")["status"] == "approved"


def test_save_connection_never_returns_password_with_sslmode(client):
    headers, _ = register(client)
    r = client.put(URL, json=body(sslmode="disable"), headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "password" not in data
    assert data["sslmode"] == "disable"  # not a secret; shown so a re-save keeps it
    assert os.environ["ATLAS_READONLY_PASSWORD"] not in r.text
    got = client.get(URL, headers=headers)
    assert got.status_code == 200
    assert "password" not in got.json()
    assert got.json()["sslmode"] == "disable"


def schema_of(client: TestClient, headers: dict) -> dict:
    r = client.get(f"{URL}/schema", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def table_of(schema: dict, name: str) -> dict:
    return next(t for t in schema["tables"] if t["name"] == name)


def embedding_of(company_id: str, table_name: str) -> str | None:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        row = conn.execute(
            "SELECT embedding::text FROM schema_tables WHERE company_id = %s AND table_name = %s",
            (company_id, table_name),
        ).fetchone()
    return row[0] if row else None


def test_approving_table_approves_safe_columns_and_embeds(client):
    headers, company_id = connected_and_introspected(client)
    table_id = table_of(schema_of(client, headers), "stores")["id"]
    r = client.patch(f"{URL}/schema/tables/{table_id}", json={"status": "approved"}, headers=headers)
    assert r.status_code == 200, r.text
    stores = r.json()
    assert stores["status"] == "approved"
    by_name = {c["name"]: c for c in stores["columns"]}
    assert by_name["id"]["status"] == "approved"
    assert by_name["name"]["status"] == "approved"
    assert by_name["city"]["status"] == "approved"
    assert (by_name["address"]["status"], by_name["address"]["is_sensitive"]) == ("excluded", True)
    assert (by_name["phone"]["status"], by_name["phone"]["is_sensitive"]) == ("excluded", True)
    assert embedding_of(company_id, "stores") is not None


def test_sensitive_phone_needs_unmark_before_approve(client):
    headers, _ = connected_and_introspected(client)
    phone_id = column_of(schema_of(client, headers), "stores", "phone")["id"]
    r = client.patch(f"{URL}/schema/columns/{phone_id}", json={"status": "approved"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "column_sensitive"

    r = client.patch(
        f"{URL}/schema/columns/{phone_id}",
        json={"is_sensitive": False, "status": "approved"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    phone = next(c for c in r.json()["columns"] if c["name"] == "phone")
    assert (phone["is_sensitive"], phone["status"]) == (False, "approved")


def test_marking_sku_sensitive_excludes_and_changes_embedding(client):
    headers, company_id = connected_and_introspected(client)
    products_id = table_of(schema_of(client, headers), "products")["id"]
    assert (
        client.patch(
            f"{URL}/schema/tables/{products_id}",
            json={"status": "approved"},
            headers=headers,
        ).status_code
        == 200
    )
    before = embedding_of(company_id, "products")
    sku_id = column_of(schema_of(client, headers), "products", "sku")["id"]
    r = client.patch(
        f"{URL}/schema/columns/{sku_id}",
        json={"is_sensitive": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    sku = next(c for c in r.json()["columns"] if c["name"] == "sku")
    assert (sku["is_sensitive"], sku["status"]) == (True, "excluded")
    after = embedding_of(company_id, "products")
    assert after is not None
    assert after != before


def test_exclude_clears_embedding_and_description_refreshes_it(client):
    headers, company_id = connected_and_introspected(client)
    table_id = table_of(schema_of(client, headers), "products")["id"]
    assert (
        client.patch(
            f"{URL}/schema/tables/{table_id}",
            json={"status": "approved"},
            headers=headers,
        ).status_code
        == 200
    )
    assert embedding_of(company_id, "products") is not None

    r = client.patch(
        f"{URL}/schema/tables/{table_id}",
        json={"status": "excluded"},
        headers=headers,
    )
    assert r.status_code == 200
    assert embedding_of(company_id, "products") is None

    assert (
        client.patch(
            f"{URL}/schema/tables/{table_id}",
            json={"status": "approved"},
            headers=headers,
        ).status_code
        == 200
    )
    before = embedding_of(company_id, "products")
    r = client.patch(
        f"{URL}/schema/tables/{table_id}",
        json={"description": "Catalog of products for sale."},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["description"] == "Catalog of products for sale."
    assert embedding_of(company_id, "products") != before
    # An empty description clears it.
    r = client.patch(f"{URL}/schema/tables/{table_id}", json={"description": ""}, headers=headers)
    assert r.json()["description"] is None


def test_absent_table_cannot_be_approved(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    table_id = table_of(schema_of(client, headers), "products")["id"]
    with connection() as conn:
        conn.execute(
            "UPDATE schema_tables SET is_present = false WHERE company_id = %s AND id = %s",
            (company_id, table_id),
        )
    r = client.patch(
        f"{URL}/schema/tables/{table_id}",
        json={"status": "approved"},
        headers=headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "schema_not_present"


def test_other_company_cannot_change_schema(client):
    headers_a, _ = connected_and_introspected(client)
    schema_a = schema_of(client, headers_a)
    headers_b, _ = register(client)
    table_id = table_of(schema_a, "products")["id"]
    column_id = column_of(schema_a, "products", "price")["id"]

    r = client.patch(f"{URL}/schema/tables/{table_id}", json={"status": "approved"}, headers=headers_b)
    assert r.status_code == 404
    r = client.patch(f"{URL}/schema/columns/{column_id}", json={"status": "approved"}, headers=headers_b)
    assert r.status_code == 404
    assert table_of(schema_of(client, headers_a), "products")["status"] == "pending"


def test_patch_rejects_bad_input(client):
    headers, _ = connected_and_introspected(client)
    url = f"{URL}/schema/tables/{table_of(schema_of(client, headers), 'products')['id']}"
    assert client.patch(url, json={"status": "maybe"}, headers=headers).status_code == 422
    assert client.patch(url, json={"description": "bad\x00text"}, headers=headers).status_code == 422
    assert client.patch(f"{URL}/schema/tables/not-a-uuid", json={}, headers=headers).status_code == 422


def approve_products(client: TestClient, headers: dict) -> str:
    table_id = table_of(schema_of(client, headers), "products")["id"]
    r = client.patch(f"{URL}/schema/tables/{table_id}", json={"status": "approved"}, headers=headers)
    assert r.status_code == 200, r.text
    return table_id


def test_reintrospect_type_change_sends_column_back_to_review(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    approve_products(client, headers)
    real_type = column_of(schema_of(client, headers), "products", "price")["data_type"]
    before = embedding_of(company_id, "products")
    # Pretend the price used to be text: on the next read the client DB says otherwise.
    with connection() as conn:
        conn.execute(
            "UPDATE schema_columns SET data_type = 'text' WHERE company_id = %s AND column_name = 'price'",
            (company_id,),
        )

    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["needs_review"] == 1

    schema = schema_of(client, headers)
    price = column_of(schema, "products", "price")
    assert (price["data_type"], price["status"]) == (real_type, "pending")
    assert column_of(schema, "products", "name")["status"] == "approved"  # the others keep the decision
    assert table_of(schema, "products")["status"] == "approved"
    after = embedding_of(company_id, "products")
    assert after is not None
    assert after != before  # price left the embedded text


class _BrokenEmbedder:
    """Stands in for a model that crashes (out of memory, missing files...)."""

    def embed_passages(self, texts):
        raise RuntimeError("model crashed")

    def embed_query(self, text):
        raise RuntimeError("model crashed")


def test_introspect_survives_embedder_failure_and_retries_next_time(client):
    from site_ai_widget.db.pool import connection
    from site_ai_widget.docs.embed import FakeEmbedder, set_embedder

    headers, company_id = connected_and_introspected(client)
    approve_products(client, headers)
    assert embedding_of(company_id, "products") is not None
    # An approved column whose type "changed" puts products in refresh_ids.
    with connection() as conn:
        conn.execute(
            "UPDATE schema_columns SET data_type = 'text' WHERE company_id = %s AND column_name = 'price'",
            (company_id,),
        )

    set_embedder(_BrokenEmbedder())
    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.status_code == 200, r.text  # the schema save still stands
    assert r.json()["needs_review"] == 1
    assert embedding_of(company_id, "products") is None  # fail closed, no stale vector
    assert table_of(schema_of(client, headers), "products")["status"] == "approved"

    # Next introspect with a working model: approved + NULL embedding is retried.
    set_embedder(FakeEmbedder())
    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.status_code == 200, r.text
    assert embedding_of(company_id, "products") is not None


def test_reintrospect_missing_column_needs_review(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    table_id = approve_products(client, headers)
    # A column the client dropped later: it was approved, now it is gone.
    with connection() as conn:
        conn.execute(
            "INSERT INTO schema_columns (company_id, table_id, column_name, data_type, is_nullable, ordinal, status) "
            "VALUES (%s, %s, 'old_code', 'text', true, 99, 'approved')",
            (company_id, table_id),
        )
    # Refresh the embedding so it includes old_code.
    r = client.patch(f"{URL}/schema/tables/{table_id}", json={"description": "Products."}, headers=headers)
    assert r.status_code == 200
    before = embedding_of(company_id, "products")

    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.json()["needs_review"] == 1
    old_code = column_of(schema_of(client, headers), "products", "old_code")
    assert (old_code["is_present"], old_code["status"]) == (False, "pending")
    assert embedding_of(company_id, "products") != before


def test_reintrospect_removed_table_loses_embedding(client):
    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    with connection() as conn:
        (ghost_id,) = conn.execute(
            "INSERT INTO schema_tables (company_id, schema_name, table_name, kind, status) "
            "VALUES (%s, 'public', 'ghost', 'table', 'approved') RETURNING id",
            (company_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO schema_columns (company_id, table_id, column_name, data_type, is_nullable, ordinal, status) "
            "VALUES (%s, %s, 'id', 'integer', false, 1, 'approved')",
            (company_id, ghost_id),
        )
    r = client.patch(f"{URL}/schema/tables/{ghost_id}", json={"description": "Old table."}, headers=headers)
    assert r.status_code == 200
    assert embedding_of(company_id, "ghost") is not None

    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.json()["removed_tables"] == 1
    assert embedding_of(company_id, "ghost") is None
    ghost = table_of(schema_of(client, headers), "ghost")
    assert (ghost["is_present"], ghost["status"]) == (False, "pending")
    assert (ghost["columns"][0]["is_present"], ghost["columns"][0]["status"]) == (False, "pending")


# 300 extra tables in their own schema: scale + isolation (DROP SCHEMA in finally).
_NOISE_SQL = """
CREATE SCHEMA zz_noise;
GRANT USAGE ON SCHEMA zz_noise TO atlas_readonly;
DO $$
BEGIN
  FOR i IN 1..300 LOOP
    EXECUTE format(
      'CREATE TABLE zz_noise.%I (
         id integer PRIMARY KEY,
         body text,
         created_at timestamptz,
         user_email text
       );
       GRANT SELECT ON zz_noise.%I TO atlas_readonly',
      format('log_%s', lpad(i::text, 3, '0')),
      format('log_%s', lpad(i::text, 3, '0'))
    );
  END LOOP;
END $$;
"""


@pytest.fixture
def noise_tables():
    owner_pw = os.environ.get("CLIENTDB_PASSWORD")
    if not owner_pw:
        pytest.skip("CLIENTDB_PASSWORD is not set")
    with psycopg.connect(
        host=HOST,
        port=PORT,
        dbname=DB,
        user="atlas_owner",
        password=owner_pw,
        autocommit=True,
    ) as conn:
        conn.execute("DROP SCHEMA IF EXISTS zz_noise CASCADE")
        conn.execute(_NOISE_SQL)
        try:
            yield
        finally:
            conn.execute("DROP SCHEMA IF EXISTS zz_noise CASCADE")


def test_three_hundred_tables_introspect_and_suggest(client, noise_tables, monkeypatch):
    calls: list = []
    monkeypatch.setattr("site_ai_widget.dbconn.suggest.complete_chat", fake_llm(calls))
    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200

    r = client.post(f"{URL}/introspect", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["tables"] == 306
    assert r.json()["columns"] == 1229  # 29 demo + 300 * 4

    schema = schema_of(client, headers)
    assert len(schema["tables"]) == 306
    for table in schema["tables"]:
        if table["name"].startswith("log_"):
            assert table["schema_name"] == "zz_noise"
            email = column_of(schema, table["name"], "user_email")
            assert (email["is_sensitive"], email["status"]) == (True, "excluded")

    remaining = 1
    while remaining:
        r = client.post(f"{URL}/suggest", headers=headers)
        assert r.status_code == 200, r.text
        remaining = r.json()["remaining"]
    assert len(calls) == 13  # ceil(306 / 25)
    assert all(status == "pending" for status, _, _ in schema_rows(company_id).values())


def test_other_company_introspect_does_not_touch_ours(client):
    from site_ai_widget.db.pool import connection

    headers_a, company_a = connected_and_introspected(client)
    approve_products(client, headers_a)
    with connection() as conn:
        conn.execute(
            "INSERT INTO schema_tables (company_id, schema_name, table_name, kind, status) "
            "VALUES (%s, 'public', 'ghost', 'table', 'approved')",
            (company_a,),
        )

    headers_b, _ = register(client)
    assert client.put(URL, json=body(), headers=headers_b).status_code == 200
    assert client.post(f"{URL}/introspect", headers=headers_b).status_code == 200

    schema_a = schema_of(client, headers_a)
    ghost = table_of(schema_a, "ghost")
    assert (ghost["is_present"], ghost["status"]) == (True, "approved")
    assert table_of(schema_a, "products")["status"] == "approved"


def test_suggest_only_sees_own_company_tables(client, monkeypatch):
    calls: list = []
    monkeypatch.setattr("site_ai_widget.dbconn.suggest.complete_chat", fake_llm(calls))
    headers_a, _ = connected_and_introspected(client)
    connected_and_introspected(client)  # company B, same client DB
    r = client.post(f"{URL}/suggest", headers=headers_a)
    assert r.status_code == 200, r.text
    # If the company_id filter failed, A would see B's 6 tables too (12).
    assert r.json() == {"suggested": 6, "remaining": 0}
    assert len(calls) == 1


def test_schema_column_fk_blocks_cross_company(client):
    from site_ai_widget.db.pool import connection

    headers_a, _ = connected_and_introspected(client)
    _, company_b = register(client)
    table_id = table_of(schema_of(client, headers_a), "products")["id"]
    # Even a bug in our code cannot do this: the (table_id, company_id) FK refuses it.
    with pytest.raises(psycopg.errors.ForeignKeyViolation), connection() as conn:
        conn.execute(
            "INSERT INTO schema_columns (company_id, table_id, column_name, data_type, is_nullable, ordinal, status) "
            "VALUES (%s, %s, 'stolen', 'text', true, 1, 'pending')",
            (company_b, table_id),
        )


def test_delete_while_connection_locked_still_clears_schema(client):
    """DELETE locks db_connections first, so it cannot race past a held introspect lock."""
    import threading

    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    ready = threading.Event()
    errors: list[BaseException] = []

    def hold_lock() -> None:
        try:
            with connection() as conn:
                conn.execute(
                    "SELECT 1 FROM db_connections WHERE company_id = %s FOR UPDATE",
                    (company_id,),
                )
                ready.set()
                time.sleep(0.4)
        except Exception as exc:
            errors.append(exc)

    def do_delete() -> None:
        try:
            assert ready.wait(timeout=5)
            r = client.delete(URL, headers=headers)
            assert r.status_code == 204, r.text
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=hold_lock)
    t2 = threading.Thread(target=do_delete)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors, errors
    assert stored_dsn(company_id) is None
    assert count_schema_tables(company_id) == 0


def test_update_column_locks_parent_table_first(client):
    """Opposite lock order (column then table) deadlocks; ours waits then succeeds."""
    import threading

    from site_ai_widget.db.pool import connection

    headers, _ = connected_and_introspected(client)
    schema = schema_of(client, headers)
    table_id = table_of(schema, "products")["id"]
    column_id = column_of(schema, "products", "price")["id"]
    ready = threading.Event()
    result: dict[str, int] = {}
    errors: list[BaseException] = []

    def hold_table() -> None:
        try:
            with connection() as conn:
                conn.execute(
                    "SELECT 1 FROM schema_tables WHERE id = %s FOR UPDATE",
                    (table_id,),
                )
                ready.set()
                time.sleep(0.4)
        except Exception as exc:
            errors.append(exc)

    def patch_column() -> None:
        try:
            assert ready.wait(timeout=5)
            time.sleep(0.05)  # ensure the table lock is held
            r = client.patch(
                f"{URL}/schema/columns/{column_id}",
                json={"description": "Unit price."},
                headers=headers,
            )
            result["status"] = r.status_code
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=hold_table)
    t2 = threading.Thread(target=patch_column)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors, errors
    assert result.get("status") == 200


def test_probe_ip_bucket_is_shared_and_does_not_spend_company_budget(client, monkeypatch):
    from site_ai_widget.api.ratelimit import RateLimiter
    from site_ai_widget.api.routes import db_connection

    limiter = RateLimiter(2, 600)
    monkeypatch.setattr(db_connection, "_probe_limiter", limiter)
    headers_a, _ = register(client)
    headers_b, company_b = register(client)
    blocked = body(host="10.0.0.5")  # refused after the limiter, so nothing is reached

    for _ in range(2):
        r = client.put(URL, json=blocked, headers=headers_a)
        assert r.json()["detail"]["code"] == "db_host_not_allowed"

    # Same client IP, other company: the IP bucket is full.
    r = client.put(URL, json=blocked, headers=headers_b)
    assert r.status_code == 429
    assert f"probe:{company_b}" not in limiter._calls  # company b spent nothing

    # A spoofed X-Forwarded-For from an untrusted peer does not get a fresh bucket.
    r = client.put(URL, json=blocked, headers={**headers_b, "X-Forwarded-For": "198.51.100.7"})
    assert r.status_code == 429


def _connection_state(company_id: str) -> tuple:
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        return conn.execute(
            "SELECT status, last_error, last_checked_at, dsn_encrypted FROM db_connections "
            "WHERE company_id = %s",
            (company_id,),
        ).fetchone()


def test_busy_probe_is_503_and_keeps_the_saved_status(client):
    """A busy session says nothing about the connection: never store it as 'failed'."""
    from site_ai_widget.dbconn import guard

    headers, company_id = register(client)
    assert client.put(URL, json=body(), headers=headers).status_code == 200
    before = _connection_state(company_id)
    assert before[:2] == ("ok", None)

    # This company already has a session open (e.g. an introspect is running).
    with guard._active_lock:
        guard._active_companies.add(company_id)
    try:
        tested = client.post(f"{URL}/test", headers=headers)
        assert tested.status_code == 503
        assert tested.json()["detail"]["code"] == "db_busy"

        saved = client.put(URL, json=body(), headers=headers)
        assert saved.status_code == 503
        assert saved.json()["detail"]["code"] == "db_busy"
    finally:
        with guard._active_lock:
            guard._active_companies.discard(company_id)

    assert _connection_state(company_id) == before  # status, last_error, time, DSN untouched
    # Once the session is free again, the test runs normally.
    assert client.post(f"{URL}/test", headers=headers).json()["status"] == "ok"


def test_schema_approval_starts_pending_is_atomic_and_admin_only(client):
    """New tables start not approved; a bad id leaves nothing half-saved; members get 403."""
    from site_ai_widget.db.pool import connection

    headers, company_id = connected_and_introspected(client)
    schema = schema_of(client, headers)
    assert {t["status"] for t in schema["tables"]} == {"pending"}
    products = table_of(schema, "products")["id"]
    stores = table_of(schema, "stores")["id"]

    with connection() as conn:
        conn.execute(
            "UPDATE schema_tables SET is_present = false WHERE company_id = %s AND id = %s",
            (company_id, products),
        )
    r = client.put(
        f"{URL}/schema/approval",
        json={"approved_table_ids": [stores, products]},
        headers=headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "schema_not_present"
    # stores was processed before the failure in id order or not: either way rolled back.
    assert table_of(schema_of(client, headers), "stores")["status"] == "pending"

    dup = client.put(
        f"{URL}/schema/approval",
        json={"approved_table_ids": [stores, stores]},
        headers=headers,
    )
    assert dup.status_code == 422

    with connection() as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE company_id = %s", (company_id,))
    member = client.put(
        f"{URL}/schema/approval",
        json={"approved_table_ids": [stores]},
        headers=headers,
    )
    assert member.status_code == 403
    assert table_of(schema_of(client, headers), "stores")["status"] == "pending"
