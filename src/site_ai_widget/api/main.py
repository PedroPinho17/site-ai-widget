"""FastAPI application entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from site_ai_widget.api.config import get_settings
from site_ai_widget.api.errors import ApiError, api_error_handler
from site_ai_widget.api.routes import auth_routes, company, health, widget_keys
from site_ai_widget.db import pool as db_pool


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast if secrets are missing / invalid (pool opens lazily on first query)
    get_settings()
    yield
    db_pool.close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="site-ai-widget",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url=None,
    )
    app.add_exception_handler(ApiError, api_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "validation_error",
                    "message": "Invalid request.",
                    # Only stable, JSON-safe fields: pydantic's "ctx" can hold
                    # exception objects (not serialisable → 500) and "input"
                    # would echo request data such as passwords back.
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
    return app


# Instantiated at import so `uvicorn site_ai_widget.api.main:app` works;
# get_settings() raises if JWT_SECRET / DATABASE_URL are missing.
app = create_app()
