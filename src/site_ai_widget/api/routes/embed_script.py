"""Serve the embeddable widget script. The file is static: no database and no secrets."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter(tags=["widget"])

_EMBED_JS = Path(__file__).resolve().parents[2] / "widget" / "embed.js"
# Short enough that a redeploy shows up quickly, long enough to spare repeat views.
_CACHE_CONTROL = "public, max-age=300"
_HEADERS = {
    "Cache-Control": _CACHE_CONTROL,
    # The file holds non-ASCII text; a host page in another charset must not
    # re-decode it.
    "Content-Type": "application/javascript; charset=utf-8",
    "X-Content-Type-Options": "nosniff",
    # Host pages that set Cross-Origin-Embedder-Policy can still load it.
    "Cross-Origin-Resource-Policy": "cross-origin",
}


@router.get("/embed.js")
def embed_script() -> Response:
    return Response(content=_EMBED_JS.read_bytes(), headers=_HEADERS)
