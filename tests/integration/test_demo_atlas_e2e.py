"""End-to-end Mercearia Atlas answers through the running app API.

Hits the Docker stack (widget key + ``/assistant/test``) so the client DB host
``clientdb`` resolves. Enable with ``LIVE_DEMO=1``. Never reads ``.env``; the
panel password must already be in the process environment for the JWT path.
"""
from __future__ import annotations

import os
import re
import time

import pytest

LIVE_BASE = os.environ.get("LIVE_DEMO_BASE", "http://127.0.0.1:8002").rstrip("/")
ATLAS_EMAIL = "admin@mercearia-atlas.example"
WIDGET_KEY = "wk_demo_mercearia_atlas"
WIDGET_ORIGIN = "http://127.0.0.1:5500"

QUESTIONS = (
    "Qual o preço do azeite?",
    "Que queijos têm?",
    "Como posso contactar-vos?",
)


def _live_enabled() -> bool:
    return os.environ.get("LIVE_DEMO", "").strip().lower() in ("1", "true", "yes")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Set LIVE_DEMO=1 to hit the running Docker app (clientdb is only reachable there)",
    ),
]


def _assert_azeite(answer: str, *, mode: str | None = None) -> None:
    if mode is not None:
        assert mode == "sql"
    assert "8,49" in answer
    assert "8.49" not in answer
    assert "€" in answer
    assert re.search(r"(?i)azeite", answer)


def _assert_queijos(answer: str, *, mode: str | None = None) -> None:
    if mode is not None:
        assert mode == "sql"
    lower = answer.casefold()
    assert "queijo da serra da estrela" in lower
    assert "queijo de ovelha curado" in lower
    assert re.search(r"\d,\d{2}\s*€", answer)
    assert not re.search(r"\d\.\d{2}\s*€", answer)
    assert "manual" not in lower


def _assert_contact(answer: str) -> None:
    assert "Amendoeiras 48" in answer
    assert "Amendoeiras48" not in answer
    assert "\u202f" not in answer
    assert "\u00a0" not in answer


_CHECKS = (
    _assert_azeite,
    _assert_queijos,
    _assert_contact,
)


def _post_with_retry(client, method: str, url: str, **kwargs):
    """Retry on rate limit (shared demo key / IP bucket)."""
    response = None
    for attempt in range(4):
        response = client.request(method, url, **kwargs)
        if response.status_code != 429:
            return response
        time.sleep(2.0 * (attempt + 1))
    assert response is not None
    return response


def test_live_widget_and_assistant_three_atlas_questions():
    import httpx

    password = os.environ.get("DEMO_PASSWORD_ATLAS")
    with httpx.Client(base_url=LIVE_BASE, timeout=90.0) as client:
        headers = {"Origin": WIDGET_ORIGIN}
        for question, check in zip(QUESTIONS, _CHECKS, strict=True):
            r = _post_with_retry(
                client,
                "POST",
                f"/v1/widget/{WIDGET_KEY}/ask",
                headers=headers,
                json={"question": question, "lang": "pt"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            if check is _assert_azeite or check is _assert_queijos:
                check(body.get("answer") or "", mode=body.get("mode"))
            else:
                check(body.get("answer") or "")
            if check is _assert_queijos:
                assert not body.get("sources")
            time.sleep(0.5)

        if not password:
            pytest.skip("DEMO_PASSWORD_ATLAS is not set in the process environment")

        login = _post_with_retry(
            client,
            "POST",
            "/auth/login",
            json={"email": ATLAS_EMAIL, "password": password},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        for question, check in zip(QUESTIONS, _CHECKS, strict=True):
            r = _post_with_retry(
                client,
                "POST",
                "/assistant/test",
                headers=auth,
                json={"question": question, "lang": "pt"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            if check is _assert_azeite or check is _assert_queijos:
                check(body["answer"], mode=body["mode"])
            else:
                check(body["answer"])
            time.sleep(0.5)
