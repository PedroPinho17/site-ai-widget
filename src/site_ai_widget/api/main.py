"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from site_ai_widget.api.body_limit import BodySizeLimitMiddleware, default_limit_for
from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError, api_error_handler
from site_ai_widget.api.routes import (
    ask,
    ask_docs,
    auth_routes,
    company,
    db_connection,
    documents,
    health,
    widget_keys,
)
from site_ai_widget.db import pool as db_pool
from site_ai_widget.docs.storage import upload_root
from site_ai_widget.docs.worker import recover_stuck_documents, shutdown_worker

log = logging.getLogger("site_ai_widget")

# Lifespan runs once per process in production; tests create many apps — cache the
# probe so a downed Postgres does not cost connect_timeout on every TestClient.
_db_reachable_cache: bool | None = None


def reset_db_reachable_cache() -> None:
    global _db_reachable_cache
    _db_reachable_cache = None


def _db_reachable(database_url: str, *, connect_timeout: float = 1.0) -> bool:
    """Cheap probe so lifespan does not open the pool when Postgres is down.

    Uses a short TCP ``connect_timeout`` (not the pool wait timeout).
    """
    global _db_reachable_cache
    if _db_reachable_cache is not None:
        return _db_reachable_cache
    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=connect_timeout) as conn:
            conn.execute("SELECT 1")
        _db_reachable_cache = True
    except Exception:
        _db_reachable_cache = False
    return _db_reachable_cache


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast if secrets are missing / invalid (pool opens lazily on first query)
    settings = get_settings()
    upload_root()
    if _db_reachable(settings.database_url):
        try:
            recover_stuck_documents()
        except Exception:
            log.warning("Could not recover stuck documents", exc_info=True)
    else:
        log.warning("Postgres unreachable at startup; skipping stuck-document recovery")
    yield
    shutdown_worker()
    db_pool.close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="site-ai-widget",
        version="0.2.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
    )
    app.add_exception_handler(ApiError, api_error_handler)
    # Reject oversized bodies before multipart parsing spools them to disk.
    app.add_middleware(
        BodySizeLimitMiddleware,
        limit_for=default_limit_for(settings.max_upload_bytes),
    )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation_error",
                    "message": "Invalid request.",
                    "errors": [
                        {
                            "loc": list(err.get("loc", ())),
                            "msg": str(err.get("msg", "")),
                            "type": str(err.get("type", "")),
                        }
                        for err in exc.errors()
                    ],
                }
            },
        )

    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(company.router)
    app.include_router(widget_keys.router)
    app.include_router(documents.router)
    app.include_router(ask_docs.router)
    app.include_router(ask.router)
    app.include_router(db_connection.router)
    return app


# Instantiated at import so `uvicorn site_ai_widget.api.main:app` works;
# get_settings() raises if JWT_SECRET / DATABASE_URL are missing.
app = create_app()
