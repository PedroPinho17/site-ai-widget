"""Public widget ask, history, origin allowlist and CORS."""
from __future__ import annotations

import hashlib
import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from site_ai_widget.api.errors import ApiError
from site_ai_widget.sqlqa.pipeline import SqlAnswer
from tests.conftest import postgres_available

pytestmark = pytest.mark.skipif(
    not postgres_available(),
    reason="Postgres is not reachable (docker compose up -d db)",
)

ORIGIN = "https://shop.example"
SENTINEL_Q = "SENTINEL-WIDGET-QUESTION"
SENTINEL_SQL = "SELECT secret FROM passwords WHERE id = 1"

_CORS_NAMES = (
    "access-control-allow-origin",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-allow-credentials",
    "access-control-max-age",
)


@pytest.fixture(scope="module")
def migrated():
    from site_ai_widget.db.migrate import migrate
    from site_ai_widget.db.pool import close_pool

    close_pool()
    migrate()
    close_pool()


@pytest.fixture
def client(migrated, monkeypatch):
    monkeypatch.setenv("AUTH_RATE_LIMIT", "1000")
    monkeypatch.setenv("API_RATE_LIMIT", "1000")
    monkeypatch.setenv("ASK_RATE_LIMIT", "1000")
    monkeypatch.setenv("WIDGET_KEY_RATE_LIMIT", "1000")
    # Deterministic SQL answers in these tests (no live LLM).
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY_FALLBACK", "")
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters
    from site_ai_widget.api.main import create_app
    from site_ai_widget.db.pool import close_pool

    clear_settings_cache()
    reset_limiters()
    close_pool()
    app = create_app()
    with TestClient(app) as c:
        yield c
    close_pool()
    reset_limiters()
    clear_settings_cache()


@pytest.fixture(autouse=True)
def _fake_sql(monkeypatch):
    # The widget reaches the pipeline through ask_service, not the /ask route module.
    monkeypatch.setattr(
        "site_ai_widget.sqlqa.pipeline.answer_with_sql",
        lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[["Azeite"]],
            row_count=1,
            truncated=False,
            attempts=1,
        ),
    )


