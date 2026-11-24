"""Own-company read/update and widget settings."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from site_ai_widget.api.deps import panel_user, require_admin
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.schemas import (
    CompanyResponse,
    CompanyUpdate,
    WidgetSettingsResponse,
    WidgetSettingsUpdate,
)
from site_ai_widget.db.pool import connection

router = APIRouter(prefix="/company", tags=["company"])


def _fetch_company(company_id) -> CompanyResponse:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, slug, default_locale, created_at
            FROM companies WHERE id = %s
            """,
            (str(company_id),),
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return CompanyResponse(
        id=row[0], name=row[1], slug=row[2], default_locale=row[3], created_at=row[4]
    )


@router.get("", response_model=CompanyResponse)
def get_company(user: dict = Depends(panel_user)) -> CompanyResponse:
    return _fetch_company(user["company_id"])


@router.patch("", response_model=CompanyResponse)
def update_company(
    body: CompanyUpdate,
    user: dict = Depends(require_admin),
) -> CompanyResponse:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return _fetch_company(user["company_id"])
    sets = []
    params: list = []
    for col in ("name", "default_locale"):
        if col in changes:
            sets.append(f"{col} = %s")
            params.append(changes[col])
    params.append(str(user["company_id"]))
    with connection() as conn:
        row = conn.execute(
            f"""
            UPDATE companies SET {", ".join(sets)}
            WHERE id = %s
            RETURNING id, name, slug, default_locale, created_at
            """,
            params,
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return CompanyResponse(
        id=row[0], name=row[1], slug=row[2], default_locale=row[3], created_at=row[4]
    )


_SETTINGS_COLS = (
    "company_id, primary_color, greeting, locale, position, assistant_name, currency, "
    "show_suggestions, show_fullscreen, allow_visitor_documents, "
    "conversation_retention_days, suggested_questions"
)


def _settings_from_row(row) -> WidgetSettingsResponse:
    return WidgetSettingsResponse(
        company_id=row[0],
        primary_color=row[1],
        greeting=row[2],
        locale=row[3],
        position=row[4],
        assistant_name=row[5],
        currency=row[6],
        show_suggestions=bool(row[7]),
        show_fullscreen=bool(row[8]),
        allow_visitor_documents=bool(row[9]),
        conversation_retention_days=int(row[10] or 30),
        suggested_questions=list(row[11] or []),
    )


@router.get("/widget-settings", response_model=WidgetSettingsResponse)
def get_widget_settings(user: dict = Depends(panel_user)) -> WidgetSettingsResponse:
    with connection() as conn:
        row = conn.execute(
            f"SELECT {_SETTINGS_COLS} FROM widget_settings WHERE company_id = %s",
            (str(user["company_id"]),),
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return _settings_from_row(row)


@router.patch("/widget-settings", response_model=WidgetSettingsResponse)
def update_widget_settings(
    body: WidgetSettingsUpdate,
    user: dict = Depends(require_admin),
) -> WidgetSettingsResponse:
    # currency may be cleared (null). suggested_questions null means "clear"
    # (the column is NOT NULL). Other fields ignore null.
    raw = body.model_dump(exclude_unset=True)
    nullable = {"currency", "suggested_questions"}
    changes = {key: value for key, value in raw.items() if value is not None or key in nullable}
    if "suggested_questions" in changes:
        questions = changes["suggested_questions"] or []
        changes["suggested_questions"] = questions
        # Admin-typed questions survive document upload/delete; an empty list
        # hands the cache back to automatic generation.
        changes["suggestions_manual"] = bool(questions)
    if not changes:
        return get_widget_settings(user)
    allowed = {
        "primary_color",
        "greeting",
        "locale",
        "position",
        "assistant_name",
        "currency",
        "show_suggestions",
        "show_fullscreen",
        "allow_visitor_documents",
        "conversation_retention_days",
        "suggested_questions",
        "suggestions_manual",
    }
    cols = [c for c in changes if c in allowed]
    if not cols:
        return get_widget_settings(user)
    sets = ", ".join(f"{c} = %s" for c in cols)
    params = [changes[c] for c in cols]
    params.append(str(user["company_id"]))
    with connection() as conn:
        row = conn.execute(
            f"""
            UPDATE widget_settings SET {sets}
            WHERE company_id = %s
            RETURNING {_SETTINGS_COLS}
            """,
            params,
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return _settings_from_row(row)


# Parameterized route last so it does not shadow /widget-settings
@router.get("/{company_id}", response_model=CompanyResponse)
def get_company_by_id(
    company_id: UUID,
    user: dict = Depends(panel_user),
) -> CompanyResponse:
    """Return the company only if it is the caller's; otherwise 404 (not 403)."""
    if str(company_id) != str(user["company_id"]):
        raise ApiError("not_found", 404)
    return _fetch_company(company_id)
