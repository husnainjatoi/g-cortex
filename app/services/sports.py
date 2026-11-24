"""Sports facility reservations: fair-use quotas, double-booking protection, zero-trust check-in and no-show auto-release."""
import datetime
import json
import secrets

from app import db
from . import audit, clock, events, settings
from .actor import Actor
from .audit import op
from .errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed
from .requisitions import VENUE_BOOKED, VENUE_RELEASED

ACTIVE = ("approved", "pending", "checked_in")


def facilities(actor: Actor) -> list[dict]:
    return db.query("SELECT venue_id, name, capacity, facilities, description, exclusive FROM venues WHERE category='sports' AND is_active ORDER BY venue_id")


def _facility(cur, fid: str) -> dict:
    cur.execute("SELECT * FROM venues WHERE venue_id=%s", ((fid or "").upper().strip(),))
    v = cur.fetchone()
    if not v or v["category"] != "sports" or not v["is_active"]:
        raise NotFound(f"'{fid}' is not an available sports facility.")
    return dict(v)


def _standing(cur, user_id: str) -> dict:
    """No-show standing. An expired suspension is lifted lazily and strikes reset."""
    cur.execute("SELECT * FROM no_show_records WHERE user_id=%s FOR UPDATE", (user_id,))
    r = cur.fetchone()
    if not r:
        return {"strikes": 0, "suspended": False, "suspended_until": None}
    if r["suspended"] and r["suspended_until"] and r["suspended_until"] < clock.today():
        cur.execute("UPDATE no_show_records SET suspended=false, suspended_until=NULL, strikes=0 WHERE user_id=%s", (user_id,))
        return {"strikes": 0, "suspended": False, "suspended_until": None}
    return {"strikes": r["strikes"], "suspended": bool(r["suspended"]), "suspended_until": r["suspended_until"]}


def _used_hours(cur, user_id: str, d1: datetime.date, d2: datetime.date) -> float:
    cur.execute("""SELECT COALESCE(SUM(duration_hours),0) AS h FROM bookings WHERE user_id=%s AND source='sports_reservation'
                   AND status IN ('approved','pending','checked_in') AND start_ts::date BETWEEN %s AND %s""", (user_id, d1, d2))
    return float(cur.fetchone()["h"])


def quota(actor: Actor, student_id: str | None = None, date: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "requisition")
    d = clock.parse_date(date, "date") if date else clock.today()
    with db.tx() as cur:
        daily, weekly = settings.get(cur, "sports_daily_quota_hours"), settings.get(cur, "sports_weekly_quota_hours")
        monday = d - datetime.timedelta(days=d.weekday())
        used_d, used_w = _used_hours(cur, sid, d, d), _used_hours(cur, sid, monday, monday + datetime.timedelta(days=6))
        st = _standing(cur, sid)
        return {"student_id": sid, "date": str(d), "daily_limit_hours": daily, "daily_used_hours": used_d,
                "daily_remaining_hours": max(0.0, daily - used_d), "weekly_limit_hours": weekly, "weekly_used_hours": used_w,
                "weekly_remaining_hours": max(0.0, weekly - used_w), "no_show_strikes": st["strikes"],
                "strike_limit": settings.get(cur, "strike_limit"), "suspended": st["suspended"],
                "suspended_until": str(st["suspended_until"]) if st["suspended_until"] else None}