def _block_ask_service(monkeypatch) -> list:
    from site_ai_widget import ask_service

    calls: list = []

    def spy(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("ask service must not run")

    monkeypatch.setattr(ask_service, "answer_question", spy)
    return calls


def _register(client: TestClient) -> tuple[str, str]:
    slug = f"w-{uuid4().hex[:10]}"
    response = client.post(
        "/auth/register",
        json={
            "company_name": f"Company {slug}",
            "company_slug": slug,
            "email": f"{slug}@example.com",
            "password": "test-pass-12",
            "default_locale": "pt",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["access_token"], body["user"]["company_id"]


def _make_key(client: TestClient, token: str, origins: list[str] | None = None) -> dict:
    response = client.post(
        "/widget-keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"allowed_origins": [ORIGIN] if origins is None else origins},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _ask(
    client: TestClient,
    key: str,
    *,
    origin: str | None = ORIGIN,
    question: str = "qual o preco?",
    **fields,
):
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    return client.post(f"/v1/widget/{key}/ask", headers=headers, json={"question": question, **fields})


def _history(client: TestClient, key: str, token: str, *, origin: str = ORIGIN):
    return client.post(
        f"/v1/widget/{key}/history",
        headers={"Origin": origin},
        json={"session_token": token},
    )


def _assert_cors(response, origin: str) -> None:
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-origin"] != "*"
    assert "access-control-allow-credentials" not in response.headers
    assert response.headers["access-control-allow-methods"] == "POST"
    assert response.headers["access-control-allow-headers"].lower() == "content-type"
    assert response.headers["access-control-max-age"] == "600"
    assert "origin" in response.headers["vary"].lower()


def _assert_no_cors(response) -> None:
    for name in _CORS_NAMES:
        assert name not in response.headers


def _tighten(monkeypatch, **env: str) -> None:
    from site_ai_widget.api.config import clear_settings_cache
    from site_ai_widget.api.deps import reset_limiters

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    clear_settings_cache()
    reset_limiters()


def test_valid_key_returns_answer_and_session(client):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    response = _ask(client, key, question="qual o preco?", lang="pt")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "sql"
    assert body["columns"] == ["name"]
    assert body["rows"] == [["Azeite"]]
    assert body["row_count"] == 1
    assert body["answer"] == "nome: Azeite"  # one-row fallback includes the column label
    assert "SELECT" not in body["answer"]
    assert isinstance(body["session_token"], str)
    assert len(body["session_token"]) >= 40
    assert "SELECT" not in response.text
    _assert_cors(response, ORIGIN)


def test_only_the_exact_allowlisted_origin_is_accepted(client, monkeypatch):
    """The allowlist is stored canonical; the header must match it byte for byte."""
    calls = _block_ask_service(monkeypatch)
    token, _company = _register(client)
    # Stored canonical as https://shop.example even though written loosely.
    key = _make_key(client, token, ["https://Shop.Example/"])["public_key"]
    for variant in (
        "https://Shop.Example/",
        "https://shop.example/",
        "HTTPS://shop.example",
        "https://SHOP.EXAMPLE",
        "https://shop.example:443",
    ):
        response = _ask(client, key, origin=variant, question="preco?")
        assert response.status_code == 403, variant
        assert response.json()["detail"]["code"] == "origin_not_allowed"
        _assert_no_cors(response)
        preflight = client.options(
            f"/v1/widget/{key}/ask",
            headers={"Origin": variant, "Access-Control-Request-Method": "POST"},
        )
        assert preflight.status_code == 403, variant
        _assert_no_cors(preflight)
    assert calls == []


def test_exact_origin_is_reflected(client):
    token, _company = _register(client)
    key = _make_key(client, token, ["https://Shop.Example/"])["public_key"]
    response = _ask(client, key, origin=ORIGIN, question="preco?")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_unknown_and_inactive_key_same_401(client, monkeypatch):
    calls = _block_ask_service(monkeypatch)
    token, _company = _register(client)
    created = _make_key(client, token)
    deactivated = client.patch(
        f"/widget-keys/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    question = f"{SENTINEL_Q} {SENTINEL_SQL}"
    unknown = _ask(client, "wk_unknown_example_key_0001", question=question)
    inactive = _ask(client, created["public_key"], question=question)
    assert unknown.status_code == inactive.status_code == 401
    assert unknown.json() == inactive.json()
    assert unknown.json()["detail"]["code"] == "widget_key_invalid"
    assert SENTINEL_Q not in unknown.text
    assert SENTINEL_SQL not in inactive.text
    assert created["public_key"] not in inactive.text
    assert calls == []


def test_origin_rejected_does_not_call_ask_service(client, monkeypatch):
    calls = _block_ask_service(monkeypatch)
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    question = f"{SENTINEL_Q} {SENTINEL_SQL}"
    rejected = [
        _ask(client, key, origin=None, question=question),
        _ask(client, key, origin="https://evil.example", question=question),
        _ask(client, key, origin="https://shop.example:8443", question=question),
        _ask(client, key, origin="http://shop.example", question=question),
        client.post(
            f"/v1/widget/{key}/ask",
            headers={"Referer": ORIGIN},
            json={"question": question},
        ),
    ]
    for response in rejected:
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "origin_not_allowed"
        assert SENTINEL_Q not in response.text
        assert "SELECT" not in response.text
        _assert_no_cors(response)
    assert calls == []


def test_preflight_cors(client, monkeypatch):
    calls = _block_ask_service(monkeypatch)
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    allowed = client.options(
        f"/v1/widget/{key}/ask",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert allowed.status_code == 204
    _assert_cors(allowed, ORIGIN)

    history = client.options(
        f"/v1/widget/{key}/history",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert history.status_code == 204
    _assert_cors(history, ORIGIN)

    denied = client.options(
        f"/v1/widget/{key}/ask",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert denied.status_code == 403
    _assert_no_cors(denied)

    unknown = client.options(
        "/v1/widget/wk_unknown_example_key_0001/ask",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert unknown.status_code == 401
    _assert_no_cors(unknown)

    health = client.get("/health", headers={"Origin": ORIGIN})
    assert health.status_code == 200
    _assert_no_cors(health)
    panel = client.post("/ask", headers={"Origin": ORIGIN}, json={"question": "q"})
    # No JWT: rejected by auth, and still no widget CORS headers.
    assert panel.status_code == 401
    _assert_no_cors(panel)
    assert calls == []


def test_extra_fields_rejected(client, monkeypatch):
    calls = _block_ask_service(monkeypatch)
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    extra = _ask(client, key, question="hi", company_id="nope")
    assert extra.status_code == 422
    assert extra.json()["detail"]["code"] == "validation_error"
    bad_lang = _ask(client, key, question="hi", lang="fr")
    assert bad_lang.status_code == 422
    assert calls == []


def test_question_rules_match_ask(client):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    empty = _ask(client, key, question="   ")
    assert empty.status_code == 422
    assert empty.json()["detail"]["code"] == "validation_error"
    too_long = _ask(client, key, question="x" * 1001)
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["code"] == "question_too_long"
    assert too_long.json()["detail"].get("n") == 1000
    ok = _ask(client, key, question="y" * 1000)
    assert ok.status_code == 200


def test_sessions_do_not_share_history(client):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    first = _ask(client, key, question="session-one-marker")
    second = _ask(client, key, question="session-two-marker")
    assert first.status_code == second.status_code == 200
    token_a = first.json()["session_token"]
    token_b = second.json()["session_token"]
    assert token_a != token_b
    hist_a = _history(client, key, token_a)
    hist_b = _history(client, key, token_b)
    assert hist_a.status_code == hist_b.status_code == 200
    text_a = " ".join(item["text"] or "" for item in hist_a.json()["messages"])
    text_b = " ".join(item["text"] or "" for item in hist_b.json()["messages"])
    assert "session-one-marker" in text_a
    assert "session-two-marker" not in text_a
    assert "session-two-marker" in text_b
    assert "session-one-marker" not in text_b
    assert hist_a.json()["messages"][0]["role"] == "user"
    assert hist_a.json()["messages"][0]["text"] == "session-one-marker"
    unknown = _history(client, key, "not-a-real-session-token")
    assert unknown.status_code == 200
    assert unknown.json()["messages"] == []


def test_same_session_history_is_oldest_first(client):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    first = _ask(client, key, question="primeira")
    session = first.json()["session_token"]
    second = _ask(client, key, question="segunda", session_token=session)
    assert second.status_code == 200
    assert second.json()["session_token"] == session
    messages = _history(client, key, session).json()["messages"]
    user_texts = [item["text"] for item in messages if item["role"] == "user"]
    assert user_texts == ["primeira", "segunda"]
    assert messages[0]["text"] == "primeira"
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["mode"] == "sql"
    # History stores the same visitor-facing answer the ask response returned.
    assert messages[-1]["text"] == second.json()["answer"]
    assert messages[-1]["text"] == "name: Azeite"
    assert "SELECT" not in (messages[-1]["text"] or "")
    assert messages[-1]["text"] != "price"


def test_history_returns_last_50_oldest_first(client):
    from site_ai_widget.db.pool import connection
    from site_ai_widget.widget.access import hash_session_token

    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    created = _ask(client, key, question="seed")
    session = created.json()["session_token"]
    with connection() as conn:
        sid, company_id = conn.execute(
            "SELECT id::text, company_id::text FROM visitor_sessions WHERE token_hash = %s",
            (hash_session_token(session),),
        ).fetchone()
        for i in range(60):
            conn.execute(
                """
                INSERT INTO visitor_messages (session_id, company_id, role, text)
                VALUES (%s, %s, 'user', %s)
                """,
                (sid, company_id, f"m{i}"),
            )
    messages = _history(client, key, session).json()["messages"]
    assert len(messages) == 50
    assert messages[0]["text"] == "m10"
    assert messages[-1]["text"] == "m59"
    assert [item["text"] for item in messages[:3]] == ["m10", "m11", "m12"]


def test_session_is_not_reused_across_companies(client):
    token_a, _company_a = _register(client)
    token_b, _company_b = _register(client)
    key_a = _make_key(client, token_a)["public_key"]
    key_b = _make_key(client, token_b)["public_key"]
    first = _ask(client, key_a, question="alpha-from-a")
    session_a = first.json()["session_token"]
    crossed = _ask(client, key_b, question="beta-from-b", session_token=session_a)
    assert crossed.status_code == 200
    session_b = crossed.json()["session_token"]
    assert session_b != session_a
    hist_a = _history(client, key_a, session_a).json()["messages"]
    hist_b_with_a = _history(client, key_b, session_a).json()["messages"]
    hist_b = _history(client, key_b, session_b).json()["messages"]
    text_a = " ".join(item["text"] or "" for item in hist_a)
    text_b = " ".join(item["text"] or "" for item in hist_b)
    assert "alpha-from-a" in text_a
    assert "beta-from-b" not in text_a
    assert hist_b_with_a == []
    assert "beta-from-b" in text_b
    assert "alpha-from-a" not in text_b


def test_expired_session_creates_a_new_one(client):
    from site_ai_widget.db.pool import connection
    from site_ai_widget.widget.access import hash_session_token

    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    first = _ask(client, key, question="before-expiry")
    session = first.json()["session_token"]
    digest = hash_session_token(session)

    def _age(hours: int) -> None:
        with connection() as conn:
            conn.execute(
                """
                UPDATE visitor_sessions
                SET last_seen = now() - make_interval(hours => %s)
                WHERE token_hash = %s
                """,
                (hours, digest),
            )

    _age(23)
    still = _ask(client, key, question="still-alive", session_token=session)
    assert still.status_code == 200
    assert still.json()["session_token"] == session

    _age(25)
    renewed = _ask(client, key, question="after-expiry", session_token=session)
    assert renewed.status_code == 200
    fresh = renewed.json()["session_token"]
    assert fresh != session
    # An expired token no longer reads its old conversation.
    assert _history(client, key, session).json()["messages"] == []
    new_text = " ".join(item["text"] or "" for item in _history(client, key, fresh).json()["messages"])
    assert "after-expiry" in new_text
    assert "before-expiry" not in new_text


def test_token_stored_only_as_hash(client):
    from site_ai_widget.db.pool import connection

    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    response = _ask(client, key, question="qual o preco do azeite?")
    session = response.json()["session_token"]
    digest = hashlib.sha256(session.encode()).hexdigest()
    with connection() as conn:
        columns = [
            row[0]
            for row in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'visitor_sessions'
                """
            ).fetchall()
        ]
        assert "token_hash" in columns
        assert "token" not in columns
        assert "session_token" not in columns
        stored = conn.execute(
            """
            SELECT id::text, company_id::text, token_hash, created_at::text, last_seen::text
            FROM visitor_sessions WHERE token_hash = %s
            """,
            (digest,),
        ).fetchone()
        assert stored is not None
        assert stored[2] == digest
        assert session not in " ".join(stored)
        message_columns = [
            row[0]
            for row in conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'visitor_messages'
                """
            ).fetchall()
        ]
        assert "sql" not in message_columns
        texts = conn.execute(
            "SELECT coalesce(text, '') FROM visitor_messages WHERE session_id = %s",
            (stored[0],),
        ).fetchall()
        blob = " ".join(row[0] for row in texts)
        assert session not in blob
        assert "SELECT" not in blob


def test_rate_limit_per_ip_shares_ask_bucket(client, monkeypatch):
    _tighten(monkeypatch, ASK_RATE_LIMIT="3", WIDGET_KEY_RATE_LIMIT="60")
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    panel = client.post(
        "/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "from the panel"},
    )
    assert panel.status_code == 200
    blocked = _ask(client, key, question="from the widget")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in blocked.headers


def test_rate_limit_per_ip_blocks_a_second_key(client, monkeypatch):
    _tighten(monkeypatch, ASK_RATE_LIMIT="3", WIDGET_KEY_RATE_LIMIT="60")
    token, _company = _register(client)
    key_a = _make_key(client, token)["public_key"]
    key_b = _make_key(client, token)["public_key"]
    assert _ask(client, key_a, question="first").status_code == 200
    blocked = _ask(client, key_b, question="second")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"


def test_rate_limit_per_key(client, monkeypatch):
    _tighten(monkeypatch, ASK_RATE_LIMIT="1000", WIDGET_KEY_RATE_LIMIT="3")
    token, _company = _register(client)
    key_a = _make_key(client, token)["public_key"]
    key_b = _make_key(client, token)["public_key"]
    for i in range(3):
        assert _ask(client, key_a, question=f"q{i}").status_code == 200
    blocked = _ask(client, key_a, question="q3")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert _ask(client, key_b, question="other key").status_code == 200


def test_no_sql_or_sentinel_in_response_or_logs(client, monkeypatch, caplog):
    monkeypatch.setattr(
        "site_ai_widget.sqlqa.pipeline.answer_with_sql",
        lambda *a, **k: SqlAnswer(status="refused", reason="forbidden_op", attempts=2),
    )
    monkeypatch.setattr(
        "site_ai_widget.docs.ask.ask_documents",
        lambda **kwargs: (_ for _ in ()).throw(ApiError("llm_unavailable", 503)),
    )
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    question = f"{SENTINEL_Q} {SENTINEL_SQL}"
    with caplog.at_level(logging.INFO, logger="site_ai_widget"):
        response = _ask(client, key, question=question)
    assert response.status_code == 200
    assert response.json()["mode"] == "none"
    assert SENTINEL_Q not in response.text
    assert SENTINEL_SQL not in response.text
    assert "SELECT" not in response.text
    assert "forbidden_op" not in response.text
    chunks = []
    for record in caplog.records:
        chunks.append(record.getMessage())
        if record.args:
            chunks.append(repr(record.args))
    logged = " ".join(chunks)
    session = response.json()["session_token"]
    assert SENTINEL_Q not in logged
    assert SENTINEL_SQL not in logged
    assert "SELECT" not in logged
    assert session not in logged
    assert key not in logged
    assert "forbidden_op" not in logged


def test_history_of_expired_session_is_empty(client):
    from site_ai_widget.db.pool import connection
    from site_ai_widget.widget.access import hash_session_token

    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    session = _ask(client, key, question="old-conversation").json()["session_token"]
    assert _history(client, key, session).json()["messages"]
    with connection() as conn:
        conn.execute(
            "UPDATE visitor_sessions SET last_seen = now() - interval '25 hours' WHERE token_hash = %s",
            (hash_session_token(session),),
        )
    expired = _history(client, key, session)
    assert expired.status_code == 200
    assert expired.json()["messages"] == []
    assert "old-conversation" not in expired.text


def test_blank_history_token_is_rejected_or_empty(client):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    blank = _history(client, key, "   ")
    assert blank.status_code == 200
    assert blank.json()["messages"] == []


def test_vary_origin_on_denied_widget_responses(client):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    responses = [
        _ask(client, key, origin="https://evil.example"),
        _ask(client, "wk_unknown_example_key_0001"),
        client.options(
            f"/v1/widget/{key}/ask",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        ),
        client.options(
            "/v1/widget/wk_unknown_example_key_0001/ask",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
        ),
    ]
    for response in responses:
        assert response.status_code in (401, 403)
        assert "origin" in response.headers.get("vary", "").lower()
        _assert_no_cors(response)


def test_cors_check_runs_in_thread_pool(client, monkeypatch):
    """The blocking DB lookup must not run on the event loop."""
    from site_ai_widget.api import widget_cors

    seen: list[object] = []
    real = widget_cors.run_in_threadpool

    async def spy(func, *args, **kwargs):
        seen.append(func)
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(widget_cors, "run_in_threadpool", spy)
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    preflight = client.options(
        f"/v1/widget/{key}/ask",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 204
    assert _ask(client, key).status_code == 200
    assert seen == [widget_cors.authorize_widget, widget_cors.authorize_widget]


def test_history_is_rate_limited_per_ip(client, monkeypatch):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    session = _ask(client, key, question="hist").json()["session_token"]
    _tighten(monkeypatch, API_RATE_LIMIT="2")
    assert _history(client, key, session).status_code == 200
    assert _history(client, key, session).status_code == 200
    blocked = _history(client, key, session)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert "hist" not in blocked.text
    # The CORS headers still reach an allowed origin, so the widget can read the 429.
    _assert_cors(blocked, ORIGIN)


def test_history_bad_origin_does_not_spend_rate_limit(client, monkeypatch):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    session = _ask(client, key, question="hist").json()["session_token"]
    _tighten(monkeypatch, API_RATE_LIMIT="1")
    for _ in range(3):
        assert _history(client, key, session, origin="https://evil.example").status_code == 403
    assert _history(client, key, session).status_code == 200


def test_widget_does_not_go_through_the_ask_route_module(client, monkeypatch):
    """ask_service resolves the pipeline itself; patching /ask's globals has no effect."""
    monkeypatch.setattr(
        "site_ai_widget.api.routes.ask.answer_with_sql",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("route module used")),
    )
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    response = _ask(client, key)
    assert response.status_code == 200
    assert response.json()["mode"] == "sql"


def test_lookup_is_rate_limited_before_the_database(client, monkeypatch):
    """Random keys cannot each cost a database lookup without limit."""
    from site_ai_widget.api import widget_cors

    seen: list[str] = []
    real = widget_cors.authorize_widget

    def spy(key, origin):
        seen.append(key)
        return real(key, origin)

    monkeypatch.setattr(widget_cors, "authorize_widget", spy)
    _tighten(monkeypatch, WIDGET_LOOKUP_RATE_LIMIT="2")
    first = _ask(client, "wk_unknown_example_key_0001")
    second = client.options(
        "/v1/widget/wk_unknown_example_key_0002/ask",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert first.status_code == second.status_code == 401
    blocked = _ask(client, "wk_unknown_example_key_0003")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert "Retry-After" in blocked.headers
    assert "origin" in blocked.headers["vary"].lower()
    _assert_no_cors(blocked)
    assert seen == ["wk_unknown_example_key_0001", "wk_unknown_example_key_0002"]


def test_lookup_bucket_is_separate_from_the_api_bucket(client, monkeypatch):
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    session = _ask(client, key, question="hist").json()["session_token"]
    _tighten(monkeypatch, API_RATE_LIMIT="1", WIDGET_LOOKUP_RATE_LIMIT="1000")
    for _ in range(5):
        assert _ask(client, key, origin="https://evil.example").status_code == 403
    assert _history(client, key, session).status_code == 200


def test_denied_request_is_refused_before_the_body_is_parsed(client, monkeypatch):
    """A foreign origin gets 403 even when the body is invalid: no route work runs."""
    calls = _block_ask_service(monkeypatch)
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    bad_body = _ask(client, key, origin="https://evil.example", question="hi", company_id="nope")
    assert bad_body.status_code == 403
    assert bad_body.json()["detail"]["code"] == "origin_not_allowed"
    _assert_no_cors(bad_body)
    not_json = client.post(
        f"/v1/widget/{key}/ask",
        headers={"Origin": "https://evil.example", "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert not_json.status_code == 403
    unknown = client.post(
        "/v1/widget/wk_unknown_example_key_0001/history",
        headers={"Origin": ORIGIN},
        json={"session_token": ""},
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"]["code"] == "widget_key_invalid"
    _assert_no_cors(unknown)
    assert calls == []


def test_widget_response_never_includes_table_names(client, monkeypatch):
    """Table names are schema text: the panel may show them, a visitor never does."""
    monkeypatch.setattr(
        "site_ai_widget.sqlqa.pipeline.answer_with_sql",
        lambda *a, **k: SqlAnswer(
            status="ok",
            columns=["name"],
            rows=[["Azeite"]],
            row_count=1,
            truncated=False,
            attempts=1,
            tables=["secret_catalog_table"],
        ),
    )
    token, _company = _register(client)
    key = _make_key(client, token)["public_key"]
    response = _ask(client, key, question="tem azeite?")
    assert response.status_code == 200
    assert "secret_catalog_table" not in response.text
    assert response.json().get("tables") is None
