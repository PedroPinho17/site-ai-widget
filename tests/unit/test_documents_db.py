"""DB-backed document upload / isolation / roles (skip if Postgres is down)."""
from __future__ import annotations

import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.docs.embed import FakeEmbedder, set_embedder
from tests.conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres is not reachable (docker compose up -d db)",
)


def _pdf_with_text(text: str = "Mercearia Atlas aberto ao domingo das 10 as 14.") -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 10, text)
    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()


def _wait_status(client: TestClient, token: str, doc_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/documents/{doc_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in ("ready", "failed"):
            return last
        time.sleep(0.15)
    return last


@pytest.fixture(scope="module")
def migrated():
    from site_ai_widget.db.migrate import migrate

    migrate()


@pytest.fixture
def client(migrated, tmp_path, monkeypatch):
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.pool import close_pool

    monkeypatch.setenv("SITE_AI_WIDGET_FAKE_EMBEDDER", "1")
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
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


def _register(client: TestClient, slug: str | None = None):
    slug = slug or f"co-{uuid.uuid4().hex[:10]}"
    email = f"{slug}@example.com"
    password = "test-pass-12"
    r = client.post(
        "/auth/register",
        json={
            "company_name": f"Company {slug}",
            "company_slug": slug,
            "email": email,
            "password": password,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return data["access_token"], data["user"], password


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_upload_process_ready_e2e(client):
    token, _, _ = _register(client)
    raw = _pdf_with_text("Atlas horario domingo 10:00-14:00. Morada Rua das Amendoeiras 48.")
    r = client.post(
        "/documents",
        headers=_auth(token),
        files={"file": ("atlas.pdf", raw, "application/pdf")},
    )
    assert r.status_code == 202, r.text
    doc_id = r.json()["id"]
    assert r.json()["status"] == "pending"

    body = _wait_status(client, token, doc_id)
    assert body["status"] == "ready", body
    assert body["chunk_count"] >= 1
    assert body["page_count"] >= 1


def test_company_isolation_documents(client):
    token_a, user_a, _ = _register(client)
    token_b, _, _ = _register(client)
    raw = _pdf_with_text("Conteudo secreto da empresa B: codigo XYZ-999.")
    up = client.post(
        "/documents",
        headers=_auth(token_b),
        files={"file": ("b.pdf", raw, "application/pdf")},
    )
    assert up.status_code == 202
    doc_b = up.json()["id"]
    body = _wait_status(client, token_b, doc_b)
    assert body["status"] == "ready", body

    assert client.get("/documents", headers=_auth(token_a)).json() == []
    assert client.get(f"/documents/{doc_b}", headers=_auth(token_a)).status_code == 404
    assert client.delete(f"/documents/{doc_b}", headers=_auth(token_a)).status_code == 404

    from site_ai_widget.docs.retrieve import hybrid_search

    hits = hybrid_search(user_a["company_id"], "codigo XYZ-999")
    assert hits == []


def test_member_cannot_upload_or_delete(client):
    token, user, _ = _register(client)
    from site_ai_widget.db.pool import connection

    with connection() as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE id = %s", (user["id"],))

    raw = _pdf_with_text("texto")
    up = client.post(
        "/documents",
        headers=_auth(token),
        files={"file": ("m.pdf", raw, "application/pdf")},
    )
    assert up.status_code == 403

    token2, user2, _ = _register(client)
    up2 = client.post(
        "/documents",
        headers=_auth(token2),
        files={"file": ("a.pdf", raw, "application/pdf")},
    )
    assert up2.status_code == 202
    doc_id = up2.json()["id"]
    with connection() as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE id = %s", (user2["id"],))
    deleted = client.delete(f"/documents/{doc_id}", headers=_auth(token2))
    assert deleted.status_code == 403


def test_duplicate_upload_409(client):
    token, _, _ = _register(client)
    raw = _pdf_with_text(f"unique content {uuid.uuid4()}")
    files = {"file": ("dup.pdf", raw, "application/pdf")}
    assert client.post("/documents", headers=_auth(token), files=files).status_code == 202
    files2 = {"file": ("dup.pdf", raw, "application/pdf")}
    dup = client.post("/documents", headers=_auth(token), files=files2)
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "document_duplicate"
    assert "document_id" in dup.json()["detail"]


def test_delete_removes_chunks_and_file(client):
    token, _, _ = _register(client)
    raw = _pdf_with_text("delete me please atlas lima")
    up = client.post(
        "/documents",
        headers=_auth(token),
        files={"file": ("del.pdf", raw, "application/pdf")},
    )
    doc_id = up.json()["id"]
    body = _wait_status(client, token, doc_id)
    assert body["status"] == "ready", body

    from site_ai_widget.db.pool import connection
    from site_ai_widget.docs.storage import storage_path

    with connection() as conn:
        storage_name = conn.execute(
            "SELECT storage_name FROM documents WHERE id = %s", (doc_id,)
        ).fetchone()[0]
    path = storage_path(storage_name)
    assert path.is_file()

    assert client.delete(f"/documents/{doc_id}", headers=_auth(token)).status_code == 204
    assert not path.exists()
    with connection() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM document_chunks WHERE document_id = %s", (doc_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM documents WHERE id = %s", (doc_id,)).fetchone()[0]
            == 0
        )


def _upload_ready(client: TestClient, token: str, text: str, name: str = "doc.pdf") -> str:
    up = client.post(
        "/documents",
        headers=_auth(token),
        files={"file": (name, _pdf_with_text(text), "application/pdf")},
    )
    assert up.status_code == 202, up.text
    doc_id = up.json()["id"]
    assert _wait_status(client, token, doc_id)["status"] == "ready"
    return doc_id


def test_ask_docs_with_other_company_document_ids_404(client, monkeypatch):
    token_a, _, _ = _register(client)
    token_b, _, _ = _register(client)
    doc_a = _upload_ready(client, token_a, f"Atlas domingo 10-14 {uuid.uuid4()}")
    doc_b = _upload_ready(client, token_b, f"Segredo da empresa B {uuid.uuid4()}")

    called = []
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.hybrid_search",
        lambda *a, **k: called.append(1) or [],
    )
    for ids in ([doc_b], [doc_a, doc_b], [str(uuid.uuid4())]):
        r = client.post(
            "/ask/docs",
            headers=_auth(token_a),
            json={"question": "Qual o segredo?", "document_ids": ids},
        )
        assert r.status_code == 404, (ids, r.text)
        assert r.json()["detail"]["code"] == "not_found"
    assert called == []  # rejected before any retrieval

    own = client.post(
        "/ask/docs",
        headers=_auth(token_a),
        json={"question": "domingo?", "document_ids": [doc_a]},
    )
    assert own.status_code == 200, own.text


class _SpyConn:
    """Wraps a real psycopg connection to observe / alter SQL in hybrid_search."""

    def __init__(self, inner, rewrite_text_query: bool = False):
        self._inner = inner
        self._rewrite = rewrite_text_query
        self.observed: dict = {}

    def execute(self, sql, params=None):
        if "SET LOCAL hnsw.iterative_scan" in sql:
            # psycopg opens the transaction (BEGIN) with this first statement;
            # SET LOCAL outside a transaction would be a no-op.
            result = self._inner.execute(sql, params)
            self.observed["status_after_set"] = self._inner.info.transaction_status
            self.observed["pid_at_set"] = self._inner.info.backend_pid
            return result
        if "ORDER BY c.embedding" in sql:
            self.observed["iterative_scan_during_vector"] = self._inner.execute(
                "SHOW hnsw.iterative_scan"
            ).fetchone()[0]
            self.observed["backend_pid"] = self._inner.info.backend_pid
        if "websearch_to_tsquery" in sql and self._rewrite:
            # to_tsquery raises a real syntax error on malformed input
            sql = sql.replace("websearch_to_tsquery(", "to_tsquery(")
        return self._inner.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _spy_connection(monkeypatch, **kwargs) -> _SpyConn:
    from contextlib import contextmanager

    from site_ai_widget.db import pool as pool_mod
    from site_ai_widget.docs import retrieve

    holder: dict = {}

    @contextmanager
    def spy():
        with pool_mod.connection() as real:
            holder["spy"] = _SpyConn(real, **kwargs)
            yield holder["spy"]

    monkeypatch.setattr(retrieve, "connection", spy)
    return holder


def test_set_local_iterative_scan_same_transaction_as_search(client, monkeypatch):
    token, user, _ = _register(client)
    _upload_ready(client, token, f"Atlas horario domingo 10-14 {uuid.uuid4()}")

    from site_ai_widget.db.pool import connection
    from site_ai_widget.docs.retrieve import hybrid_search

    holder = _spy_connection(monkeypatch)
    hits = hybrid_search(user["company_id"], "domingo")
    assert hits
    seen = holder["spy"].observed
    from psycopg.pq import TransactionStatus

    assert seen["status_after_set"] == TransactionStatus.INTRANS
    assert seen["pid_at_set"] == seen["backend_pid"]  # same session
    assert seen["iterative_scan_during_vector"] == "relaxed_order"

    # LOCAL: gone after the transaction, so it never leaks via the pool
    with connection() as conn:
        assert conn.execute("SHOW hnsw.iterative_scan").fetchone()[0] != "relaxed_order"


def test_fulltext_error_falls_back_to_vector_results(client, monkeypatch, caplog):
    token, user, _ = _register(client)
    _upload_ready(client, token, f"Atlas horario domingo 10-14 {uuid.uuid4()}")

    from site_ai_widget.docs.retrieve import hybrid_search

    _spy_connection(monkeypatch, rewrite_text_query=True)
    question = "domingo & & ! ("  # invalid for to_tsquery -> real DB error
    hits = hybrid_search(user["company_id"], question)
    assert hits, "vector results must survive a full-text error"
    assert question not in caplog.text


def test_ask_docs_llm_failure_is_503_not_500(client, monkeypatch):
    import httpx
    import openai

    token, _, _ = _register(client)
    _upload_ready(client, token, f"Atlas horario domingo 10-14 {uuid.uuid4()}")

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_dummy_key_for_unit_tests_only")
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.llm import client as llm

    clear_settings_cache()
    llm.reset_llm_client()

    req = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")

    class Boom:
        def chat_completions_create(self, **_):
            raise openai.InternalServerError(
                "upstream down", response=httpx.Response(502, request=req), body=None
            )

    monkeypatch.setattr(llm, "get_llm_client", lambda: Boom())
    r = client.post("/ask/docs", headers=_auth(token), json={"question": "domingo?"})
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "llm_unavailable"
    llm.reset_llm_client()
