"""Groq fallback-on-429 and stable error mapping (no network, no Postgres)."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.llm import client as llm

QUESTION = "Pergunta secreta sobre o horario de domingo?"
EXCERPT = "Conteudo confidencial do documento XYZ-123"
KEY_A = "gsk_test_primary_key_aaaaaaaaaaaaaaaa"
KEY_B = "gsk_test_fallback_key_bbbbbbbbbbbbbbb"

_REQ = httpx.Request("POST", "https://llm.invalid/v1/chat/completions")


def _status_error(cls, status: int, message: str = "provider error"):
    return cls(message, response=httpx.Response(status, request=_REQ), body=None)


def _ok(text: str = "Aberto ao domingo [1]."):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeOpenAI:
    """Stands in for ``openai.OpenAI``; records which key served each call."""

    calls: list[str] = []
    behaviour: dict[str, object] = {}

    def __init__(self, *, api_key, base_url=None, timeout=None, **_):
        self.api_key = api_key
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        FakeOpenAI.calls.append(self.api_key)
        outcome = FakeOpenAI.behaviour.get(self.api_key)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or _ok()


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeOpenAI.calls = []
    FakeOpenAI.behaviour = {}
    yield
    llm.reset_llm_client()


def _client(clock=lambda: 0.0):
    return llm.FallbackClient([KEY_A, KEY_B], factory=FakeOpenAI, clock=clock)


def test_fallback_used_on_429():
    FakeOpenAI.behaviour[KEY_A] = _status_error(openai.RateLimitError, 429, "Rate limit")
    resp = _client().chat_completions_create(model="m", messages=[])
    assert resp.choices[0].message.content.startswith("Aberto")
    assert FakeOpenAI.calls == [KEY_A, KEY_B]


def test_primary_cooled_down_after_429():
    now = [0.0]
    c = _client(clock=lambda: now[0])
    FakeOpenAI.behaviour[KEY_A] = _status_error(openai.RateLimitError, 429, "try again in 1m")
    c.chat_completions_create(model="m", messages=[])
    FakeOpenAI.calls.clear()
    FakeOpenAI.behaviour.pop(KEY_A)
    c.chat_completions_create(model="m", messages=[])
    assert FakeOpenAI.calls == [KEY_B]  # primary still cooling down
    now[0] += llm.MIN_COOLDOWN + 1
    FakeOpenAI.calls.clear()
    c.chat_completions_create(model="m", messages=[])
    assert FakeOpenAI.calls == [KEY_A]


@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.InternalServerError, 500),
        _status_error(openai.BadRequestError, 400),
        _status_error(openai.AuthenticationError, 401),
        openai.APIConnectionError(request=_REQ),
        openai.APITimeoutError(request=_REQ),
    ],
    ids=["500", "400", "401", "connection", "timeout"],
)
def test_no_fallback_on_non_quota_errors(error):
    FakeOpenAI.behaviour[KEY_A] = error
    with pytest.raises(type(error)):
        _client().chat_completions_create(model="m", messages=[])
    assert FakeOpenAI.calls == [KEY_A]


def _messages():
    return [
        {"role": "system", "content": "system"},
        {"role": "user", "content": f"<excerpt>{EXCERPT}</excerpt>\nQuestion: {QUESTION}"},
    ]


@pytest.mark.parametrize(
    ("primary", "fallback", "retry_after"),
    [
        (_status_error(openai.InternalServerError, 503, f"echo {QUESTION}"), None, False),
        (openai.APIConnectionError(request=_REQ), None, False),
        (
            _status_error(openai.RateLimitError, 429, "try again in 7m"),
            _status_error(openai.RateLimitError, 429, "try again in 7m"),
            True,
        ),
    ],
    ids=["5xx", "connection", "all-keys-429"],
)
def test_complete_chat_maps_errors_to_llm_unavailable(
    monkeypatch, caplog, primary, fallback, retry_after
):
    FakeOpenAI.behaviour[KEY_A] = primary
    if fallback is not None:
        FakeOpenAI.behaviour[KEY_B] = fallback
    monkeypatch.setattr(llm, "get_llm_client", lambda: _client())

    with caplog.at_level(logging.DEBUG, logger="site_ai_widget"), pytest.raises(ApiError) as exc:
        llm.complete_chat(_messages())
    assert exc.value.code == "llm_unavailable"
    assert exc.value.status == 503
    if retry_after:
        assert exc.value.headers and int(exc.value.headers["Retry-After"]) >= 300
    logged = caplog.text
    for secret in (QUESTION, EXCERPT, KEY_A, KEY_B):
        assert secret not in logged


def test_complete_chat_maps_malformed_response(monkeypatch):
    FakeOpenAI.behaviour[KEY_A] = SimpleNamespace(choices=[])
    monkeypatch.setattr(llm, "get_llm_client", lambda: _client())
    with pytest.raises(ApiError) as exc:
        llm.complete_chat(_messages())
    assert exc.value.code == "llm_unavailable"


def test_complete_chat_success(monkeypatch):
    monkeypatch.setattr(llm, "get_llm_client", lambda: _client())
    assert llm.complete_chat(_messages()) == "Aberto ao domingo [1]."