def availability(actor: Actor, facility_id: str, date: str) -> dict:
    d = clock.parse_date(date, "date")
    with db.tx() as cur:
        v = _facility(cur, facility_id)
        open_h, close_h = settings.get(cur, "sports_open_hour"), settings.get(cur, "sports_close_hour")
        cur.execute("""SELECT user_id, start_ts, end_ts, status FROM bookings WHERE venue_id=%s AND status IN ('approved','pending','checked_in') AND start_ts::date=%s""",
                    (v["venue_id"], d))
        taken = cur.fetchall()
        now = clock.now()
        slots = []
        for h in range(int(open_h), int(close_h)):
            s, e = datetime.datetime.combine(d, datetime.time(h)), datetime.datetime.combine(d, datetime.time(h)) + datetime.timedelta(hours=1)
            overlapping = [t for t in taken if t["start_ts"] < e and s < t["end_ts"]]
            mine = any(t["user_id"] == actor.user_id for t in overlapping)
            free = (len(overlapping) < (v["capacity"] if not v["exclusive"] else 1)) and s > now
            slots.append({"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M"), "available": free, "booked_by_me": mine,
                          "reason": None if free else ("in the past" if s <= now else "booked" if v["exclusive"] else "full")})
        return {"facility_id": v["venue_id"], "facility": v["name"], "date": str(d), "slots": slots,
                "available_slots": [s["start"] for s in slots if s["available"]]}


@op("sports.book", "booking", "facility_id")
def book(actor: Actor, facility_id: str, date: str, start_time: str, duration_hours: float = 1.0, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "requisition")
    d, t = clock.parse_date(date, "date"), clock.parse_time(start_time, "start time")
    try:
        dur = float(duration_hours)
    except (TypeError, ValueError):
        raise ValidationFailed("Duration must be a number of hours.")
    if dur <= 0 or (dur * 2) % 1 != 0:
        raise ValidationFailed("Duration must be a positive multiple of 0.5 hours.")
    if t.minute not in (0, 30):
        raise ValidationFailed("Sessions start on the hour or half hour.")
    with db.tx() as cur:
        cur.execute("SELECT role, is_active FROM users WHERE user_id=%s", (sid,))
        u = cur.fetchone()
        if not u or u["role"] != "student" or not u["is_active"]:
            raise ValidationFailed("Sports reservations are for active student accounts.")
        cur.execute("SELECT 1 FROM users WHERE user_id=%s FOR UPDATE", (sid,))  # serialise this student's quota accounting
        v = _facility(cur, facility_id)
        s = settings
        open_h, close_h, daily, weekly, adv, limit = (s.get(cur, k) for k in (
            "sports_open_hour", "sports_close_hour", "sports_daily_quota_hours", "sports_weekly_quota_hours", "sports_advance_days", "strike_limit"))
        start_dt = datetime.datetime.combine(d, t)
        end_dt = start_dt + datetime.timedelta(hours=dur)
        if start_dt <= clock.now():
            raise RuleViolation("That time has already passed.")
        if d > clock.today() + datetime.timedelta(days=adv):
            raise RuleViolation(f"Sports slots can only be booked up to {adv} days ahead.")
        if start_dt.hour < open_h or end_dt > datetime.datetime.combine(d, datetime.time(0)) + datetime.timedelta(hours=close_h):
            raise RuleViolation(f"Sports facilities are open {int(open_h):02d}:00-{int(close_h):02d}:00.")
        st = _standing(cur, sid)
        if st["suspended"] or st["strikes"] >= limit:
            raise RuleViolation(f"Booking privileges are suspended after {limit} no-shows" + (f" until {st['suspended_until']}." if st["suspended_until"] else "."), code="suspended")
        used_d = _used_hours(cur, sid, d, d)
        if used_d + dur > daily:
            raise RuleViolation(f"Daily fair-use quota exceeded: {used_d:g}h already booked on {d}; limit is {daily:g}h/day ({max(0, daily - used_d):g}h left).", code="daily_quota")
        monday = d - datetime.timedelta(days=d.weekday())
        used_w = _used_hours(cur, sid, monday, monday + datetime.timedelta(days=6))
        if used_w + dur > weekly:
            raise RuleViolation(f"Weekly fair-use quota exceeded: {used_w:g}h used this week; limit is {weekly:g}h ({max(0, weekly - used_w):g}h left).", code="weekly_quota")
        cur.execute("""SELECT 1 FROM bookings WHERE user_id=%s AND status IN ('approved','pending','checked_in') AND tsrange(start_ts,end_ts) && tsrange(%s,%s)""",
                    (sid, start_dt, end_dt))
        if cur.fetchone():
            raise RuleViolation("You already have another booking that overlaps this time.", code="self_overlap")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (v["venue_id"],))
        cur.execute("""SELECT COUNT(*) AS n FROM bookings WHERE venue_id=%s AND status IN ('approved','pending','checked_in') AND tsrange(start_ts,end_ts) && tsrange(%s,%s)""",
                    (v["venue_id"], start_dt, end_dt))
        if cur.fetchone()["n"] >= (1 if v["exclusive"] else v["capacity"]):
            raise RuleViolation(f"{v['name']} is already {'booked' if v['exclusive'] else 'full'} for that time.", code="slot_taken")
        cur.execute("SELECT nextval('booking_seq') AS n")
        code = f"BK-SPT-{cur.fetchone()['n']}"
        pin = f"CHK-{secrets.randbelow(10000):04d}"
        try:
            cur.execute("SAVEPOINT b")
            cur.execute("""INSERT INTO bookings (booking_code, venue_id, user_id, booking_time, booking_date, start_time, duration_hours, status, checkin_token,
                           checkin_status, source, start_ts, end_ts, slot_key, channel)
                           VALUES (%s,%s,%s,now(),%s,%s,%s,'approved',%s,'pending_checkin','sports_reservation',%s,%s,%s,%s) RETURNING booking_id""",
                        (code, v["venue_id"], sid, d, t.strftime("%H:%M"), dur, pin, start_dt, end_dt, v["venue_id"] if v["exclusive"] else None, actor.channel))
        except Exception as exc:
            if getattr(exc, "pgcode", None) == "23P01":
                cur.execute("ROLLBACK TO SAVEPOINT b")
                raise RuleViolation(f"{v['name']} was just booked by someone else for that time.", code="slot_taken")
            raise
        bid = cur.fetchone()["booking_id"]
        audit.log(cur, actor, "sports.book", "booking", code, detail={"student_id": sid, "facility": v["venue_id"], "start": str(start_dt), "hours": dur})
        events.emit(cur, VENUE_BOOKED, {"booking_id": bid, "booking_code": code, "user_id": sid, "venue": v["name"], "venue_id": v["venue_id"],
                                        "start": start_dt.isoformat(), "hours": dur, "kind": "sports"})
        return {"ok": True, "booking_id": bid, "booking_code": code, "facility": v["name"], "date": str(d), "start_time": t.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M"), "duration_hours": dur, "checkin_pin": pin,
                "daily_remaining_hours": max(0.0, daily - used_d - dur), "weekly_remaining_hours": max(0.0, weekly - used_w - dur),
                "message": f"Booked {v['name']} on {d} {t.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}. Check-in PIN {pin} (valid from "
                           f"{settings.get(cur, 'sports_checkin_early_minutes')} min before start until {settings.get(cur, 'checkin_grace_minutes')} min after)."}


