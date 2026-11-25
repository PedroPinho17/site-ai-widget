"""Security headers on every API response.

Set as defaults: a route that already chose a value (``embed.js`` cache and CORP
headers, the PDF routes' ``Cache-Control``) keeps it.

* ``X-Content-Type-Options: nosniff`` — a JSON body or a PDF is never run as a
  script or rendered as HTML.
* ``Referrer-Policy: no-referrer`` — a signed PDF URL (``?exp=…&sig=…``) is never
  sent to another site when a link inside the PDF is opened.
* ``frame-ancestors 'none'`` / ``X-Frame-Options: DENY`` — no page can frame the
  API (the panel previews PDFs from a ``blob:`` URL, never by framing the API).
* JSON responses also get ``default-src 'none'`` (they load nothing) and
  ``Cache-Control: no-store`` (they may carry a session token or company data).
  PDFs, ``embed.js`` and the optional ``/docs`` page keep their own needs.

Outermost middleware, so answers written by the CORS middlewares (preflight,
refused origins, rate limits) get the headers too.
"""
from __future__ import annotations

_COMMON: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)
_CSP_JSON = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
_CSP_OTHER = b"frame-ancestors 'none'"


def _is_json(headers: list[tuple[bytes, bytes]]) -> bool:
    for name, value in headers:
        if name.lower() == b"content-type":
            return value.split(b";", 1)[0].strip().lower() == b"application/json"
    return False


def secure_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """``headers`` plus every missing security header (existing values win)."""
    out = list(headers)
    present = {name.lower() for name, _ in out}
    json_body = _is_json(out)
    extra = list(_COMMON)
    extra.append((b"content-security-policy", _CSP_JSON if json_body else _CSP_OTHER))
    if json_body:
        extra.append((b"cache-control", b"no-store"))
    for name, value in extra:
        if name not in present:
            out.append((name, value))
    return out


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_secure(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": secure_headers(list(message.get("headers") or []))}
            await send(message)

        await self.app(scope, receive, send_secure)
