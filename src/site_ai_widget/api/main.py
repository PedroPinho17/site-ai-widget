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
from site_ai_widget.api.routes import ask_docs, auth_routes, company, documents, health, widget_keys
from site_ai_widget.db import pool as db_pool
from site_ai_widget.docs.storage import upload_root
from site_ai_widget.docs.worker import recover_stuck_documents, shutdown_worker

log = logging.getLogger("site_ai_widget")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast if secrets are missing / invalid (pool opens lazily on first query)
    get_settings()
    upload_root()
    try:
        recover_stuck_documents()
    except Exception:
        log.warning("Could not recover stuck documents (DB may be down)", exc_info=True)
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
    return app


# Instantiated at import so `uvicorn site_ai_widget.api.main:app` works;
# get_settings() raises if JWT_SECRET / DATABASE_URL are missing.
app = create_app()
