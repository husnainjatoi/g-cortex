"""Student/staff portal endpoints: dashboard, notifications, own activity history."""
from fastapi import APIRouter, Depends

from app.api.deps import current_actor
from app.services import audit, notifications, reports
from app.services.actor import Actor

router = APIRouter()


@router.get("/dashboard")
def dashboard(actor: Actor = Depends(current_actor)):
    actor.require_role("student")
    return reports.student_dashboard(actor)


@router.get("/notifications")
def list_notifications(unread: bool = False, actor: Actor = Depends(current_actor)):
    return {"items": notifications.list_for(actor, unread), "unread": notifications.unread_count(actor)}


@router.post("/notifications/read")
def read_all(actor: Actor = Depends(current_actor)):
    return {"updated": notifications.mark_read(actor)}


@router.post("/notifications/{notification_id}/read")
def read_one(notification_id: int, actor: Actor = Depends(current_actor)):
    return {"updated": notifications.mark_read(actor, notification_id)}


@router.get("/activity")
def activity(limit: int = 100, offset: int = 0, outcome: str | None = None, actor: Actor = Depends(current_actor)):
    """The caller's OWN audit trail (successes, denials and failures). Never exposes other users' entries."""
    own = actor.as_channel(actor.channel)
    from app import db
    rows = db.query(
        """SELECT audit_id, at, channel, action, entity_type, entity_id, outcome, detail FROM audit_log
           WHERE actor_id=%s AND action NOT LIKE 'auth.%%' """ + ("AND outcome=%s " if outcome else "") + "ORDER BY audit_id DESC LIMIT %s OFFSET %s",
        (own.user_id, *([outcome] if outcome else []), max(1, min(limit, 300)), max(0, offset)))
    return rows
