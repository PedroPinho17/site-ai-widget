"""Signed PDF URLs, suggestion sanitising and retention (no secrets printed)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from site_ai_widget.api.errors import ApiError
from site_ai_widget.docs.signed import sign_document_access, verify_document_access
from site_ai_widget.docs.suggestions import parse_questions, sanitize_question
from tests.conftest import postgres_available


def test_sanitize_strips_email_and_caps_length():
    assert sanitize_question("  Qual o horário?  ") == "Qual o horário?"
    # A line carrying personal data is dropped, not half-redacted.
    assert sanitize_question("Email-me em atlas@example.com sobre o azeite?") is None
    assert sanitize_question("Ligue para +351 912 345 678 para encomendar?") is None
    assert sanitize_question("x" * 100 + "?") is None


def test_parse_questions_limits():
    raw = "\n".join(f"Pergunta número {i} sobre a loja?" for i in range(10))
    parsed = parse_questions(raw)
    assert 3 <= len(parsed) <= 6


def test_signed_url_rejects_expired_and_wrong_company():
    company = uuid4()
    doc = uuid4()
    sig, exp = sign_document_access(company_id=company, document_id=doc, ttl_seconds=60)
    verify_document_access(company_id=company, document_id=doc, exp=exp, signature=sig)
    with pytest.raises(ApiError) as expired:
        verify_document_access(
            company_id=company,
            document_id=doc,
            exp=int(exp) - 10_000,
            signature=sig,
        )
    assert expired.value.code == "signed_url_expired"
    other = uuid4()
    with pytest.raises(ApiError) as invalid:
        verify_document_access(company_id=other, document_id=doc, exp=exp, signature=sig)
    assert invalid.value.code == "signed_url_invalid"


@pytest.mark.skipif(not postgres_available(), reason="Postgres is not reachable")
def test_retention_deletes_only_own_old_sessions():
    from site_ai_widget.db.migrate import migrate
    from site_ai_widget.db.pool import close_pool, connection
    from site_ai_widget.widget.retention import cleanup_expired_conversations

    close_pool()
    migrate()
    close_pool()

    company_a = None
    company_b = None
    with connection() as conn:
        company_a = conn.execute(
            "INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id",
            ("Atlas Stats", f"atlas-ret-{uuid4().hex[:8]}"),
        ).fetchone()[0]
        company_b = conn.execute(
            "INSERT INTO companies (name, slug) VALUES (%s, %s) RETURNING id",
            ("Lima Stats", f"lima-ret-{uuid4().hex[:8]}"),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO widget_settings (company_id, conversation_retention_days) VALUES (%s, 30)",
            (company_a,),
        )
        conn.execute(
            "INSERT INTO widget_settings (company_id, conversation_retention_days) VALUES (%s, 60)",
            (company_b,),
        )
        old = datetime.now(UTC) - timedelta(days=40)
        recent = datetime.now(UTC)
        conn.execute(
            """
            INSERT INTO visitor_sessions (company_id, token_hash, last_seen)
            VALUES (%s, %s, %s)
            """,
            (company_a, "a" * 64, old),
        )
        conn.execute(
            """
            INSERT INTO visitor_sessions (company_id, token_hash, last_seen)
            VALUES (%s, %s, %s)
            """,
            (company_b, "b" * 64, old),
        )
        conn.execute(
            """
            INSERT INTO visitor_sessions (company_id, token_hash, last_seen)
            VALUES (%s, %s, %s)
            """,
            (company_a, "c" * 64, recent),
        )
    cleanup_expired_conversations(now=datetime.now(UTC))
    with connection() as conn:
        left_a = conn.execute(
            "SELECT COUNT(*) FROM visitor_sessions WHERE company_id = %s",
            (company_a,),
        ).fetchone()[0]
        left_b = conn.execute(
            "SELECT COUNT(*) FROM visitor_sessions WHERE company_id = %s",
            (company_b,),
        ).fetchone()[0]
    # A (30 days): the 40-day-old session is gone, the recent one stays.
    # B (60 days): the same 40-day-old session is still inside its window.
    assert left_a == 1
    assert left_b == 1
