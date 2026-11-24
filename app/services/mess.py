"""Mess Management business logic: mess-out requests, rebate ledger, TOTP QR entry, menu/macros, forecasting."""
import base64
import datetime
import hashlib
import hmac
import io
import secrets
import struct

from app import config, db
from . import audit, clock, events, settings
from .actor import Actor
from .audit import op
from .errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed

MESS_OUT_SUBMITTED = "mess.out_submitted"
MESS_OUT_APPROVED = "mess.out_approved"
MESS_OUT_REJECTED = "mess.out_rejected"
MESS_OUT_CANCELLED = "mess.out_cancelled"
MESS_ENTRY_LOGGED = "mess.entry_logged"
GATE_PASS_SYNCED = "mess.gate_pass_synced"

MEALS = ("breakfast", "lunch", "dinner")
TOTP_STEP = 30


def _meal(value: str | None) -> str:
    m = (value or "").lower().strip()
    if m not in MEALS:
        raise ValidationFailed("Meal must be breakfast, lunch or dinner.")
    return m


def current_meal(now: datetime.datetime | None = None) -> str:
    h = (now or clock.now()).hour
    return "breakfast" if h < 11 else "lunch" if h < 16 else "dinner"


def _student(cur, user_id: str) -> dict:
    cur.execute("SELECT u.user_id, u.role, u.is_active, p.allergies FROM users u LEFT JOIN student_profiles p USING (user_id) WHERE u.user_id=%s", (user_id,))
    u = cur.fetchone()
    if not u:
        raise NotFound("Student not found.")
    if u["role"] != "student":
        raise ValidationFailed("This operation applies to student accounts.")
    return u


# ── Menu, macros, allergen shield ───────────────────────────

