"""Seed two fictional demo companies (passwords from env only)."""
from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from site_ai_widget.api.auth import hash_password
from site_ai_widget.db.pool import close_pool, connection

load_dotenv()
log = logging.getLogger("site_ai_widget")

DEMOS = (
    {
        "name": "Mercearia Atlas",
        "slug": "mercearia-atlas",
        "email": "admin@mercearia-atlas.example",
        "locale": "pt",
        "password_env": "DEMO_PASSWORD_ATLAS",
        "greeting": "Bem-vindo à Mercearia Atlas! Em que posso ajudar?",
        "assistant_name": "Atlas",
        "primary_color": "#0f766e",
    },
    {
        "name": "Oficina Lima",
        "slug": "oficina-lima",
        "email": "admin@oficina-lima.example",
        "locale": "pt",
        "password_env": "DEMO_PASSWORD_LIMA",
        "greeting": "Olá, aqui é a Oficina Lima. Como podemos ajudar?",
        "assistant_name": "Lima",
        "primary_color": "#b45309",
    },
)


def seed(database_url: str | None = None) -> list[str]:
    created: list[str] = []
    with connection(database_url) as conn:
        for demo in DEMOS:
            password = os.environ.get(demo["password_env"], "").strip()
            if not password:
                raise SystemExit(
                    f"Missing {demo['password_env']} in the environment "
                    "(no default passwords in code)."
                )
            existing = conn.execute(
                "SELECT id FROM companies WHERE slug = %s",
                (demo["slug"],),
            ).fetchone()
            if existing:
                log.info("Skip existing company %s", demo["slug"])
                continue
            company = conn.execute(
                """
                INSERT INTO companies (name, slug, default_locale)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (demo["name"], demo["slug"], demo["locale"]),
            ).fetchone()
            company_id = company[0]
            conn.execute(
                """
                INSERT INTO users (company_id, email, password_hash, role)
                VALUES (%s, %s, %s, 'admin')
                """,
                (company_id, demo["email"], hash_password(password)),
            )
            conn.execute(
                """
                INSERT INTO widget_settings
                    (company_id, primary_color, greeting, locale, assistant_name)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    company_id,
                    demo["primary_color"],
                    demo["greeting"],
                    demo["locale"],
                    demo["assistant_name"],
                ),
            )
            created.append(demo["slug"])
            log.info("Created company %s (%s)", demo["name"], demo["email"])
    return created


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        raise SystemExit(1)
    created = seed(url)
    print(f"OK seed: {len(created)} company(ies)" + (f" ({', '.join(created)})" if created else " (all existed)"))
    close_pool()


if __name__ == "__main__":
    main()
