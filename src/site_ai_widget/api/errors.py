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
    "widget_key_invalid": "Invalid widget key.",
    "origin_not_allowed": "Origin is not allowed.",
    "validation_error": "Invalid request.",
    "not_pdf": "File is not a PDF (missing %PDF- signature).",
    "upload_too_large": "File exceeds the maximum size of {n} MB.",
    "pdf_encrypted": "Encrypted PDFs are not supported.",
    "pdf_too_many_pages": "PDF has more than {n} pages.",
    "pdf_corrupt": "Could not read this PDF.",
    "document_duplicate": "This file was already uploaded.",
    "question_too_long": "Question exceeds {n} characters.",
    "llm_not_configured": "Document Q&A is not configured (missing LLM API key).",
    "llm_unavailable": "The answer service is temporarily unavailable. Try again shortly.",
    "connections_not_configured": "Database connections are not configured (missing CONNECTIONS_KEY).",
    "connection_unreadable": "The saved connection cannot be read. Save it again.",
    "db_host_invalid": "Invalid database host or port.",
    "db_host_unresolvable": "Could not resolve the database host.",
    "db_host_not_allowed": "That host is not allowed (private, local or internal address).",
    "db_unreachable": "Could not reach a PostgreSQL server at that host and port.",
    "db_auth_failed": "Wrong username or password for the database.",
    "db_not_found": "That database does not exist.",
    "db_version_unsupported": "PostgreSQL 13 or newer is required.",
    "db_user_can_write": "This user can change the database. Use a user that can only read (SELECT).",
    "db_no_readable_tables": "This user cannot read any table.",
    "db_connection_failed": "The database connection test failed.",
    "db_schema_too_large": "This database has too many tables or columns. Use a user that only sees the tables you need.",
    "db_timeout": "The database took too long to answer. Try again later.",
    "db_busy": "Too many database checks are running right now. Try again in a moment.",
    "db_schema_invalid": "The database sent table or column details that a real PostgreSQL never sends.",
    "db_conflict_retry": "Another change hit the same tables at the same time. Try again.",
    "schema_suggest_failed": "The AI returned an invalid answer. Try again.",
    "schema_not_present": "This table or column no longer exists in your database.",
    "column_sensitive": "A sensitive column cannot be approved. Unmark it as sensitive first.",
}


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    detail: dict = {"code": exc.code, "message": exc.message}
    # Safe extra fields for clients (never passwords / document text)
    for key in ("document_id", "n"):
        if key in exc.params:
            detail[key] = exc.params[key]
    return JSONResponse(
        status_code=exc.status,
        content={"detail": detail},
        headers=exc.headers,
    )
