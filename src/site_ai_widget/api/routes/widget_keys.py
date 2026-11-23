"""Widget key CRUD for the logged-in company's panel."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from site_ai_widget.api.auth import new_widget_public_key
from site_ai_widget.api.deps import panel_user, require_admin
from site_ai_widget.api.errors import ApiError
from site_ai_widget.api.schemas import WidgetKeyCreate, WidgetKeyResponse, WidgetKeyUpdate
from site_ai_widget.db.pool import connection

router = APIRouter(prefix="/widget-keys", tags=["widget-keys"])


def _row_to_key(row) -> WidgetKeyResponse:
    return WidgetKeyResponse(
        id=row[0],
        public_key=row[1],
        allowed_origins=list(row[2] or []),
        is_active=bool(row[3]),
        created_at=row[4],
    )


@router.get("", response_model=list[WidgetKeyResponse])
def list_keys(user: dict = Depends(panel_user)) -> list[WidgetKeyResponse]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, public_key, allowed_origins, is_active, created_at
            FROM widget_keys
            WHERE company_id = %s
            ORDER BY created_at DESC
            """,
            (str(user["company_id"]),),
        ).fetchall()
    return [_row_to_key(r) for r in rows]


@router.post("", response_model=WidgetKeyResponse, status_code=201)
def create_key(
    body: WidgetKeyCreate,
    user: dict = Depends(require_admin),
) -> WidgetKeyResponse:
    public_key = new_widget_public_key()
    with connection() as conn:
        row = conn.execute(
            """
            INSERT INTO widget_keys (company_id, public_key, allowed_origins)
            VALUES (%s, %s, %s)
            RETURNING id, public_key, allowed_origins, is_active, created_at
            """,
            (str(user["company_id"]), public_key, body.allowed_origins),
        ).fetchone()
    return _row_to_key(row)


def _get_own_key(key_id: UUID, company_id) -> tuple:
    """Fetch a key belonging to ``company_id``; 404 if missing or other company."""
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id, public_key, allowed_origins, is_active, created_at, company_id
            FROM widget_keys WHERE id = %s
            """,
            (str(key_id),),
        ).fetchone()
    if not row or str(row[5]) != str(company_id):
        raise ApiError("not_found", 404)
    return row


@router.patch("/{key_id}", response_model=WidgetKeyResponse)
def update_key(
    key_id: UUID,
    body: WidgetKeyUpdate,
    user: dict = Depends(require_admin),
) -> WidgetKeyResponse:
    _get_own_key(key_id, user["company_id"])
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        row = _get_own_key(key_id, user["company_id"])
        return _row_to_key(row[:5])
    sets = []
    params: list = []
    if "allowed_origins" in changes:
        sets.append("allowed_origins = %s")
        params.append(changes["allowed_origins"])
    if "is_active" in changes:
        sets.append("is_active = %s")
        params.append(changes["is_active"])
    params.extend([str(key_id), str(user["company_id"])])
    with connection() as conn:
        row = conn.execute(
            f"""
            UPDATE widget_keys SET {", ".join(sets)}
            WHERE id = %s AND company_id = %s
            RETURNING id, public_key, allowed_origins, is_active, created_at
            """,
            params,
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return _row_to_key(row)


@router.post("/{key_id}/revoke", response_model=WidgetKeyResponse)
def revoke_key(
    key_id: UUID,
    user: dict = Depends(require_admin),
) -> WidgetKeyResponse:
    _get_own_key(key_id, user["company_id"])
    with connection() as conn:
        row = conn.execute(
            """
            UPDATE widget_keys SET is_active = false
            WHERE id = %s AND company_id = %s
            RETURNING id, public_key, allowed_origins, is_active, created_at
            """,
            (str(key_id), str(user["company_id"])),
        ).fetchone()
    if not row:
        raise ApiError("not_found", 404)
    return _row_to_key(row)