def _booking(cur, ref, lock=True) -> dict:
    ref = str(ref).strip()
    if ref.isdigit():
        cur.execute("SELECT b.*, v.name AS venue_name FROM bookings b JOIN venues v USING (venue_id) WHERE b.booking_id=%s" + (" FOR UPDATE OF b" if lock else ""), (int(ref),))
    else:
        cur.execute("SELECT b.*, v.name AS venue_name FROM bookings b JOIN venues v USING (venue_id) WHERE b.booking_code=%s" + (" FOR UPDATE OF b" if lock else ""), (ref.upper(),))
    r = cur.fetchone()
    if not r:
        raise NotFound(f"Booking '{ref}' not found.")
    return dict(r)


@op("sports.cancel", "booking", "booking_ref")
def cancel(actor: Actor, booking_ref: str, reason: str = "") -> dict:
    with db.tx() as cur:
        b = _booking(cur, booking_ref)
        admin = actor.has_scope("requisition")
        if b["user_id"] != actor.user_id and not admin:
            raise Forbidden("You can only cancel your own bookings.")
        if b["status"] not in ("approved", "pending"):
            raise RuleViolation(f"A booking that is {b['status']} cannot be cancelled.")
        if b["requisition_id"] and not admin:
            raise RuleViolation("Event bookings are cancelled by cancelling the requisition.")
        cutoff = settings.get(cur, "cancel_cutoff_minutes")
        if not admin and clock.now() > b["start_ts"] - datetime.timedelta(minutes=cutoff):
            raise RuleViolation(f"Bookings cannot be cancelled within {cutoff} minutes of the start time.", code="cancel_window")
        cur.execute("UPDATE bookings SET status='cancelled', slot_key=NULL, cancelled_at=now(), cancel_reason=%s WHERE booking_id=%s",
                    ((reason or "cancelled by user")[:200], b["booking_id"]))
        audit.log(cur, actor, "sports.cancel", "booking", b["booking_code"], detail={"reason": reason})
        events.emit(cur, VENUE_RELEASED, {"booking_code": b["booking_code"], "user_id": b["user_id"], "venue": b["venue_name"], "venue_id": b["venue_id"],
                                          "start": b["start_ts"].isoformat(), "reason": "cancelled", "by": actor.user_id})
    return {"ok": True, "booking_code": b["booking_code"], "status": "cancelled", "message": f"Booking {b['booking_code']} cancelled; the slot is open again."}


