"""Flyway-style migrations from ``db/migrations``.

- ``V001__description.sql``: versioned, run once in order.
- ``R__description.sql``: repeatable; re-run when checksum changes.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("site_ai_widget")

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"
VERSIONED = re.compile(r"^V(\d{3,})__([a-z0-9_]+)\.sql$")
REPEATABLE = re.compile(r"^R__([a-z0-9_]+)\.sql$")
LOCK_ID = 8_401_202
NO_TRANSACTION = "-- migrate: no-transaction"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text PRIMARY KEY,
    description text NOT NULL,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)"""


@dataclass(frozen=True)
class Migration:
    version: str
    description: str
    path: Path
    repeatable: bool

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.replace("\r\n", "\n").encode()).hexdigest()[:16]

    @property
    def transactional(self) -> bool:
        return NO_TRANSACTION not in self.sql


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    versioned, repeatable = [], []
    for path in sorted(directory.glob("*.sql")):
        if m := VERSIONED.match(path.name):
            versioned.append(Migration(m.group(1), m.group(2).replace("_", " "), path, False))
        elif m := REPEATABLE.match(path.name):
            repeatable.append(Migration(f"R__{m.group(1)}", m.group(1).replace("_", " "), path, True))
        else:
            raise ValueError(f"Invalid migration name: {path.name} (use V001__name.sql or R__name.sql)")
    versions = [m.version for m in versioned]
    if len(set(versions)) != len(versions):
        raise ValueError(f"Duplicate versions in {directory}: {versions}")
    return sorted(versioned, key=lambda m: int(m.version)) + repeatable


def applied(conn) -> dict[str, str]:
    conn.execute(CREATE_TABLE)
    return dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())


def pending(migrations: list[Migration], done: dict[str, str]) -> list[Migration]:
    out = []
    for m in migrations:
        if m.repeatable:
            if done.get(m.version) != m.checksum:
                out.append(m)
        elif m.version not in done:
            out.append(m)
        elif done[m.version] != m.checksum:
            log.warning(
                "Migration %s changed after apply; will not re-run. Add a new migration.",
                m.path.name,
            )
    return out


def split_statements(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [st.strip() for st in "\n".join(lines).split(";") if st.strip()]


def _apply(conn, m: Migration) -> None:
    record = (
        "INSERT INTO schema_migrations (version, description, checksum) VALUES (%s, %s, %s) "
        "ON CONFLICT (version) DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = now()"
    )
    if m.transactional:
        with conn.transaction():
            conn.execute(m.sql)
            conn.execute(record, (m.version, m.description, m.checksum))
    else:
        for statement in split_statements(m.sql):
            conn.execute(statement)
        conn.execute(record, (m.version, m.description, m.checksum))


def migrate(url: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    migrations = discover(directory)
    with psycopg.connect(url or os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            todo = pending(migrations, applied(conn))
            for m in todo:
                log.info("Applying %s", m.path.name)
                _apply(conn, m)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return [m.path.name for m in todo]


def status(url: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    migrations = discover(directory)
    with psycopg.connect(url or os.environ["DATABASE_URL"], autocommit=True) as conn:
        done = applied(conn)
        out = []
        for m in migrations:
            if m.version not in done:
                state = "pending"
            elif done[m.version] != m.checksum:
                state = "pending (changed)" if m.repeatable else "changed after apply"
            else:
                state = "applied"
            out.append((m.path.name, state))
        return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--status" in sys.argv[1:]:
        for name, state in status():
            print(f"  {state:<30} {name}")
    else:
        done_now = migrate()
        suffix = f" ({', '.join(done_now)})" if done_now else ""
        print(f"OK migrations: {len(done_now)} applied{suffix}")


if __name__ == "__main__":
    main()