def menu(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess") if actor.is_student or student_id else None
    rows = db.query("SELECT * FROM daily_menu ORDER BY CASE meal_period WHEN 'breakfast' THEN 1 WHEN 'lunch' THEN 2 ELSE 3 END")
    allergies = ""
    if sid:
        p = db.query_one("SELECT allergies FROM student_profiles WHERE user_id=%s", (sid,))
        allergies = (p or {}).get("allergies") or "none"
    out = []
    for r in rows:
        item = {"meal_period": r["meal_period"], "items": [i.strip() for i in r["items"].split(",") if i.strip()],
                "calories": r["calories"], "protein_g": r["protein_g"], "carbs_g": r["carbs_g"], "fat_g": r["fat_g"],
                "allergens": [a.strip() for a in r["allergens"].split(",") if a.strip()]}
        if sid:
            item["allergen_shield"] = allergen_check(item["allergens"], allergies)
        out.append(item)
    return {"meals": out, "student_allergies": allergies or None}


def allergen_check(meal_allergens: list[str], student_allergies: str) -> dict:
    mine = {a.strip().lower() for a in (student_allergies or "").split(",") if a.strip() and a.strip().lower() != "none"}
    hit = sorted(a for a in meal_allergens if a.lower() in mine)
    return {"safe": not hit, "triggered": hit,
            "message": (f"WARNING: contains {', '.join(hit)} which matches your declared allergies." if hit
                        else "No declared allergens detected in this meal.")}


@op("mess.set_allergies", "user", "student_id")
def set_allergies(actor: Actor, allergies: str, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    cleaned = ",".join(sorted({a.strip().lower() for a in (allergies or "").split(",") if a.strip()})) or "none"
    if len(cleaned) > 200:
        raise ValidationFailed("Allergy list is too long.")
    with db.tx() as cur:
        _student(cur, sid)
        cur.execute("INSERT INTO student_profiles (user_id, allergies) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET allergies=EXCLUDED.allergies", (sid, cleaned))
        audit.log(cur, actor, "mess.set_allergies", "user", sid, detail={"allergies": cleaned})
    return {"ok": True, "allergies": cleaned}


@op("mess.update_menu", "menu", "meal_period")
def update_menu(actor: Actor, meal_period: str, items: str, calories: int, protein_g: int, carbs_g: int, fat_g: int, allergens: str) -> dict:
    actor.require_scope("mess")
    meal = _meal(meal_period)
    try:
        nums = [int(calories), int(protein_g), int(carbs_g), int(fat_g)]
    except (TypeError, ValueError):
        raise ValidationFailed("Macros must be whole numbers.")
    if any(n < 0 or n > 5000 for n in nums) or not (items or "").strip():
        raise ValidationFailed("Menu items are required and macros must be between 0 and 5000.")
    with db.tx() as cur:
        cur.execute("""INSERT INTO daily_menu (meal_period, items, calories, protein_g, carbs_g, fat_g, allergens)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (meal_period) DO UPDATE SET items=EXCLUDED.items,
                       calories=EXCLUDED.calories, protein_g=EXCLUDED.protein_g, carbs_g=EXCLUDED.carbs_g,
                       fat_g=EXCLUDED.fat_g, allergens=EXCLUDED.allergens""",
                    (meal, items.strip(), *nums, (allergens or "none").strip()))
        audit.log(cur, actor, "mess.update_menu", "menu", meal, detail={"items": items, "macros": nums, "allergens": allergens})
    return {"ok": True, "meal_period": meal}


# ── Mess-out requests and rebate calculation ────────────────

def _evaluate(cur, user_id: str, start, end, *, ignore_notice: bool = False) -> dict:
    s, e = clock.parse_date(start, "Start date"), clock.parse_date(end, "End date")
    problems: list[str] = []
    if e < s:
        raise ValidationFailed("End date must be on or after the start date.")
    days = (e - s).days + 1
    rate, min_days, max_days, notice = (settings.get(cur, k) for k in ("mess_daily_rate", "mess_min_days", "mess_max_days", "mess_notice_days"))
    if days < min_days:
        problems.append(f"A mess-out must cover at least {min_days} consecutive days (you asked for {days}).")
    if days > max_days:
        problems.append(f"A mess-out cannot exceed {max_days} days.")
    earliest = clock.today() + datetime.timedelta(days=notice)
    if not ignore_notice and s < earliest:
        problems.append(f"Mess-out requests need {notice} day(s) notice: the earliest start date is {earliest.isoformat()}.")
    cur.execute("""SELECT request_id, start_date, end_date, status FROM mess_out_requests
                   WHERE user_id=%s AND status IN ('pending','approved') AND daterange(start_date,end_date,'[]') && daterange(%s,%s,'[]')""",
                (user_id, s, e))
    clash = cur.fetchone()
    if clash:
        problems.append(f"Overlaps your {clash['status']} mess-out #{clash['request_id']} ({clash['start_date']} to {clash['end_date']}).")
    cur.execute("""SELECT gate_pass_id, destination FROM gate_passes WHERE user_id=%s AND approved
                   AND leave_start <= %s AND leave_end >= %s LIMIT 1""", (user_id, s, e))
    gp = cur.fetchone()
    return {"start_date": s.isoformat(), "end_date": e.isoformat(), "days": days, "daily_rate_pkr": rate,
            "rebate_pkr": round(days * rate, 2), "eligible": not problems, "problems": problems,
            "gate_pass_verified": bool(gp), "gate_pass_destination": gp["destination"] if gp else None,
            "outcome_if_submitted": ("approved automatically (matching security gate-pass on file)" if gp else
                                     "pending approval by the Mess Office"),
            "earliest_start_date": earliest.isoformat()}


def preview_mess_out(actor: Actor, start_date: str, end_date: str, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    with db.tx() as cur:
        _student(cur, sid)
        return {"student_id": sid, **_evaluate(cur, sid, start_date, end_date)}


@op("mess.out_submit", "mess_out", None)
def submit_mess_out(actor: Actor, start_date: str, end_date: str, reason: str | None = None, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    reason = (reason or "").strip()[:255] or None
    with db.tx() as cur:
        _student(cur, sid)
        cur.execute("SELECT 1 FROM users WHERE user_id=%s FOR UPDATE", (sid,))  # serialise a student's own submissions
        ev = _evaluate(cur, sid, start_date, end_date)
        if not ev["eligible"]:
            raise RuleViolation(" ".join(ev["problems"]), code="mess_out_rejected", details={"problems": ev["problems"]})
        auto = ev["gate_pass_verified"]
        try:
            cur.execute("SAVEPOINT ins")
            cur.execute("""INSERT INTO mess_out_requests (user_id, start_date, end_date, days_count, reason, status, est_rebate_pkr,
                           gate_pass_synced, channel, decided_at, decision_note)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING request_id""",
                        (sid, ev["start_date"], ev["end_date"], ev["days"], reason, "approved" if auto else "pending", ev["rebate_pkr"],
                         auto, actor.channel, clock.now() if auto else None, "Auto-approved via gate-pass synchronisation" if auto else None))
        except Exception as exc:  # exclusion constraint = lost a race against a concurrent overlapping request
            if getattr(exc, "pgcode", None) == "23P01":
                cur.execute("ROLLBACK TO SAVEPOINT ins")
                raise Conflict("You already have a mess-out request covering these dates.")
            raise
        rid = cur.fetchone()["request_id"]
        payload = {"request_id": rid, "user_id": sid, "start_date": ev["start_date"], "end_date": ev["end_date"],
                   "days": ev["days"], "rebate_pkr": ev["rebate_pkr"]}
        audit.log(cur, actor, "mess.out_submit", "mess_out", rid, detail={**payload, "auto_approved": auto})
        events.emit(cur, MESS_OUT_APPROVED if auto else MESS_OUT_SUBMITTED, payload)
        return {"ok": True, "request_id": rid, "status": "approved" if auto else "pending", **ev,
                "message": (f"Mess-out #{rid} approved ({ev['days']} days). Rebate of PKR {ev['rebate_pkr']:.0f} credited to your ledger."
                            if auto else f"Mess-out #{rid} submitted for Mess Office approval ({ev['days']} days, estimated rebate PKR {ev['rebate_pkr']:.0f}).")}


@op("mess.out_cancel", "mess_out", "request_id")
def cancel_mess_out(actor: Actor, request_id: int, student_id: str | None = None) -> dict:
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_out_requests WHERE request_id=%s FOR UPDATE", (request_id,))
        r = cur.fetchone()
        if not r:
            raise NotFound("Mess-out request not found.")
        if r["user_id"] != actor.user_id and not actor.has_scope("mess"):
            raise Forbidden("You can only cancel your own mess-out requests.")
        if r["status"] not in ("pending", "approved"):
            raise RuleViolation(f"A {r['status']} request cannot be cancelled.")
        if r["start_date"] <= clock.today():
            raise RuleViolation("This mess-out has already started and can no longer be cancelled.")
        cur.execute("UPDATE mess_out_requests SET status='cancelled', decided_at=now(), decided_by=%s WHERE request_id=%s", (actor.user_id, request_id))
        audit.log(cur, actor, "mess.out_cancel", "mess_out", request_id)
        events.emit(cur, MESS_OUT_CANCELLED, {"request_id": request_id, "user_id": r["user_id"], "was_approved": r["status"] == "approved"})
    return {"ok": True, "request_id": request_id, "status": "cancelled", "message": f"Mess-out #{request_id} cancelled."}


@op("mess.out_decide", "mess_out", "request_id")
def decide_mess_out(actor: Actor, request_id: int, approve: bool, note: str | None = None) -> dict:
    actor.require_scope("mess")
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_out_requests WHERE request_id=%s FOR UPDATE", (request_id,))
        r = cur.fetchone()
        if not r:
            raise NotFound("Mess-out request not found.")
        if r["status"] != "pending":
            raise RuleViolation(f"Request #{request_id} is already {r['status']}.")
        status = "approved" if approve else "rejected"
        cur.execute("UPDATE mess_out_requests SET status=%s, decided_by=%s, decided_at=now(), decision_note=%s WHERE request_id=%s",
                    (status, actor.user_id, (note or "")[:255] or None, request_id))
        payload = {"request_id": request_id, "user_id": r["user_id"], "start_date": r["start_date"], "end_date": r["end_date"],
                   "days": r["days_count"], "rebate_pkr": r["est_rebate_pkr"], "note": note}
        audit.log(cur, actor, "mess.out_decide", "mess_out", request_id, detail={"decision": status, "note": note})
        events.emit(cur, MESS_OUT_APPROVED if approve else MESS_OUT_REJECTED, payload)
    return {"ok": True, "request_id": request_id, "status": status}


def list_mess_out(actor: Actor, student_id: str | None = None, status: str | None = None, all_students: bool = False) -> list[dict]:
    where, params = [], []
    if all_students:
        actor.require_scope("mess")
    else:
        where.append("r.user_id=%s"); params.append(actor.resolve_student(student_id, "mess"))
    if status:
        where.append("r.status=%s"); params.append(status)
    rows = db.query(
        f"""SELECT r.*, u.full_name, reb.rebate_id, reb.rebate_amount_pkr AS credited_pkr,
                   CASE WHEN r.status='approved' AND r.end_date < CURRENT_DATE THEN 'past'
                        WHEN r.status='approved' AND r.start_date <= CURRENT_DATE THEN 'ongoing'
                        WHEN r.status='approved' THEN 'upcoming' END AS phase
            FROM mess_out_requests r JOIN users u USING (user_id) LEFT JOIN mess_rebates reb ON reb.mess_out_request_id = r.request_id
            {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY r.start_date DESC, r.request_id DESC LIMIT 200""", params)
    for r in rows:
        r.pop("span", None)
    return rows


def rebate_ledger(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    rows = db.query("""SELECT rebate_id, start_date, end_date, days_count, rebate_amount_pkr, status, gate_pass_synced, applied_at, mess_out_request_id
                       FROM mess_rebates WHERE user_id=%s ORDER BY applied_at DESC, rebate_id DESC""", (sid,))
    credited = sum(float(r["rebate_amount_pkr"]) for r in rows if r["status"] == "approved")
    return {"student_id": sid, "transactions": rows, "total_credited_pkr": credited,
            "total_days": sum(r["days_count"] for r in rows if r["status"] == "approved")}


def my_gate_passes(actor: Actor, student_id: str | None = None) -> list[dict]:
    sid = actor.resolve_student(student_id, "mess")
    return db.query("SELECT gate_pass_id, approved, leave_start, leave_end, destination, synced_at FROM gate_passes WHERE user_id=%s ORDER BY leave_start DESC NULLS LAST", (sid,))


# ── Gate-pass synchronisation (REST integration point for the campus security gate controller) ──

@op("mess.gate_pass_sync", "gate_pass", "user_id")
def sync_gate_pass(actor: Actor | None, user_id: str, leave_start: str, leave_end: str, destination: str | None = None,
                   approved: bool = True, external_ref: str | None = None) -> dict:
    if actor is not None:
        actor.require_scope("mess")
    s, e = clock.parse_date(leave_start, "leave_start"), clock.parse_date(leave_end, "leave_end")
    if e < s:
        raise ValidationFailed("leave_end must be on or after leave_start.")
    with db.tx() as cur:
        _student(cur, user_id)
        if external_ref:
            cur.execute("SELECT gate_pass_id FROM gate_passes WHERE external_ref=%s FOR UPDATE", (external_ref,))
            ex = cur.fetchone()
        else:
            ex = None
        if ex:
            cur.execute("UPDATE gate_passes SET approved=%s, leave_start=%s, leave_end=%s, destination=%s, synced_at=now() WHERE gate_pass_id=%s RETURNING gate_pass_id",
                        (approved, s, e, destination, ex["gate_pass_id"]))
        else:
            cur.execute("INSERT INTO gate_passes (user_id, approved, leave_start, leave_end, destination, external_ref, synced_at) VALUES (%s,%s,%s,%s,%s,%s,now()) RETURNING gate_pass_id",
                        (user_id, approved, s, e, destination, external_ref))
        gid = cur.fetchone()["gate_pass_id"]
        audit.log(cur, actor or Actor("GATE", "system", frozenset(), "system"), "mess.gate_pass_sync", "gate_pass", gid,
                  detail={"user_id": user_id, "leave_start": str(s), "leave_end": str(e), "approved": approved})
        events.emit(cur, GATE_PASS_SYNCED, {"gate_pass_id": gid, "user_id": user_id, "approved": approved, "leave_start": str(s), "leave_end": str(e)})
    return {"ok": True, "gate_pass_id": gid}


# ── TOTP dynamic QR ─────────────────────────────────────────

def _hotp(secret_hex: str, counter: int, digits: int = 8) -> str:
    mac = hmac.new(bytes.fromhex(secret_hex), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def _secret(cur, user_id: str) -> str:
    cur.execute("SELECT secret FROM mess_totp_secrets WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    if row:
        return row["secret"]
    s = secrets.token_hex(20)
    cur.execute("INSERT INTO mess_totp_secrets (user_id, secret) VALUES (%s,%s) ON CONFLICT (user_id) DO NOTHING", (user_id, s))
    cur.execute("SELECT secret FROM mess_totp_secrets WHERE user_id=%s", (user_id,))
    return cur.fetchone()["secret"]


def _qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _active_mess_out(cur, user_id: str, day: datetime.date) -> dict | None:
    cur.execute("SELECT request_id, start_date, end_date FROM mess_out_requests WHERE user_id=%s AND status='approved' AND %s BETWEEN start_date AND end_date", (user_id, day))
    return cur.fetchone()


@op("mess.qr_generate", "qr", "meal")
def generate_qr(actor: Actor, meal: str | None = None, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    meal = _meal(meal) if meal else current_meal()
    with db.tx() as cur:
        _student(cur, sid)
        cur.execute("SELECT 1 FROM daily_menu WHERE meal_period=%s", (meal,))
        if not cur.fetchone():
            raise NotFound("That meal is not served.")
        away = _active_mess_out(cur, sid, clock.today())
        if away:
            raise RuleViolation(f"You are on an approved mess-out until {away['end_date']}; dining entry is suspended.")
        now = clock.now().timestamp()
        step = int(now // TOTP_STEP)
        code = _hotp(_secret(cur, sid), step)
        payload = f"GCQR:{sid}:{meal}:{code}"
    return {"student_id": sid, "meal": meal, "payload": payload, "code": code, "step": step,
            "expires_in_seconds": TOTP_STEP - int(now % TOTP_STEP), "step_seconds": TOTP_STEP, "svg": _qr_svg(payload)}


def verify_entry(actor: Actor | None, payload: str, meal: str | None = None, gate_key_ok: bool = False) -> dict:
    """Turnstile scan. Authorised for Mess Office staff or the gate controller (shared key checked by the API layer)."""
    if not gate_key_ok:
        if actor is None:
            raise Forbidden("Scanner authentication required.")
        actor.require_scope("mess")
    scanner = actor or Actor("GATE", "system", frozenset(), "system")

    def reject(reason: str, user: str | None = None):
        audit.log_independent(scanner, "mess.entry_scan", "qr", user, "denied", {"reason": reason})
        raise RuleViolation(reason, code="entry_denied")

    parts = (payload or "").strip().split(":")
    if len(parts) != 4 or parts[0] != "GCQR":
        reject("Malformed QR payload.")
    _, uid, token_meal, code = parts
    meal = _meal(meal) if meal else current_meal()
    if token_meal != meal:
        reject(f"QR was issued for {token_meal}, but the current service is {meal}.", uid)
    now = clock.now().timestamp()
    step_now = int(now // TOTP_STEP)
    with db.tx() as cur:
        cur.execute("SELECT is_active FROM users WHERE user_id=%s AND role='student'", (uid,))
        u = cur.fetchone()
        if not u or not u["is_active"]:
            reject("Unknown or inactive student.", uid)
        cur.execute("SELECT secret FROM mess_totp_secrets WHERE user_id=%s", (uid,))
        sec = cur.fetchone()
        matched = None
        if sec:
            for st in (step_now, step_now - 1):  # allow one step of clock drift
                if hmac.compare_digest(_hotp(sec["secret"], st), code):
                    matched = st
                    break
        if matched is None:
            reject("Invalid or expired QR code.", uid)
        away = _active_mess_out(cur, uid, clock.today())
        if away:
            reject(f"Active mess-out #{away['request_id']} until {away['end_date']}: dining access suspended.", uid)
        cur.execute("INSERT INTO mess_qr_redemptions (user_id, meal, step) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (uid, meal, matched))
        if cur.rowcount == 0:
            reject("This QR code was already used (replay blocked).", uid)
        cur.execute("""INSERT INTO mess_ledger (user_id, meal_date, meal_type, status, source) VALUES (%s,%s,%s,'consumed','turnstile')
                       ON CONFLICT (user_id, meal_date, meal_type, status) DO NOTHING""", (uid, clock.today(), meal))
        if cur.rowcount == 0:
            cur.connection.rollback()
            reject(f"Anti-passback: {uid} has already entered for {meal} today.", uid)
        audit.log(cur, scanner, "mess.entry_scan", "qr", uid, detail={"meal": meal})
        events.emit(cur, MESS_ENTRY_LOGGED, {"user_id": uid, "meal": meal, "date": str(clock.today())})
    return {"status": "access_granted", "turnstile_action": "UNLOCK_MOMENTARY", "student_id": uid, "meal": meal,
            "timestamp": clock.now().isoformat()}


# ── Meal records, nutrition, attendance, forecasting, reports ───

def meal_records(actor: Actor, student_id: str | None = None, days: int = 30) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    days = max(1, min(int(days), 180))
    rows = db.query("""SELECT l.entry_id, l.meal_date, l.meal_type, l.status, l.source, d.calories, d.protein_g, d.carbs_g, d.fat_g
                       FROM mess_ledger l LEFT JOIN daily_menu d ON d.meal_period = l.meal_type
                       WHERE l.user_id=%s AND l.meal_date >= CURRENT_DATE - %s ORDER BY l.meal_date DESC, l.entry_id DESC""", (sid, days))
    consumed = [r for r in rows if r["status"] == "consumed"]
    daily: dict[str, dict] = {}
    for r in consumed:
        d = daily.setdefault(str(r["meal_date"]), {"date": str(r["meal_date"]), "calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "meals": 0})
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            d[k] += r[k] or 0
        d["meals"] += 1
    return {"student_id": sid, "records": rows, "daily_nutrition": sorted(daily.values(), key=lambda x: x["date"], reverse=True),
            "summary": {"meals_consumed": len(consumed), "meals_rebated": sum(1 for r in rows if r["status"] == "rebated"),
                        "avg_daily_calories": round(sum(d["calories"] for d in daily.values()) / len(daily)) if daily else 0}}


def profile(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    led = rebate_ledger(actor, sid)
    mo = [r for r in list_mess_out(actor, sid) if r["status"] in ("pending", "approved")]
    prof = db.query_one("SELECT allergies FROM student_profiles WHERE user_id=%s", (sid,))
    cfg = db.query("SELECT key, value FROM system_settings WHERE key LIKE 'mess_%%'")
    return {"student_id": sid, "allergies": (prof or {}).get("allergies", "none"),
            "gate_passes": my_gate_passes(actor, sid), "mess_out_requests": mo, "total_rebate_credited_pkr": led["total_credited_pkr"],
            "rules": {r["key"]: r["value"] for r in cfg}}


def attendance(actor: Actor, date: str | None = None, meal: str | None = None) -> dict:
    actor.require_scope("mess")
    d = clock.parse_date(date, "date") if date else clock.today()
    params, extra = [d], ""
    if meal:
        extra = " AND l.meal_type=%s"; params.append(_meal(meal))
    rows = db.query(f"""SELECT l.entry_id, l.user_id, u.full_name, l.meal_type, l.status, l.source, l.created_at
                        FROM mess_ledger l JOIN users u USING (user_id) WHERE l.meal_date=%s{extra} ORDER BY l.created_at DESC, l.entry_id DESC""", params)
    return {"date": str(d), "entries": rows,
            "counts": {m: sum(1 for r in rows if r["meal_type"] == m and r["status"] == "consumed") for m in MEALS}}


def forecast(actor: Actor, meal: str, date: str | None = None, day_type: str | None = None) -> dict:
    """Predictive kitchen demand: seasonal (same weekday) moving average of real turnstile entries, adjusted for
    approved mess-outs on the target date. Falls back to calendar multipliers when no history exists."""
    if not (actor.has_scope("mess") or actor.is_faculty):
        raise Forbidden("Demand forecasts are available to Mess Office staff and faculty.")
    meal = _meal(meal)
    d = clock.parse_date(date, "date") if date else clock.today() + datetime.timedelta(days=1)
    with db.tx() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='student' AND is_active")
        total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(DISTINCT user_id) AS n FROM mess_out_requests WHERE status='approved' AND %s BETWEEN start_date AND end_date", (d,))
        away = cur.fetchone()["n"]
        cur.execute("""SELECT meal_date, COUNT(*) AS n FROM mess_ledger WHERE meal_type=%s AND status='consumed'
                       AND meal_date < %s AND meal_date >= %s::date - 56 GROUP BY meal_date""", (meal, d, d))
        hist = cur.fetchall()
    same_dow = [h["n"] for h in hist if h["meal_date"].weekday() == d.weekday()]
    sample = same_dow or [h["n"] for h in hist]
    mult = {"regular": 0.88, "weekend": 0.65, "midterm_exam": 0.72, "pre_holiday": 0.55}
    if sample:
        w = list(range(1, len(sample) + 1))  # later samples weigh more (list is date-ascending is not guaranteed -> sort)
        ordered = [h["n"] for h in sorted([h for h in hist if (h["meal_date"].weekday() == d.weekday()) or not same_dow], key=lambda x: x["meal_date"])]
        w = list(range(1, len(ordered) + 1))
        avg = sum(n * wi for n, wi in zip(ordered, w)) / sum(w)
        predicted = max(0, min(total - away, round(avg)))
        method = f"weighted moving average of {len(ordered)} past {'same-weekday ' if same_dow else ''}service(s)"
    else:
        rate = mult.get((day_type or ("weekend" if d.weekday() >= 5 else "regular")), 0.85)
        predicted = round((total - away) * rate)
        method = f"no turnstile history yet; calendar multiplier ({day_type or 'regular'})"
    buffer = round(predicted * 1.05)
    return {"meal": meal, "date": str(d), "enrolled_students": total, "approved_mess_outs": away,
            "predicted_headcount": predicted, "recommended_production": buffer, "method": method,
            "estimated_overproduction_avoided_pct": round(100 * (total - buffer) / total, 1) if total else 0,
            "note": "Statistical forecast from ledger data; accuracy improves as turnstile history accumulates."}


def rebate_summary(actor: Actor) -> dict:
    actor.require_scope("mess")
    row = db.query_one("""SELECT COUNT(*) FILTER (WHERE status='approved') AS approved, COALESCE(SUM(rebate_amount_pkr) FILTER (WHERE status='approved'),0) AS total_pkr,
                          COALESCE(SUM(days_count) FILTER (WHERE status='approved'),0) AS total_days FROM mess_rebates""")
    pend = db.query_one("SELECT COUNT(*) AS n FROM mess_out_requests WHERE status='pending'")["n"]
    return {**row, "total_pkr": float(row["total_pkr"]), "pending_requests": pend}
