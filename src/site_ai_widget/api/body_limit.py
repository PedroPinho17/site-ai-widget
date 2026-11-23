"""ASGI middleware: cap request body size before Starlette spools it.

Starlette parses (and spools to disk) a whole multipart body before the route
sees ``UploadFile``, so the per-file limit alone does not stop a client from
streaming gigabytes. This middleware rejects oversized bodies up front via
``Content-Length`` and, for chunked bodies, stops reading as soon as the
running total passes the cap.
"""
from __future__ import annotations

import json
from collections.abc import Callable

Scope = dict
ASGIApp = Callable

ONE_MIB = 1024 * 1024


class BodyTooLarge(Exception):
    pass


def _payload(limit_mb: int) -> bytes:
    return json.dumps(
        {
            "detail": {
                "code": "upload_too_large",
                "message": f"Request body exceeds the maximum size of {limit_mb} MB.",
                "n": limit_mb,
            }
        }
    ).encode()


class BodySizeLimitMiddleware:
    """Per-request cap: ``limit_for(scope)`` returns the max bytes for that request."""

    def __init__(self, app: ASGIApp, limit_for: Callable[[Scope], int]):
        self.app = app
        self.limit_for = limit_for

    async def _reject(self, send, limit: int) -> None:
        body = _payload(max(1, limit // ONE_MIB))
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope)

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > limit:
                    await self._reject(send, limit)
                    return
                break

        state = {"received": 0, "exceeded": False, "started": False}

        async def limited_receive():
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > limit:
                    state["exceeded"] = True
                    raise BodyTooLarge()
            return message

        async def guarded_send(message):
            if state["exceeded"]:
                # Swallow whatever error response the app produced; we send 413.
                return
            if message["type"] == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            # Whatever the app raised while the body was cut short (our
            # BodyTooLarge, or a parser error wrapping it) becomes a 413.
            if not state["exceeded"]:
                raise
        if state["exceeded"] and not state["started"]:
            await self._reject(send, limit)


def default_limit_for(upload_bytes: int, *, other_bytes: int = ONE_MIB) -> Callable[[Scope], int]:
    """Upload route gets ``upload_bytes`` + 1 MiB multipart overhead; everything else 1 MiB."""
    upload_cap = upload_bytes + ONE_MIB

    def limit_for(scope: Scope) -> int:
        path = scope.get("path", "")
        if scope.get("method") == "POST" and path.rstrip("/") == "/documents":
            return upload_cap
        return other_bytes

    return limit_for
