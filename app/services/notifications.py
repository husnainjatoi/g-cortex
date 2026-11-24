"""In-app notifications. Created by event handlers inside their own transaction."""
from app import db
from .actor import Actor
from .errors import NotFound


def notify(cur, user_id: str, kind: str, title: str, body: str = "", level: str = "info", link: str | None = None) -> None:
    cur.execute(
        "INSERT INTO notifications (user_id, kind, level, title, body, link) VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, kind, level, title[:150], body, link))


def notify_admins(cur, scope: str, kind: str, title: str, body: str = "", level: str = "info", link: str | None = None) -> None:
    cur.execute(
        """SELECT DISTINCT u.user_id FROM users u JOIN admin_scopes s ON s.user_id = u.user_id
           WHERE u.role='admin' AND u.is_active AND s.scope IN ('super', %s)""", (scope,))
    for r in cur.fetchall():
        notify(cur, r["user_id"], kind, title, body, level, link)


def list_for(actor: Actor, unread_only: bool = False, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM notifications WHERE user_id = %s" + (" AND read_at IS NULL" if unread_only else "")
    return db.query(sql + " ORDER BY created_at DESC, notification_id DESC LIMIT %s", (actor.user_id, max(1, min(limit, 300))))


def unread_count(actor: Actor) -> int:
    return db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND read_at IS NULL", (actor.user_id,))["n"]


def mark_read(actor: Actor, notification_id: int | None = None) -> int:
    with db.tx() as cur:
        if notification_id is None:
            cur.execute("UPDATE notifications SET read_at=now() WHERE user_id=%s AND read_at IS NULL", (actor.user_id,))
        else:
            cur.execute("UPDATE notifications SET read_at=now() WHERE user_id=%s AND notification_id=%s AND read_at IS NULL",
                        (actor.user_id, notification_id))
            if cur.rowcount == 0:
                cur.execute("SELECT 1 FROM notifications WHERE user_id=%s AND notification_id=%s", (actor.user_id, notification_id))
                if not cur.fetchone():
                    raise NotFound("Notification not found.")
        return cur.rowcount
