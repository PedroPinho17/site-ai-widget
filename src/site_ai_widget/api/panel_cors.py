"""CORS for the admin panel origin list.

Applies to every route except ``/v1/widget/*`` (those stay on
:class:`WidgetCorsMiddleware`). Reflects the request Origin when it is in
``PANEL_ORIGINS``. Never ``*``, never credentials. Every response it
handles carries ``Vary: Origin``.
"""
from __future__ import annotations

from site_ai_widget.api.config import get_settings

_VARY = (b"vary", b"Origin")
_METHODS = b"GET, POST, PUT, PATCH, DELETE, OPTIONS"
_HEADERS = b"Authorization, Content-Type, Accept"
_MAX_AGE = b"600"


def _header_bytes(scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value
    return None


def _cors(origin: bytes) -> list[tuple[bytes, bytes]]:
    return [
        (b"access-control-allow-origin", origin),
        _VARY,
        (b"access-control-allow-methods", _METHODS),
        (b"access-control-allow-headers", _HEADERS),
        (b"access-control-max-age", _MAX_AGE),
    ]


async def _send(send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
    payload = list(headers)
    if body:
        payload.append((b"content-type", b"application/json"))
        payload.append((b"content-length", str(len(body)).encode()))
    await send({"type": "http.response.start", "status": status, "headers": payload})
    await send({"type": "http.response.body", "body": body})


class PanelCorsMiddleware:
    """Answer panel preflight and attach CORS headers on PANEL_ORIGINS."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path.startswith("/v1/widget/"):
            await self.app(scope, receive, send)
            return

        origin_bytes = _header_bytes(scope, b"origin")
        origin_text = origin_bytes.decode("latin-1") if origin_bytes is not None else None
        allowed = bool(origin_text) and origin_text in get_settings().panel_origin_set()

        if scope.get("method") == "OPTIONS" and origin_bytes is not None:
            headers = _cors(origin_bytes) if allowed else [_VARY]
            await _send(send, 204, headers, b"")
            return

        # Vary on every response, Origin or not, so a shared cache never hands a
        # no-Origin copy to the panel or a panel copy to another origin.
        cors = _cors(origin_bytes) if allowed and origin_bytes is not None else [_VARY]

        async def send_cors(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(cors)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_cors)
