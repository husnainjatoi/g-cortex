"""Event-driven cross-module pipeline (transactional outbox + idempotent handlers).

* `emit(cur, type, payload)` persists the event in the SAME transaction as the business change.
* After the transaction commits, `@op` dispatches it to subscribed handlers.
* Each handler runs in its own transaction and is recorded in `event_handler_runs`, so a handler can
  never be applied twice for one event, and failed handlers can be retried safely.
"""
import contextvars
import json
import logging

from app import db

logger = logging.getLogger("g_cortex.events")

_collect: contextvars.ContextVar[list | None] = contextvars.ContextVar("event_collect", default=None)
_handlers: dict[str, list] = {}


def subscribe(event_type: str):
    def deco(fn):
        _handlers.setdefault(event_type, []).append(fn)
        return fn
    return deco


def subscriptions() -> dict[str, list[str]]:
    return {t: [h.__name__ for h in hs] for t, hs in _handlers.items()}


def emit(cur, event_type: str, payload: dict) -> str:
    cur.execute("INSERT INTO domain_events (event_type, payload) VALUES (%s,%s) RETURNING event_id",
                (event_type, json.dumps(payload, default=str)))
    event_id = str(cur.fetchone()["event_id"])
    pending = _collect.get()
    if pending is not None:
        pending.append(event_id)
    return event_id


def begin_collection():
    return _collect.set([])


def discard_collection(token) -> None:
    _collect.reset(token)


def dispatch_collection(token) -> None:
    ids = _collect.get() or []
    _collect.reset(token)
    for event_id in ids:
        dispatch(event_id)


def dispatch(event_id: str) -> int:
    row = db.query_one("SELECT event_type, payload FROM domain_events WHERE event_id = %s", (event_id,))
    if not row:
        return 0
    ok = 0
    for handler in _handlers.get(row["event_type"], []):
        if _run_handler(event_id, handler, row["event_type"], row["payload"]):
            ok += 1
    try:  # bridge to the in-process bus for any legacy subscribers
        from app.core.event_bus import event_bus
        event_bus.publish(row["event_type"], {**row["payload"], "event_id": event_id})
    except Exception:
        logger.debug("event bus bridge skipped", exc_info=True)
    return ok


def _run_handler(event_id: str, handler, event_type: str, payload: dict) -> bool:
    name = handler.__name__
    try:
        with db.tx() as cur:
            cur.execute("SELECT status FROM event_handler_runs WHERE event_id = %s AND handler = %s FOR UPDATE", (event_id, name))
            prior = cur.fetchone()
            if prior and prior["status"] == "done":
                return True  # duplicate delivery: already applied
            handler(cur, payload)
            cur.execute(
                """INSERT INTO event_handler_runs (event_id, handler, status) VALUES (%s,%s,'done')
                   ON CONFLICT (event_id, handler) DO UPDATE SET status='done', error=NULL, at=now(),
                   attempts = event_handler_runs.attempts + 1""", (event_id, name))
        return True
    except Exception as exc:
        logger.exception("handler %s failed for %s", name, event_type)
        try:
            with db.tx() as cur:
                cur.execute(
                    """INSERT INTO event_handler_runs (event_id, handler, status, error) VALUES (%s,%s,'failed',%s)
                       ON CONFLICT (event_id, handler) DO UPDATE SET status='failed', error=EXCLUDED.error,
                       attempts = event_handler_runs.attempts + 1, at=now()""", (event_id, name, str(exc)[:500]))
        except Exception:
            logger.exception("could not record handler failure")
        return False


def retry_failed() -> dict:
    """Re-run handlers whose previous attempt failed (admin tool / background job)."""
    rows = db.query(
        """SELECT r.event_id, r.handler, e.event_type, e.payload FROM event_handler_runs r
           JOIN domain_events e USING (event_id) WHERE r.status = 'failed' AND r.attempts < 5""")
    retried = recovered = 0
    for r in rows:
        for h in _handlers.get(r["event_type"], []):
            if h.__name__ == r["handler"]:
                retried += 1
                if _run_handler(str(r["event_id"]), h, r["event_type"], r["payload"]):
                    recovered += 1
    return {"retried": retried, "recovered": recovered}


def recent(limit: int = 50) -> list[dict]:
    return db.query(
        """SELECT e.event_id, e.event_type, e.payload, e.created_at,
                  COALESCE(json_agg(json_build_object('handler', r.handler, 'status', r.status, 'error', r.error))
                           FILTER (WHERE r.handler IS NOT NULL), '[]') AS handlers
           FROM domain_events e LEFT JOIN event_handler_runs r USING (event_id)
           GROUP BY e.event_id ORDER BY e.created_at DESC LIMIT %s""", (max(1, min(limit, 200)),))
