"""Panel list of widget conversations. Company-scoped; no visitor IP."""
from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from site_ai_widget.api.deps import panel_user
from site_ai_widget.api.errors import ApiError
from site_ai_widget.db.pool import connection

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("")
def list_conversations(
    user: dict = Depends(panel_user),
    unanswered: bool = False,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> list[dict]:
    company_id = str(user["company_id"])
    clauses = ["s.company_id = %s"]
    params: list = [company_id]
    if unanswered:
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM visitor_messages m
                WHERE m.session_id = s.id AND m.company_id = s.company_id
                  AND m.role = 'assistant' AND m.unanswered
            )
            """
        )
    if date_from is not None and date_to is not None and date_from > date_to:
        raise ApiError("validation_error", 422)
    # Whole calendar days, inclusive on both ends: [from 00:00, to + 1 day 00:00).
    if date_from is not None:
        clauses.append("s.last_seen >= %s::date")
        params.append(date_from)
    if date_to is not None:
        clauses.append("s.last_seen < %s::date")
        params.append(date_to + timedelta(days=1))
    where = " AND ".join(clauses)
    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT s.id::text, s.created_at, s.last_seen, s.lang,
                   COUNT(m.id)::int AS message_count,
                   BOOL_OR(m.unanswered) AS has_unanswered,
                   (
                     SELECT m2.mode FROM visitor_messages m2
                     WHERE m2.session_id = s.id AND m2.company_id = s.company_id
                       AND m2.role = 'assistant'
                     ORDER BY m2.seq DESC
                     LIMIT 1
                   ) AS last_source
            FROM visitor_sessions s
            LEFT JOIN visitor_messages m
              ON m.session_id = s.id AND m.company_id = s.company_id
            WHERE {where}
            GROUP BY s.id, s.created_at, s.last_seen, s.lang
            ORDER BY s.last_seen DESC
            LIMIT 200
            """,
            params,
        ).fetchall()
    return [
        {
            "id": row[0],
            "created_at": row[1].isoformat() if row[1] else None,
            "last_seen": row[2].isoformat() if row[2] else None,
            "lang": row[3],
            "message_count": row[4],
            "unanswered": bool(row[5]),
            "answer_source": row[6] or "none",
        }
        for row in rows
    ]


@router.get("/{session_id}")
def get_conversation(session_id: UUID, user: dict = Depends(panel_user)) -> dict:
    company_id = str(user["company_id"])
    with connection() as conn:
        session = conn.execute(
            """
            SELECT id::text, created_at, last_seen, lang
            FROM visitor_sessions
            WHERE id = %s AND company_id = %s
            """,
            (str(session_id), company_id),
        ).fetchone()
        if not session:
            raise ApiError("not_found", 404)
        messages = conn.execute(
            """
            SELECT role, text, mode, unanswered, elapsed_ms, created_at
            FROM visitor_messages
            WHERE session_id = %s AND company_id = %s
            ORDER BY seq ASC
            LIMIT 500
            """,
            (str(session_id), company_id),
        ).fetchall()
    return {
        "id": session[0],
        "created_at": session[1].isoformat() if session[1] else None,
        "last_seen": session[2].isoformat() if session[2] else None,
        "lang": session[3],
        "messages": [
            {
                "role": m[0],
                "text": m[1],
                "mode": m[2],
                "unanswered": bool(m[3]),
                "elapsed_ms": m[4],
                "created_at": m[5].isoformat() if m[5] else None,
            }
            for m in messages
        ],
    }
