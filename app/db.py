"""Transactional PostgreSQL access used by the shared service layer."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from app.config import database_url

_pool: ThreadedConnectionPool | None = None


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 20, database_url(), connect_timeout=5)
    return _pool


def reset_pool() -> None:
    """Drop the pool (used by tests that point DATABASE_URL elsewhere)."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def tx(isolation: str | None = None):
    """Yield a dict cursor inside ONE transaction. Commits on success, rolls back on any exception."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = False
        if isolation:
            conn.set_session(isolation_level=isolation)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            cur.close()
            if isolation:
                conn.set_session(isolation_level=None)
    finally:
        pool.putconn(conn)


def query(sql: str, params=None) -> list[dict]:
    with tx() as cur:
        cur.execute(sql, params or ())
        return [dict(r) for r in cur.fetchall()]


def query_one(sql: str, params=None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def ping() -> bool:
    try:
        return query_one("SELECT 1 AS ok")["ok"] == 1
    except Exception:
        return False
