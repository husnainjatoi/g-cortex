"""Minimal forward-only SQL migration runner.

Usage:  python -m app.migrate
Applies every migrations/NNN_*.sql file not yet recorded in schema_migrations, each in one transaction.
"""
import sys
from pathlib import Path

import psycopg2

from app.config import ROOT, database_url

MIGRATIONS_DIR = ROOT / "migrations"


def run(verbose: bool = True) -> list[str]:
    conn = psycopg2.connect(database_url())
    applied: list[str] = []
    try:
        with conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT now())")
            cur.execute("SELECT version FROM schema_migrations")
            done = {r[0] for r in cur.fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            with conn, conn.cursor() as cur:
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied.append(path.name)
            if verbose:
                print(f"applied {path.name}")
    finally:
        conn.close()
    if verbose and not applied:
        print("database is up to date")
    return applied


if __name__ == "__main__":
    run()
    sys.exit(0)
