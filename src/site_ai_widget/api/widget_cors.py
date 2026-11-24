"""CORS for ``/v1/widget/*`` only.

Reflects the request Origin when that origin is allowlisted for the key's
company. Never ``*``, never credentials, and no global CORS middleware.

The key/origin check reads the database with a blocking driver, so it runs in
the thread pool — never on the event loop. Every widget response carries
``Vary: Origin`` (allowed or not) so a shared cache never serves one origin's
answer to another.
"""
from __future__ import annotations

import json
import re

from starlette.concurrency import run_in_threadpool

from site_ai_widget.api.errors import ApiError
from site_ai_widget.widget.access import authorize_widget

_PATH = re.compile(r"^/v1/widget/([^/]+)/(ask|history)$")


def _header_bytes(scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value
    return None


_VARY = (b"vary", b"Origin")


def _cors(origin: bytes) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin),
        _VARY,
        (b"access-control-allow-methods", b"POST"),
        (b"access-control-allow-headers", b"Content-Type"),
        (b"access-control-max-age", b"600"),
    ]


def _error_body(exc: ApiError) -> bytes:
    return json.dumps({"detail": {"code": exc.code, "message": exc.message}}).encode()


async def _send(send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
    payload = list(headers)
    if body:
        payload.append((b"content-type", b"application/json"))
        payload.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": payload})
    await send({"type": "http.response.body", "body": body})


class WidgetCorsMiddleware:
    """Answer widget preflight and attach CORS headers on allowlisted origins."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        match = _PATH.match(scope.get("path") or "")
        if not match:
            await self.app(scope, receive, send)
            return

        key = match.group(1)
        origin_bytes = _header_bytes(scope, b"origin")
        origin_text = origin_bytes.decode("latin-1") if origin_bytes is not None else None
        denial: ApiError | None
        try:
            await run_in_threadpool(authorize_widget, key, origin_text)
        except ApiError as exc:
            denial = exc
        else:
            denial = None

        if scope.get("method") == "OPTIONS":
            if denial is None and origin_bytes is not None:
                await _send(send, 204, _cors(origin_bytes), b"")
            elif denial is not None:
                await _send(send, denial.status, [_VARY], _error_body(denial))
            else:
                missing = ApiError("origin_not_allowed", 403)
                await _send(send, missing.status, [_VARY], _error_body(missing))
            return

        allowed = denial is None and origin_bytes is not None
        cors = _cors(origin_bytes) if allowed else [_VARY]

        async def send_cors(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(cors)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_cors)
