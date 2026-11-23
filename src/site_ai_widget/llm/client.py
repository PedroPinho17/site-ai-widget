"""Groq OpenAI-compatible client with optional fallback key on HTTP 429."""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from openai import APIStatusError, OpenAI, RateLimitError

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError

log = logging.getLogger("site_ai_widget")

MIN_COOLDOWN = 300.0
_WAIT = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.I)


def is_quota_error(error: Exception) -> bool:
    if isinstance(error, RateLimitError):
        return True
    if isinstance(error, APIStatusError):
        text = str(error).lower()
        return error.status_code == 429 or "quota" in text or "tokens per day" in text
    return False


def retry_after(error: Exception) -> float:
    match = _WAIT.search(str(error))
    if not match or not any(match.groups()):
        return MIN_COOLDOWN
    h, m, s = (float(g) if g else 0.0 for g in match.groups())
    return max(MIN_COOLDOWN, h * 3600 + m * 60 + s)


class FallbackClient:
    def __init__(
        self,
        keys: Sequence[str],
        base_url: str | None = None,
        *,
        timeout: float = 60.0,
        factory: Callable[..., OpenAI] = OpenAI,
        clock: Callable[[], float] = time.monotonic,
    ):
        keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        if not keys:
            raise ApiError("llm_not_configured", 503)
        self._clients = [
            factory(
                api_key=k,
                base_url=base_url,
                timeout=timeout,
                **({"max_retries": 0} if i < len(keys) - 1 else {}),
            )
            for i, k in enumerate(keys)
        ]
        self._blocked_until = [0.0] * len(keys)
        self._clock = clock
        self._lock = threading.Lock()

    def chat_completions_create(self, **kwargs: Any):
        last: Exception | None = None
        for i in self._order():
            try:
                return self._clients[i].chat.completions.create(**kwargs)
            except Exception as e:
                if not is_quota_error(e):
                    raise
                last = e
                wait = retry_after(e)
                with self._lock:
                    self._blocked_until[i] = self._clock() + wait
                if len(self._clients) > 1:
                    log.warning("LLM quota hit on key %s; cooling down %.0fs", i + 1, wait)
        raise last  # type: ignore[misc]

    def _order(self) -> list[int]:
        now = self._clock()
        with self._lock:
            ready = [i for i, until in enumerate(self._blocked_until) if until <= now]
            return ready + [i for i in range(len(self._clients)) if i not in ready]


_client: FallbackClient | None = None
_client_lock = threading.Lock()


def reset_llm_client() -> None:
    global _client
    with _client_lock:
        _client = None


def get_llm_client() -> FallbackClient:
    global _client
    settings = get_settings()
    keys = settings.groq_keys()
    if not keys:
        raise ApiError("llm_not_configured", 503)
    with _client_lock:
        if _client is None:
            _client = FallbackClient(
                keys,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout_seconds,
            )
        return _client


def _safe_error_label(error: Exception) -> str:
    """Class name + HTTP status only: provider messages may echo request data."""
    status = getattr(error, "status_code", None)
    return f"{type(error).__name__}" + (f" status={status}" if status else "")


def complete_chat(messages: list[dict[str, str]], *, temperature: float = 0) -> str:
    """Call the LLM. Every provider failure becomes a stable ``ApiError``.

    * all keys rate-limited / out of quota → ``llm_unavailable`` 503 + Retry-After
    * any other provider / network / malformed-response error → ``llm_unavailable`` 503

    Logs never include the prompt, question, document text or API keys.
    """
    settings = get_settings()
    client = get_llm_client()
    try:
        response = client.chat_completions_create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
        )
        content = response.choices[0].message.content
    except ApiError:
        raise
    except Exception as exc:
        quota = is_quota_error(exc)
        log.warning("LLM call failed: %s", _safe_error_label(exc))
        err = ApiError("llm_unavailable", 503)
        if quota:
            err.headers = {"Retry-After": str(int(retry_after(exc)))}
        raise err from None
    return (content or "").strip()
