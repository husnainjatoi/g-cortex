"""Append-only audit trail + the @op decorator that records denied / failed attempts and dispatches events."""
import functools
import inspect
import json
import logging

from app import db
from .actor import Actor
from .errors import ServiceError, Forbidden

logger = logging.getLogger("g_cortex.audit")


def log(cur, actor: Actor | None, action: str, entity_type: str | None = None, entity_id=None,
        outcome: str = "success", detail: dict | None = None) -> None:
    """Write an audit row inside the caller's transaction (so it commits/rolls back with the business change)."""
    cur.execute(
        """INSERT INTO audit_log (actor_id, actor_role, channel, action, entity_type, entity_id, outcome, detail)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (getattr(actor, "user_id", None), getattr(actor, "role", None), getattr(actor, "channel", "system"),
         action, entity_type, None if entity_id is None else str(entity_id), outcome,
         json.dumps(detail or {}, default=str)),
    )


def log_independent(actor: Actor | None, action: str, entity_type, entity_id, outcome: str, detail: dict) -> None:
    """Audit a failed attempt in its own transaction so it survives the rollback of the business change."""
    try:
        with db.tx() as cur:
            log(cur, actor, action, entity_type, entity_id, outcome, detail)
    except Exception:  # auditing must never mask the original error
        logger.exception("could not write audit entry for %s", action)


def op(action: str, entity_type: str | None = None, entity_arg: str | None = None):
    """Decorator for service operations.

    * Records `denied` / `failed` audit entries for rejected attempts.
    * After the operation commits, dispatches domain events emitted during it.
    The first parameter of the wrapped function must be the `actor`.
    """
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from . import events
            bound = sig.bind_partial(*args, **kwargs)
            actor = bound.arguments.get("actor")
            token = events.begin_collection()
            try:
                result = fn(*args, **kwargs)
            except ServiceError as exc:
                events.discard_collection(token)
                entity = bound.arguments.get(entity_arg) if entity_arg else None
                outcome = "denied" if isinstance(exc, Forbidden) else "failed"
                safe_args = {k: v for k, v in bound.arguments.items() if k != "actor" and not k.startswith("password")}
                log_independent(actor, action, entity_type, entity, outcome,
                                {"error": exc.message, "code": exc.code, "args": safe_args})
                raise
            except BaseException:
                events.discard_collection(token)
                raise
            events.dispatch_collection(token)
            return result
        wrapper.__wrapped_op__ = action
        return wrapper
    return deco


def list_entries(actor: Actor, *, limit: int = 100, offset: int = 0, actor_filter: str | None = None,
                 action: str | None = None, outcome: str | None = None) -> list[dict]:
    """Admins (super) see everything; everyone else sees only their own activity."""
    clauses, params = [], []
    if actor.has_scope("super"):
        if actor_filter:
            clauses.append("actor_id = %s"); params.append(actor_filter)
    else:
        clauses.append("actor_id = %s"); params.append(actor.user_id)
    if action:
        clauses.append("action LIKE %s"); params.append(action + "%")
    if outcome:
        clauses.append("outcome = %s"); params.append(outcome)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, min(int(limit), 500))
    return db.query(
        f"SELECT audit_id, at, actor_id, actor_role, channel, action, entity_type, entity_id, outcome, detail "
        f"FROM audit_log {where} ORDER BY audit_id DESC LIMIT %s OFFSET %s", (*params, limit, max(0, int(offset))))
