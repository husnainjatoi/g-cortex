"""Requisition Management: venues, multi-tier society requisitions, and the natural-language venue recommender."""
import datetime
import json
import re

from app import db
from . import audit, clock, events, settings
from .actor import Actor
from .audit import op
from .errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed

REQUISITION_SUBMITTED = "requisition.submitted"
REQUISITION_ADVANCED = "requisition.advanced"
REQUISITION_APPROVED = "requisition.approved"
REQUISITION_REJECTED = "requisition.rejected"
REQUISITION_CANCELLED = "requisition.cancelled"
VENUE_BOOKED = "venue.booked"
VENUE_RELEASED = "venue.released"

TERMINAL = ("approved", "rejected", "cancelled")
VENUE_ID_RE = re.compile(r"^[A-Z0-9_]{3,20}$")


# ── Venues ──────────────────────────────────────────────────

def list_venues(actor: Actor, category: str | None = None, q: str | None = None, include_inactive: bool = False) -> list[dict]:
    if include_inactive:
        actor.require_scope("requisition")
    where, params = [], []
    if not include_inactive:
        where.append("is_active")
    if category:
        where.append("category=%s"); params.append(category)
    if q:
        where.append("(name ILIKE %s OR COALESCE(facilities,'') ILIKE %s OR COALESCE(description,'') ILIKE %s)"); params += [f"%{q}%"] * 3
    return db.query(f"SELECT * FROM venues {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY category, venue_id", params)


def venue_availability(actor: Actor, venue_id: str, date: str) -> dict:
    d = clock.parse_date(date, "date")
    v = db.query_one("SELECT * FROM venues WHERE venue_id=%s", ((venue_id or "").upper(),))
    if not v:
        raise NotFound("Venue not found.")
    rows = db.query("""SELECT booking_code, start_ts, end_ts, status, source FROM bookings WHERE venue_id=%s AND status IN ('approved','pending','checked_in')
                       AND start_ts::date = %s ORDER BY start_ts""", (v["venue_id"], d))
    return {"venue": v["name"], "venue_id": v["venue_id"], "date": str(d), "busy": [
        {"from": r["start_ts"].strftime("%H:%M"), "to": r["end_ts"].strftime("%H:%M"), "status": r["status"]} for r in rows]}