@op("sports.checkin", "booking", "booking_ref")
def check_in(actor: Actor, booking_ref: str, pin: str) -> dict:
    with db.tx() as cur:
        b = _booking(cur, booking_ref)
        if b["user_id"] != actor.user_id:
            raise Forbidden("Only the booking holder can check in.")
        if b["status"] != "approved" or b["checkin_status"] != "pending_checkin":
            raise RuleViolation(f"Booking is {b['status']} and cannot be checked in.")
        early, grace = settings.get(cur, "sports_checkin_early_minutes"), settings.get(cur, "checkin_grace_minutes")
        now = clock.now()
        if now < b["start_ts"] - datetime.timedelta(minutes=early):
            raise RuleViolation(f"Check-in opens {early} minutes before the session starts.")
        if now > b["start_ts"] + datetime.timedelta(minutes=grace):
            raise RuleViolation("The check-in window has closed; the slot may be auto-released.")
        if (pin or "").strip().upper() != (b["checkin_token"] or ""):
            raise RuleViolation("Incorrect check-in PIN.")
        cur.execute("UPDATE bookings SET status='checked_in', checkin_status='checked_in' WHERE booking_id=%s", (b["booking_id"],))
        audit.log(cur, actor, "sports.checkin", "booking", b["booking_code"])
    return {"ok": True, "booking_code": b["booking_code"], "status": "checked_in", "message": "Checked in. Enjoy your session!"}


def my_bookings(actor: Actor, student_id: str | None = None, upcoming_only: bool = False) -> list[dict]:
    sid = actor.resolve_student(student_id, "requisition")
    rows = db.query(f"""SELECT b.booking_id, b.booking_code, b.venue_id, v.name AS venue_name, v.category, b.start_ts, b.end_ts, b.duration_hours, b.status,
                              b.checkin_status, b.checkin_token, b.source, b.requisition_id, b.cancel_reason
                       FROM bookings b JOIN venues v USING (venue_id) WHERE b.user_id=%s AND b.start_ts IS NOT NULL
                       {"AND b.end_ts >= now() AND b.status IN ('approved','pending','checked_in')" if upcoming_only else ""}
                       ORDER BY b.start_ts DESC LIMIT 200""", (sid,))
    now = clock.now()
    for r in rows:
        r["can_cancel"] = bool(r["status"] in ("approved", "pending") and r["source"] == "sports_reservation" and r["start_ts"] > now + datetime.timedelta(minutes=60))
        if r["status"] not in ("approved", "pending") or r["source"] != "sports_reservation":
            r["checkin_token"] = None
    return rows


