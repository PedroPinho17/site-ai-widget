"""Company-scoped dashboard stats from visitor messages."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from site_ai_widget.api.deps import panel_user
from site_ai_widget.db.pool import connection

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard")
def dashboard_stats(user: dict = Depends(panel_user)) -> dict:
    company_id = str(user["company_id"])
    with connection() as conn:
        per_day = conn.execute(
            """
            SELECT created_at::date AS day, COUNT(*)::int
            FROM visitor_messages
            WHERE company_id = %s AND role = 'user'
              AND created_at >= (CURRENT_DATE - INTERVAL '29 days')
            GROUP BY 1
            ORDER BY 1
            """,
            (company_id,),
        ).fetchall()
        sources = conn.execute(
            """
            SELECT COALESCE(mode, 'none'), COUNT(*)::int
            FROM visitor_messages
            WHERE company_id = %s AND role = 'assistant'
              AND created_at >= (CURRENT_DATE - INTERVAL '29 days')
            GROUP BY 1
            """,
            (company_id,),
        ).fetchall()
        avg_ms = conn.execute(
            """
            SELECT AVG(elapsed_ms)
            FROM visitor_messages
            WHERE company_id = %s AND role = 'assistant' AND elapsed_ms IS NOT NULL
              AND created_at >= (CURRENT_DATE - INTERVAL '29 days')
            """,
            (company_id,),
        ).fetchone()
        unanswered = conn.execute(
            """
            SELECT u.text, COUNT(*)::int AS n
            FROM visitor_messages a
            JOIN visitor_messages u
              ON u.session_id = a.session_id
             AND u.company_id = a.company_id
             AND u.role = 'user'
             AND u.seq = (
                 SELECT MAX(u2.seq) FROM visitor_messages u2
                 WHERE u2.session_id = a.session_id
                   AND u2.company_id = a.company_id
                   AND u2.role = 'user'
                   AND u2.seq < a.seq
             )
            WHERE a.company_id = %s AND a.role = 'assistant' AND a.unanswered
              AND a.created_at >= (CURRENT_DATE - INTERVAL '29 days')
              AND u.text IS NOT NULL
            GROUP BY u.text
            ORDER BY n DESC
            LIMIT 8
            """,
            (company_id,),
        ).fetchall()
    source_counts = {str(mode): n for mode, n in sources}
    total = sum(source_counts.values()) or 0

    def pct(mode: str) -> float:
        if not total:
            return 0.0
        return round(100.0 * source_counts.get(mode, 0) / total, 1)

    return {
        "questions_per_day": [
            {"day": str(day), "count": count} for day, count in per_day
        ],
        "source_share": {
            "sql": pct("sql"),
            "docs": pct("docs"),
            "none": pct("none"),
            "chat": pct("chat"),
        },
        "source_counts": source_counts,
        "top_unanswered": [{"question": text, "count": n} for text, n in unanswered],
        "average_response_ms": int(avg_ms[0]) if avg_ms and avg_ms[0] is not None else None,
    }
