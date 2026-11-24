"""Runtime-configurable institutional rules (stored in the database, editable by administrators)."""
from app import db
from . import audit
from .actor import Actor
from .errors import NotFound, ValidationFailed

_NUMERIC = {"max_credits_per_term", "mess_daily_rate", "mess_min_days", "mess_max_days", "mess_notice_days",
            "sports_daily_quota_hours", "sports_weekly_quota_hours", "sports_open_hour", "sports_close_hour",
            "sports_advance_days", "cancel_cutoff_minutes", "checkin_grace_minutes", "strike_limit",
            "suspension_days", "equipment_max_items", "equipment_max_days", "requisition_notice_days",
            "requisition_max_hours", "requisition_max_pending", "sports_checkin_early_minutes"}
# which admin scope may edit which setting
_SCOPE = {"current_term": "academic", "max_credits_per_term": "academic", "drop_deadline": "academic"}
for _k in _NUMERIC:
    if _k.startswith("mess"):
        _SCOPE[_k] = "mess"
    elif _k.startswith(("sports", "cancel", "checkin", "strike", "suspension", "equipment", "requisition")):
        _SCOPE[_k] = "requisition"


def get(cur, key: str):
    cur.execute("SELECT value FROM system_settings WHERE key=%s", (key,))
    row = cur.fetchone()
    if not row:
        raise NotFound(f"Unknown setting {key}.")
    v = row["value"]
    if key in _NUMERIC:
        return float(v) if "." in v else int(v)
    return v


def get_quick(key: str):
    with db.tx() as cur:
        return get(cur, key)


def list_all() -> list[dict]:
    return db.query("SELECT key, value, description, updated_at FROM system_settings ORDER BY key")


def update(actor: Actor, key: str, value: str) -> dict:
    scope = _SCOPE.get(key)
    if scope is None:
        raise NotFound(f"Unknown setting {key}.")
    actor.require_scope(scope)
    value = str(value).strip()
    if key in _NUMERIC:
        try:
            if float(value) < 0:
                raise ValueError
        except ValueError:
            raise ValidationFailed(f"{key} must be a non-negative number.")
    if key == "drop_deadline":
        import datetime
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise ValidationFailed("drop_deadline must be YYYY-MM-DD.")
    if not value:
        raise ValidationFailed("Value required.")
    with db.tx() as cur:
        cur.execute("UPDATE system_settings SET value=%s, updated_at=now() WHERE key=%s", (value, key))
        audit.log(cur, actor, "settings.update", "setting", key, detail={"value": value})
    return {"key": key, "value": value}
