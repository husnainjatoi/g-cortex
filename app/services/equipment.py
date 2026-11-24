"""Lab equipment inventory with barcode-based custody tracking."""
import datetime

from app import db
from . import audit, clock, events, settings
from .actor import Actor
from .audit import op
from .errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed

EQUIPMENT_CHECKED_OUT = "equipment.checked_out"
EQUIPMENT_RETURNED = "equipment.returned"
CONDITIONS = ("good", "fair", "damaged")


def list_equipment(actor: Actor, status: str | None = None, lab: str | None = None, q: str | None = None) -> list[dict]:
    where, params = [], []
    if status:
        where.append("e.status=%s"); params.append(status)
    if lab:
        where.append("e.lab ILIKE %s"); params.append(f"%{lab}%")
    if q:
        where.append("(e.name ILIKE %s OR e.barcode ILIKE %s OR e.category ILIKE %s)"); params += [f"%{q}%"] * 3
    rows = db.query(f"""SELECT e.*, u.full_name AS custodian_name FROM lab_equipment e LEFT JOIN users u ON u.user_id=e.custodian_id
                        {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY e.lab, e.barcode""", params)
    today = clock.today()
    for r in rows:
        r["overdue"] = bool(r["status"] == "in_custody" and r["due_date"] and r["due_date"] < today)
        if not (actor.has_scope("requisition") or r["custodian_id"] == actor.user_id):
            r["custodian_id"] = None; r["custodian_name"] = None; r["purpose"] = None  # custody details are private
    return rows


def lookup(actor: Actor, barcode: str) -> dict:
    code = (barcode or "").upper().strip()
    rows = [r for r in list_equipment(actor, q=code) if r["barcode"] == code]
    if not rows:
        raise NotFound(f"No equipment with barcode {code}.")
    return rows[0]


def my_custody(actor: Actor, student_id: str | None = None) -> list[dict]:
    sid = actor.resolve_student(student_id, "requisition")
    rows = db.query("SELECT * FROM lab_equipment WHERE custodian_id=%s AND status='in_custody' ORDER BY due_date", (sid,))
    for r in rows:
        r["overdue"] = bool(r["due_date"] and r["due_date"] < clock.today())
    return rows


@op("equipment.checkout", "equipment", "barcode")
def checkout(actor: Actor, barcode: str, purpose: str = "", days: int | None = None, student_id: str | None = None, dry_run: bool = False) -> dict:
    sid = actor.resolve_student(student_id, "requisition")
    code = (barcode or "").upper().strip()
    purpose = (purpose or "").strip()
    if not purpose:
        raise ValidationFailed("State the purpose of the loan.")
    with db.tx() as cur:
        cur.execute("SELECT role, is_active FROM users WHERE user_id=%s", (sid,))
        u = cur.fetchone()
        if not u or u["role"] != "student" or not u["is_active"]:
            raise ValidationFailed("Equipment can only be issued to active student accounts.")
        cur.execute("SELECT 1 FROM users WHERE user_id=%s FOR UPDATE", (sid,))
        max_items, max_days = settings.get(cur, "equipment_max_items"), settings.get(cur, "equipment_max_days")
        days = int(days or max_days)
        if not 1 <= days <= max_days:
            raise RuleViolation(f"Loan period must be between 1 and {max_days} days.")
        cur.execute("SELECT * FROM lab_equipment WHERE barcode=%s FOR UPDATE", (code,))
        e = cur.fetchone()
        if not e:
            raise NotFound(f"No equipment with barcode {code}.")
        if e["status"] != "available":
            raise RuleViolation(f"{e['name']} is not available (status: {e['status']}).", code="not_available")
        if e["condition"] == "damaged":
            raise RuleViolation(f"{e['name']} is marked damaged and cannot be issued.")
        cur.execute("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE due_date < %s) AS late FROM lab_equipment WHERE custodian_id=%s AND status='in_custody'", (clock.today(), sid))
        held = cur.fetchone()
        if held["late"]:
            raise RuleViolation("You have overdue equipment; return it before borrowing more.", code="overdue")
        if held["n"] >= max_items:
            raise RuleViolation(f"You already hold {held['n']} items (limit {max_items}).", code="item_limit")
        due = clock.today() + datetime.timedelta(days=days)
        if dry_run:
            return {"ok": True, "dry_run": True, "barcode": code, "name": e["name"], "lab": e["lab"], "due_date": str(due)}
        cur.execute("""UPDATE lab_equipment SET status='in_custody', custodian_id=%s, checkout_date=%s, due_date=%s, purpose=%s, updated_at=now() WHERE barcode=%s""",
                    (sid, clock.today(), due, purpose[:255], code))
        cur.execute("INSERT INTO equipment_custody_log (barcode, user_id, action, condition, note, channel) VALUES (%s,%s,'checkout',%s,%s,%s)", (code, sid, e["condition"], purpose[:255], actor.channel))
        audit.log(cur, actor, "equipment.checkout", "equipment", code, detail={"student_id": sid, "due": str(due)})
        events.emit(cur, EQUIPMENT_CHECKED_OUT, {"barcode": code, "name": e["name"], "user_id": sid, "due_date": str(due)})
    return {"ok": True, "barcode": code, "name": e["name"], "due_date": str(due), "message": f"{e['name']} ({code}) issued to {sid}; due back {due}."}


