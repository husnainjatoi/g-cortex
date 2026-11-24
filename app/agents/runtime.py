"""Per-request assistant context. Identity comes ONLY from here - set by the authenticated API layer,
never from tool arguments or model output."""
import contextvars
import functools
import logging
from dataclasses import dataclass

from app.services import pending_actions
from app.services.actor import Actor
from app.services.errors import ServiceError, Unauthenticated

logger = logging.getLogger("g_cortex.assistant")


@dataclass(frozen=True)
class AssistantContext:
    actor: Actor
    session_id: str


_ctx: contextvars.ContextVar[AssistantContext | None] = contextvars.ContextVar("assistant_ctx", default=None)


def set_context(actor: Actor, session_id: str):
    return _ctx.set(AssistantContext(actor.as_channel("assistant"), session_id))


def reset_context(token) -> None:
    _ctx.reset(token)


def context() -> AssistantContext:
    c = _ctx.get()
    if c is None:
        raise Unauthenticated("No authenticated user context for this tool call.")
    return c


def tool(fn):
    """Wrap a tool so every call is bound to the session user and service errors become honest, structured results."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            out = fn(*args, **kwargs)
            return out if isinstance(out, dict) else {"ok": True, "result": out}
        except ServiceError as exc:
            return {"ok": False, "refused_by_backend": True, "code": exc.code, "error": exc.message, "details": exc.details}
        except Exception as exc:
            logger.exception("tool %s crashed", fn.__name__)
            return {"ok": False, "code": "internal_error", "error": f"Tool failed: {type(exc).__name__}"}
    return wrapper


def propose(tool_name: str, args: dict, summary: str, preflight=None) -> dict:
    """Stage a consequential action. Runs the real rule checks first; nothing is executed until the USER confirms."""
    c = context()
    if preflight is not None:
        preflight(c.actor)                       # raises ServiceError -> reported to the user as a refusal
    p = pending_actions.propose(c.actor, c.session_id, tool_name, args, summary)
    return {"ok": True, "status": "awaiting_user_confirmation", "executed": False, "action_id": p["action_id"], "summary": summary,
            "instruction_to_assistant": ("The action has NOT been performed. Tell the user exactly what will happen (the summary) and ask them to "
                                         "confirm by replying 'yes' or pressing Confirm. Do not claim it is done.")}