@op("venue.create", "venue", "venue_id")
def create_venue(actor: Actor, venue_id: str, name: str, capacity: int, category: str, facilities: str = "", description: str = "",
                 requires_approval: bool = True, exclusive: bool = True) -> dict:
    actor.require_scope("requisition")
    vid = (venue_id or "").upper().strip()
    if not VENUE_ID_RE.match(vid):
        raise ValidationFailed("Venue ID must be 3-20 characters: A-Z, 0-9, underscore.")
    _validate_venue(name, capacity, category)
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM venues WHERE venue_id=%s", (vid,))
        if cur.fetchone():
            raise Conflict(f"Venue {vid} already exists.")
        cur.execute("""INSERT INTO venues (venue_id, name, capacity, requires_approval, category, facilities, description, exclusive)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (vid, name.strip(), int(capacity), requires_approval, category.strip().lower(), facilities, description, exclusive))
        audit.log(cur, actor, "venue.create", "venue", vid, detail={"name": name, "capacity": capacity, "category": category})
    return db.query_one("SELECT * FROM venues WHERE venue_id=%s", (vid,))


def _validate_venue(name, capacity, category):
    if not (name or "").strip() or len(name) > 100:
        raise ValidationFailed("Venue name is required (max 100 characters).")
    try:
        if int(capacity) < 1 or int(capacity) > 5000:
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationFailed("Capacity must be between 1 and 5000.")
    if not (category or "").strip():
        raise ValidationFailed("Category is required.")


@op("venue.update", "venue", "venue_id")
def update_venue(actor: Actor, venue_id: str, **fields) -> dict:
    actor.require_scope("requisition")
    allowed = {"name", "capacity", "category", "facilities", "description", "requires_approval", "is_active", "exclusive"}
    f = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not f:
        raise ValidationFailed("Nothing to update.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM venues WHERE venue_id=%s FOR UPDATE", ((venue_id or "").upper(),))
        v = cur.fetchone()
        if not v:
            raise NotFound("Venue not found.")
        _validate_venue(f.get("name", v["name"]), f.get("capacity", v["capacity"]), f.get("category", v["category"]))
        cur.execute(f"UPDATE venues SET {','.join(k + '=%s' for k in f)} WHERE venue_id=%s", [*f.values(), v["venue_id"]])
        audit.log(cur, actor, "venue.update", "venue", v["venue_id"], detail=f)
    return db.query_one("SELECT * FROM venues WHERE venue_id=%s", (v["venue_id"],))


# ── Natural-language venue recommender ──────────────────────

_INTENT = [
    (("hackathon", "coding", "software", "programming", "workshop", "lab session"), {"LAB_SE": 40, "LAB_MEDS": 30, "SEM_HALL_1": 10}),
    (("hardware", "circuit", "fpga", "oscilloscope", "microcontroller", "embedded"), {"LAB_MEDS": 50}),
    (("seminar", "guest lecture", "symposium", "conference", "keynote", "talk", "panel", "demo day", "showcase"), {"AHA_AUDI": 35, "SEM_HALL_1": 40}),
    (("concert", "fest", "cultural", "performance", "open air", "festival", "carnival"), {"OAT_MAIN": 50, "AHA_AUDI": 25}),
    (("meeting", "cabinet", "interview", "boardroom", "committee"), {"CONF_RM_A": 50}),
]


def recommend_venue(actor: Actor, description: str, attendees: int, facilities: str = "", date: str | None = None,
                    start_time: str | None = None, duration_hours: float = 2.0) -> dict:
    try:
        attendees = int(attendees)
    except (TypeError, ValueError):
        raise ValidationFailed("Expected attendees must be a number.")
    if attendees < 1:
        raise ValidationFailed("Expected attendees must be at least 1.")
    desc = (description or "").lower()
    wants = [f.strip().lower().replace(" ", "_") for f in re.split(r"[,;]", facilities or "") if f.strip()]
    for kw in ("projector", "stage", "microphone", "mic", "sound", "air_conditioning", "ac", "video_conferencing"):
        if kw.replace("_", " ") in desc and kw not in wants:
            wants.append(kw)
    window = None
    if date and start_time:
        s = datetime.datetime.combine(clock.parse_date(date, "date"), clock.parse_time(start_time, "start time"))
        window = (s, s + datetime.timedelta(hours=float(duration_hours)))
    scored, excluded = [], []
    for v in db.query("SELECT * FROM venues WHERE is_active AND category <> 'sports'"):
        cap = v["capacity"] or 0
        if cap < attendees:
            excluded.append({"venue_id": v["venue_id"], "reason": f"capacity {cap} < {attendees}"}); continue
        free = True
        if window:
            clash = db.query_one("""SELECT 1 FROM bookings WHERE venue_id=%s AND status IN ('approved','pending','checked_in')
                                    AND tsrange(start_ts,end_ts) && tsrange(%s,%s)""", (v["venue_id"], *window))
            free = not clash
            if not free:
                excluded.append({"venue_id": v["venue_id"], "reason": "already booked in that window"}); continue
        score = 45 if attendees <= cap <= attendees * 1.5 else 30 if cap <= attendees * 3 else 15
        reasons = [f"seats {cap} for {attendees} expected ({round(100 * attendees / cap)}% full)"]
        for kws, boosts in _INTENT:
            if any(k in desc for k in kws) and v["venue_id"] in boosts:
                score += boosts[v["venue_id"]]; reasons.append(f"well suited to '{next(k for k in kws if k in desc)}' events")
        have = [f.strip().lower() for f in (v["facilities"] or "").split(",") if f.strip()]
        matched = sorted({h for w in wants for h in have if w in h or h in w})
        if matched:
            score += 10 * len(matched); reasons.append("has " + ", ".join(matched))
        scored.append({"venue_id": v["venue_id"], "name": v["name"], "capacity": cap, "requires_approval": v["requires_approval"],
                       "facilities": have, "match_score": min(score, 100), "reasons": reasons, "available_in_window": free if window else None})
    scored.sort(key=lambda x: (-x["match_score"], x["capacity"]))
    return {"query": description, "attendees": attendees, "recommendations": scored[:3], "excluded": excluded,
            "guidance": (f"Best match: {scored[0]['name']} ({scored[0]['match_score']}% match)." if scored
                         else "No active venue can host that many attendees on the requested slot.")}


# ── Society requisitions with multi-tier approval ───────────

def list_societies(actor: Actor) -> list[dict]:
    return db.query("SELECT society_name, advisor_name, category FROM campus_societies ORDER BY society_name")


def my_societies(actor: Actor) -> list[dict]:
    return db.query("""SELECT s.society_name, s.advisor_name, s.category, m.member_role FROM society_members m
                       JOIN campus_societies s USING (society_name) WHERE m.user_id=%s ORDER BY 1""", (actor.user_id,))


def _stages(chain: list[dict], status: str) -> list[dict]:
    out = []
    for n in chain:
        pending_here = n["action"] == "pending" and status.startswith("pending") and (
            (n["tier"] == 1 and status == "pending_advisor_approval") or (n["tier"] == 2 and status == "pending_admin_approval"))
        out.append({**n, "label": "Faculty Advisor" if n["tier"] == 1 else "Directorate of Student Affairs",
                    "state": "current" if pending_here else n["action"]})
    return out


def _fmt(r: dict) -> dict:
    chain = json.loads(r["approval_chain"]) if r.get("approval_chain") else []
    stages = _stages(chain, r["status"])
    cur_stage = next((s for s in stages if s["state"] == "current"), None)
    out = {k: v for k, v in r.items() if k != "approval_chain"}
    out["stages"] = stages
    out["pending_authority"] = (cur_stage.get("advisor_name") or "Directorate of Student Affairs") if cur_stage else None
    return out


@op("requisition.submit", "requisition", None)
def submit_requisition(actor: Actor, society_name: str, venue_id: str, event_title: str, booking_date: str, start_time: str,
                       duration_hours: float, expected_attendees: int, justification: str = "") -> dict:
    actor.require_role("student")
    d, t = clock.parse_date(booking_date, "Event date"), clock.parse_time(start_time, "Start time")
    try:
        dur, att = float(duration_hours), int(expected_attendees)
    except (TypeError, ValueError):
        raise ValidationFailed("Duration and attendees must be numbers.")
    if not (event_title or "").strip() or len(event_title) > 150:
        raise ValidationFailed("Event title is required (max 150 characters).")
    if att < 1:
        raise ValidationFailed("Expected attendees must be at least 1.")
    with db.tx() as cur:
        max_h, notice, max_open = (settings.get(cur, k) for k in ("requisition_max_hours", "requisition_notice_days", "requisition_max_pending"))
        if not 0.5 <= dur <= max_h:
            raise ValidationFailed(f"Event duration must be between 0.5 and {max_h} hours.")
        if d < clock.today() + datetime.timedelta(days=notice):
            raise RuleViolation(f"Requisitions need at least {notice} day(s) notice (earliest date {clock.today() + datetime.timedelta(days=notice)}).")
        start_dt = datetime.datetime.combine(d, t)
        end_dt = start_dt + datetime.timedelta(hours=dur)
        if end_dt.date() != d or t < datetime.time(7, 0):
            raise ValidationFailed("Events must start after 07:00 and end before midnight.")
        cur.execute("SELECT * FROM campus_societies WHERE society_name=%s", (society_name,))
        soc = cur.fetchone()
        if not soc:
            raise NotFound(f"Society '{society_name}' is not registered.")
        cur.execute("SELECT 1 FROM society_members WHERE society_name=%s AND user_id=%s", (soc["society_name"], actor.user_id))
        if not cur.fetchone():
            raise Forbidden(f"You are not a registered member of {soc['society_name']}.")
        cur.execute("SELECT * FROM venues WHERE venue_id=%s", ((venue_id or "").upper(),))
        v = cur.fetchone()
        if not v or not v["is_active"]:
            raise NotFound("That venue does not exist or is not available.")
        if v["category"] == "sports":
            raise RuleViolation("Sports facilities are reserved through Sports Booking, not society requisitions.")
        if att > v["capacity"]:
            raise RuleViolation(f"{v['name']} holds at most {v['capacity']} people; {att} were requested.", code="capacity_exceeded")
        cur.execute("SELECT COUNT(*) AS n FROM society_requisitions WHERE applicant_id=%s AND status IN ('pending_advisor_approval','pending_admin_approval')", (actor.user_id,))
        if cur.fetchone()["n"] >= max_open:
            raise RuleViolation(f"You already have {max_open} open requisitions awaiting approval.")
        cur.execute("""SELECT 1 FROM bookings WHERE venue_id=%s AND status IN ('approved','pending','checked_in')
                       AND tsrange(start_ts,end_ts) && tsrange(%s,%s)""", (v["venue_id"], start_dt, end_dt))
        if cur.fetchone():
            raise RuleViolation(f"{v['name']} is already reserved during {t.strftime('%H:%M')}-{end_dt.strftime('%H:%M')} on {d}.", code="venue_conflict")
        cur.execute("SELECT nextval('requisition_seq') AS n")
        rid = f"REQ-{d.year}-{cur.fetchone()['n']}"
        chain = [{"tier": 1, "role": "faculty_advisor", "assigned_to": soc["advisor_id"], "advisor_name": soc["advisor_name"], "action": "pending", "timestamp": None, "comments": None},
                 {"tier": 2, "role": "student_affairs_admin", "assigned_to": None, "advisor_name": None, "action": "pending", "timestamp": None, "comments": None}]
        cur.execute("""INSERT INTO society_requisitions (requisition_id, society_name, venue_id, applicant_id, event_title, booking_date, start_time,
                       duration_hours, expected_attendees, justification, current_tier, status, approval_chain)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'pending_advisor_approval',%s)""",
                    (rid, soc["society_name"], v["venue_id"], actor.user_id, event_title.strip(), d, t.strftime("%H:%M"), dur, att, justification, json.dumps(chain)))
        audit.log(cur, actor, "requisition.submit", "requisition", rid, detail={"venue": v["venue_id"], "date": str(d), "society": soc["society_name"]})
        events.emit(cur, REQUISITION_SUBMITTED, {"requisition_id": rid, "applicant_id": actor.user_id, "advisor_id": soc["advisor_id"],
                                                 "event_title": event_title.strip(), "venue": v["name"]})
    return {"ok": True, "requisition_id": rid, "status": "pending_advisor_approval", "next_approver": soc["advisor_name"],
            "message": f"Requisition {rid} submitted and routed to {soc['advisor_name']} (Tier 1: Faculty Advisor)."}


def _load_req(cur, rid: str, lock: bool = False) -> dict:
    cur.execute("""SELECT r.*, v.name AS venue_name, u.full_name AS applicant_name FROM society_requisitions r
                   JOIN venues v USING (venue_id) JOIN users u ON u.user_id = r.applicant_id WHERE r.requisition_id=%s""" + (" FOR UPDATE OF r" if lock else ""),
                ((rid or "").upper().strip(),))
    row = cur.fetchone()
    if not row:
        raise NotFound(f"Requisition '{rid}' not found.")
    return dict(row)


def _can_view(actor: Actor, r: dict, chain: list[dict]) -> bool:
    return (actor.user_id == r["applicant_id"] or actor.has_scope("requisition")
            or (actor.is_faculty and chain and chain[0].get("assigned_to") == actor.user_id))


def get_requisition(actor: Actor, requisition_id: str) -> dict:
    with db.tx() as cur:
        r = _load_req(cur, requisition_id)
        chain = json.loads(r["approval_chain"] or "[]")
        if not _can_view(actor, r, chain):
            raise Forbidden("You do not have access to this requisition.")
        return _fmt(r)


def list_requisitions(actor: Actor, scope: str = "mine", status: str | None = None) -> list[dict]:
    """scope: 'mine' (applicant), 'assigned' (faculty advisor queue), 'all' (admin)."""
    where, params = [], []
    if scope == "all":
        actor.require_scope("requisition")
    elif scope == "assigned":
        if not actor.is_faculty and not actor.has_scope("requisition"):
            raise Forbidden("Only faculty advisors can view the advisor queue.")
        if not actor.has_scope("requisition"):
            where.append("(r.approval_chain::jsonb -> 0 ->> 'assigned_to') = %s"); params.append(actor.user_id)
    else:
        where.append("r.applicant_id=%s"); params.append(actor.user_id)
    if status:
        where.append("r.status=%s"); params.append(status)
    rows = db.query(f"""SELECT r.*, v.name AS venue_name, u.full_name AS applicant_name FROM society_requisitions r JOIN venues v USING (venue_id)
                        JOIN users u ON u.user_id=r.applicant_id {('WHERE ' + ' AND '.join(where)) if where else ''}
                        ORDER BY r.created_at DESC LIMIT 300""", params)
    return [_fmt(r) for r in rows]


@op("requisition.review", "requisition", "requisition_id")
def review_requisition(actor: Actor, requisition_id: str, action: str, comments: str = "") -> dict:
    action = (action or "").lower().strip()
    if action not in ("approve", "reject"):
        raise ValidationFailed("Action must be 'approve' or 'reject'.")
    comments = (comments or "").strip()
    if action == "reject" and not comments:
        raise ValidationFailed("A reason is required when rejecting a requisition.")
    with db.tx() as cur:
        r = _load_req(cur, requisition_id, lock=True)
        rid = r["requisition_id"]
        chain = json.loads(r["approval_chain"] or "[]")
        if r["status"] in TERMINAL:
            raise RuleViolation(f"Requisition {rid} is already {r['status']}.")
        tier = 1 if r["status"] == "pending_advisor_approval" else 2
        node = chain[tier - 1]
        if tier == 1:
            if not ((actor.is_faculty and node["assigned_to"] == actor.user_id) or actor.has_scope("requisition")):
                raise Forbidden("Tier 1 review is restricted to the society's Faculty Advisor (or a Student Affairs administrator).")
        else:
            actor.require_scope("requisition")
        node.update(action="approved" if action == "approve" else "rejected", reviewer_id=actor.user_id,
                    timestamp=clock.now().isoformat(), comments=comments or None)
        if action == "reject":
            new_status = "rejected"
        elif tier == 1:
            new_status = "pending_admin_approval"
        else:
            new_status = "approved"
            start_dt = datetime.datetime.combine(r["booking_date"], clock.parse_time(r["start_time"]))
            end_dt = start_dt + datetime.timedelta(hours=float(r["duration_hours"]))
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (r["venue_id"],))
            cur.execute("""SELECT 1 FROM bookings WHERE venue_id=%s AND status IN ('approved','checked_in') AND tsrange(start_ts,end_ts) && tsrange(%s,%s)""",
                        (r["venue_id"], start_dt, end_dt))
            if cur.fetchone():
                raise RuleViolation(f"{r['venue_name']} was booked by someone else for that slot; the requisition cannot be approved. Reject it or ask the applicant to re-apply.", code="venue_conflict")
            cur.execute("SELECT nextval('booking_seq') AS n")
            code = f"BK-EVT-{cur.fetchone()['n']}"
            cur.execute("""INSERT INTO bookings (booking_code, venue_id, user_id, booking_time, booking_date, start_time, duration_hours, status,
                           checkin_status, source, start_ts, end_ts, slot_key, requisition_id)
                           VALUES (%s,%s,%s,now(),%s,%s,%s,'approved','not_required','society_requisition',%s,%s,%s,%s)""",
                        (code, r["venue_id"], r["applicant_id"], r["booking_date"], r["start_time"], r["duration_hours"], start_dt, end_dt, r["venue_id"], rid))
        cur.execute("UPDATE society_requisitions SET status=%s, current_tier=%s, approval_chain=%s WHERE requisition_id=%s",
                    (new_status, 2 if tier == 1 and action == "approve" else tier, json.dumps(chain), rid))
        audit.log(cur, actor, "requisition.review", "requisition", rid, detail={"tier": tier, "action": action, "status": new_status, "comments": comments})
        etype = (REQUISITION_REJECTED if action == "reject" else REQUISITION_APPROVED if new_status == "approved" else REQUISITION_ADVANCED)
        events.emit(cur, etype, {"requisition_id": rid, "applicant_id": r["applicant_id"], "tier": tier, "status": new_status,
                                 "event_title": r["event_title"], "venue": r["venue_name"], "comments": comments, "reviewer": actor.user_id})
    return {"ok": True, "requisition_id": rid, "status": new_status, "tier_processed": tier,
            "message": f"Requisition {rid} {'rejected at' if action == 'reject' else 'approved at'} Tier {tier}. Status: {new_status}."}


@op("requisition.cancel", "requisition", "requisition_id")
def cancel_requisition(actor: Actor, requisition_id: str) -> dict:
    with db.tx() as cur:
        r = _load_req(cur, requisition_id, lock=True)
        if r["applicant_id"] != actor.user_id and not actor.has_scope("requisition"):
            raise Forbidden("Only the applicant can cancel this requisition.")
        if r["status"] in ("rejected", "cancelled"):
            raise RuleViolation(f"Requisition is already {r['status']}.")
        if r["status"] == "approved" and r["booking_date"] <= clock.today():
            raise RuleViolation("An approved event on or before today can no longer be cancelled online.")
        cur.execute("UPDATE society_requisitions SET status='cancelled' WHERE requisition_id=%s", (r["requisition_id"],))
        cur.execute("UPDATE bookings SET status='cancelled', slot_key=NULL, cancelled_at=now(), cancel_reason='requisition cancelled' WHERE requisition_id=%s AND status='approved'", (r["requisition_id"],))
        audit.log(cur, actor, "requisition.cancel", "requisition", r["requisition_id"])
        events.emit(cur, REQUISITION_CANCELLED, {"requisition_id": r["requisition_id"], "applicant_id": r["applicant_id"], "event_title": r["event_title"]})
    return {"ok": True, "requisition_id": r["requisition_id"], "status": "cancelled", "message": f"Requisition {r['requisition_id']} cancelled and any venue hold released."}
