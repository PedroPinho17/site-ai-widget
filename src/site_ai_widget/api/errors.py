"""Stable API error shape: ``{"detail": {"code": ..., "message": ...}}``."""

import contextlib

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Business error with a stable code and HTTP status."""

    def __init__(self, code: str, status: int = 400, message: str | None = None, **params):
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.params = params
        self.message = message or MESSAGES.get(code, code)
        self.headers: dict[str, str] | None = None
        if params and "{" in self.message:
            with contextlib.suppress(KeyError, IndexError):
                self.message = self.message.format(**params)


MESSAGES = {
    "session_invalid": "Invalid or expired session.",
    "bad_login": "Wrong email or password.",
    "rate_limited": "Too many requests. Try again shortly.",
    "email_taken": "That email is already registered.",
    "slug_taken": "That company slug is already taken.",
    "not_found": "Resource not found.",
    "forbidden": "This action requires an admin.",
    "pw_too_short": "Password must be at least {n} characters.",
    "pw_too_long": "Password is too long.",
    "pw_wrong_current": "Current password is wrong.",
    "pw_same": "New password must differ from the current one.",
    "bad_origin": "Invalid origin URL.",
    "validation_error": "Invalid request.",
}


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
    )