def list_bookings(actor: Actor, date: str | None = None, venue_id: str | None = None, status: str | None = None) -> list[dict]:
    actor.require_scope("requisition")
    where, params = ["b.start_ts IS NOT NULL"], []
    if date:
        where.append("b.start_ts::date=%s"); params.append(clock.parse_date(date, "date"))
    if venue_id:
        where.append("b.venue_id=%s"); params.append(venue_id.upper())
    if status:
        where.append("b.status=%s"); params.append(status)
    return db.query(f"""SELECT b.booking_id, b.booking_code, b.venue_id, v.name AS venue_name, b.user_id, u.full_name, b.start_ts, b.end_ts,
                        b.status, b.checkin_status, b.source FROM bookings b JOIN venues v USING (venue_id) JOIN users u ON u.user_id=b.user_id
                        WHERE {' AND '.join(where)} ORDER BY b.start_ts DESC LIMIT 300""", params)


@op("sports.auto_release", "booking", None)
def auto_release(actor: Actor | None = None) -> dict:
    """Zero-trust no-show rule: un-checked-in sports slots are released after the grace period and a strike is recorded."""
    if actor is not None:
        actor.require_scope("requisition")
    sysactor = Actor("SYSTEM", "admin", frozenset({"super"}), "system", "System")
    released = []
    with db.tx() as cur:
        grace, limit, susp_days = (settings.get(cur, k) for k in ("checkin_grace_minutes", "strike_limit", "suspension_days"))
        cutoff = clock.now() - datetime.timedelta(minutes=grace)
        cur.execute("""SELECT b.*, v.name AS venue_name FROM bookings b JOIN venues v USING (venue_id)
                       WHERE b.source='sports_reservation' AND b.status='approved' AND b.checkin_status='pending_checkin' AND b.start_ts < %s
                       FOR UPDATE OF b SKIP LOCKED""", (cutoff,))
        for b in cur.fetchall():
            cur.execute("UPDATE bookings SET status='auto-released', checkin_status='auto-released', slot_key=NULL WHERE booking_id=%s", (b["booking_id"],))
            cur.execute("""INSERT INTO no_show_records (user_id, strikes, suspended, strike_history) VALUES (%s,0,false,'[]') ON CONFLICT (user_id) DO NOTHING""", (b["user_id"],))
            cur.execute("SELECT strikes, strike_history FROM no_show_records WHERE user_id=%s FOR UPDATE", (b["user_id"],))
            ns = cur.fetchone()
            hist = json.loads(ns["strike_history"] or "[]")
            hist.append({"booking_id": b["booking_code"], "reason": f"No-show past {grace}-minute grace window", "at": clock.now().isoformat()})
            strikes = ns["strikes"] + 1
            suspended = strikes >= limit
            cur.execute("UPDATE no_show_records SET strikes=%s, strike_history=%s, suspended=%s, suspended_until=%s WHERE user_id=%s",
                        (strikes, json.dumps(hist), suspended, clock.today() + datetime.timedelta(days=susp_days) if suspended else None, b["user_id"]))
            audit.log(cur, sysactor, "sports.auto_release", "booking", b["booking_code"], detail={"user_id": b["user_id"], "strikes": strikes, "suspended": suspended})
            events.emit(cur, VENUE_RELEASED, {"booking_code": b["booking_code"], "user_id": b["user_id"], "venue": b["venue_name"], "venue_id": b["venue_id"],
                                              "start": b["start_ts"].isoformat(), "reason": "no_show", "strikes": strikes, "suspended": suspended})
            released.append({"booking_code": b["booking_code"], "user_id": b["user_id"], "strikes": strikes, "suspended": suspended})
    return {"released": len(released), "bookings": released}
