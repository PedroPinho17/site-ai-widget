"""Register, login, me, change-password."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from psycopg.errors import UniqueViolation

from site_ai_widget.api.auth import (
    BUMP_TOKEN_VERSION,
    authenticate,
    check_password,
    create_token,
    current_user,
    hash_password,
    public_user,
    validate_password,
)
from site_ai_widget.api.deps import require_api_rate, require_auth_rate
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from site_ai_widget.db.pool import connection

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse)
def register(
    body: RegisterRequest,
    _ip: str = Depends(require_auth_rate),
) -> TokenResponse:
    validate_password(body.password)
    email = str(body.email).strip().lower()
    pw_hash = hash_password(body.password)
    try:
        with connection() as conn:
            company = conn.execute(
                """
                INSERT INTO companies (name, slug, default_locale)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (body.company_name.strip(), body.company_slug, body.default_locale),
            ).fetchone()
            company_id = company[0]
            user_row = conn.execute(
                """
                INSERT INTO users (company_id, email, password_hash, role)
                VALUES (%s, %s, %s, 'admin')
                RETURNING id, company_id, email, role, token_version
                """,
                (company_id, email, pw_hash),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO widget_settings (company_id, locale)
                VALUES (%s, %s)
                """,
                (company_id, body.default_locale),
            )
    except UniqueViolation as exc:
        constraint = getattr(exc, "diag", None)
        name = getattr(constraint, "constraint_name", "") or ""
        if "slug" in name or "companies_slug" in name:
            raise ApiError("slug_taken", 409) from None
        raise ApiError("email_taken", 409) from None

    user = {
        "id": user_row[0],
        "company_id": user_row[1],
        "email": user_row[2],
        "role": user_row[3],
        "token_version": int(user_row[4]),
    }
    return TokenResponse(access_token=create_token(user), user=public_user(user))


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    _ip: str = Depends(require_auth_rate),
) -> TokenResponse:
    user = authenticate(str(body.email), body.password)
    if not user:
        raise ApiError("bad_login", 401)
    return TokenResponse(access_token=create_token(user), user=public_user(user))


@router.get("/me", response_model=UserResponse)
def me(
    user: dict = Depends(current_user),
    _ip: str = Depends(require_api_rate),
) -> UserResponse:
    pub = public_user(user)
    return UserResponse(**pub)


@router.post("/change-password", response_model=TokenResponse)
def change_password(
    body: ChangePasswordRequest,
    user: dict = Depends(current_user),
    _ip: str = Depends(require_api_rate),
) -> TokenResponse:
    """Change the password and bump ``token_version``.

    Every previously issued JWT (including the one used for this call) stops
    working; the response carries a fresh token so this client stays signed in.
    """
    validate_password(body.new_password)
    if body.new_password == body.current_password:
        raise ApiError("pw_same", 400)
    with connection() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = %s",
            (str(user["id"]),),
        ).fetchone()
        if not row or not check_password(body.current_password, row[0]):
            raise ApiError("pw_wrong_current", 400)
        new_hash = hash_password(body.new_password)
        updated = conn.execute(
            f"""
            UPDATE users
            SET password_hash = %s, {BUMP_TOKEN_VERSION}
            WHERE id = %s
            RETURNING id, company_id, email, role, token_version
            """,
            (new_hash, str(user["id"])),
        ).fetchone()
    refreshed = {
        "id": updated[0],
        "company_id": updated[1],
        "email": updated[2],
        "role": updated[3],
        "token_version": int(updated[4]),
    }
    return TokenResponse(access_token=create_token(refreshed), user=public_user(refreshed))