@op("equipment.return", "equipment", "barcode")
def return_equipment(actor: Actor, barcode: str, condition: str = "good", note: str = "") -> dict:
    code = (barcode or "").upper().strip()
    if condition not in CONDITIONS:
        raise ValidationFailed("Condition must be good, fair or damaged.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM lab_equipment WHERE barcode=%s FOR UPDATE", (code,))
        e = cur.fetchone()
        if not e:
            raise NotFound(f"No equipment with barcode {code}.")
        if e["status"] != "in_custody":
            raise RuleViolation(f"{code} is not currently checked out.")
        if e["custodian_id"] != actor.user_id and not actor.has_scope("requisition"):
            raise Forbidden("Only the current custodian (or lab staff) can return this item.")
        late_days = max(0, (clock.today() - e["due_date"]).days) if e["due_date"] else 0
        new_status = "under_repair" if condition == "damaged" else "available"
        cur.execute("""UPDATE lab_equipment SET status=%s, custodian_id=NULL, checkout_date=NULL, due_date=NULL, purpose=NULL, condition=%s, updated_at=now() WHERE barcode=%s""",
                    (new_status, condition, code))
        cur.execute("INSERT INTO equipment_custody_log (barcode, user_id, action, condition, note, channel) VALUES (%s,%s,'return',%s,%s,%s)",
                    (code, e["custodian_id"], condition, (note or "")[:255], actor.channel))
        audit.log(cur, actor, "equipment.return", "equipment", code, detail={"custodian": e["custodian_id"], "condition": condition, "late_days": late_days})
        events.emit(cur, EQUIPMENT_RETURNED, {"barcode": code, "name": e["name"], "user_id": e["custodian_id"], "condition": condition, "late_days": late_days})
    return {"ok": True, "barcode": code, "status": new_status, "late_days": late_days,
            "message": f"{e['name']} returned" + (f" ({late_days} day(s) late)" if late_days else "") + (" and flagged for repair." if new_status == "under_repair" else ".")}


@op("equipment.create", "equipment", "barcode")
def create_equipment(actor: Actor, barcode: str, name: str, lab: str, category: str = "", condition: str = "good") -> dict:
    actor.require_scope("requisition")
    code = (barcode or "").upper().strip()
    if not code or len(code) > 30 or not (name or "").strip() or not (lab or "").strip():
        raise ValidationFailed("Barcode, name and lab are required.")
    if condition not in CONDITIONS:
        raise ValidationFailed("Invalid condition.")
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM lab_equipment WHERE barcode=%s", (code,))
        if cur.fetchone():
            raise Conflict(f"Barcode {code} already exists.")
        cur.execute("INSERT INTO lab_equipment (barcode, name, lab, category, condition) VALUES (%s,%s,%s,%s,%s)", (code, name.strip(), lab.strip(), category, condition))
        cur.execute("INSERT INTO equipment_custody_log (barcode, user_id, action, condition, note) VALUES (%s,%s,'admin_update',%s,'added to inventory')", (code, actor.user_id, condition))
        audit.log(cur, actor, "equipment.create", "equipment", code, detail={"name": name, "lab": lab})
    return lookup(actor, code)


@op("equipment.update", "equipment", "barcode")
def update_equipment(actor: Actor, barcode: str, **fields) -> dict:
    actor.require_scope("requisition")
    allowed = {"name", "lab", "category", "condition", "status"}
    f = {k: v for k, v in fields.items() if k in allowed and v not in (None, "")}
    code = (barcode or "").upper().strip()
    if "condition" in f and f["condition"] not in CONDITIONS:
        raise ValidationFailed("Invalid condition.")
    if "status" in f and f["status"] not in ("available", "under_repair", "retired"):
        raise ValidationFailed("Status may be set to available, under_repair or retired (custody changes go through checkout/return).")
    with db.tx() as cur:
        cur.execute("SELECT * FROM lab_equipment WHERE barcode=%s FOR UPDATE", (code,))
        e = cur.fetchone()
        if not e:
            raise NotFound(f"No equipment with barcode {code}.")
        if e["status"] == "in_custody" and "status" in f:
            raise RuleViolation("Item is in student custody; record its return first.")
        if f:
            cur.execute(f"UPDATE lab_equipment SET {','.join(k + '=%s' for k in f)}, updated_at=now() WHERE barcode=%s", [*f.values(), code])
            cur.execute("INSERT INTO equipment_custody_log (barcode, user_id, action, condition, note) VALUES (%s,%s,'admin_update',%s,%s)",
                        (code, actor.user_id, f.get("condition", e["condition"]), str(f)[:255]))
        audit.log(cur, actor, "equipment.update", "equipment", code, detail=f)
    return lookup(actor, code)


def custody_log(actor: Actor, barcode: str | None = None, limit: int = 100) -> list[dict]:
    where, params = [], []
    if not actor.has_scope("requisition"):
        where.append("l.user_id=%s"); params.append(actor.user_id)
    if barcode:
        where.append("l.barcode=%s"); params.append(barcode.upper())
    return db.query(f"""SELECT l.*, e.name, u.full_name FROM equipment_custody_log l JOIN lab_equipment e USING (barcode) LEFT JOIN users u ON u.user_id=l.user_id
                        {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY l.at DESC LIMIT %s""", (*params, max(1, min(limit, 500))))
