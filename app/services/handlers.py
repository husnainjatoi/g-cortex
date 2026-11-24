"""Cross-module event handlers (the event-driven pipeline). Every handler is idempotent and runs in its own transaction."""
import datetime

from . import courses, equipment, events, mess, requisitions
from .notifications import notify, notify_admins
from .events import subscribe


# ── Course ──────────────────────────────────────────────────
@subscribe(courses.COURSE_ENROLLED)
def notify_course_enrolled(cur, p):
    notify(cur, p["user_id"], "course", f"Registered for {p['course_code']}", f"{p['title']} - {p['term']}", "success", "courses")


@subscribe(courses.COURSE_DROPPED)
def notify_course_dropped(cur, p):
    notify(cur, p["user_id"], "course", f"Dropped {p['course_code']}", f"Your {p['term']} registration was removed.", "info", "courses")


# ── Mess: the approval drives the rebate ledger ─────────────
@subscribe(mess.MESS_OUT_SUBMITTED)
def mess_out_submitted(cur, p):
    notify(cur, p["user_id"], "mess", f"Mess-out #{p['request_id']} submitted", f"{p['start_date']} to {p['end_date']} - awaiting Mess Office approval.", "info", "mess")
    notify_admins(cur, "mess", "mess_admin", f"Mess-out #{p['request_id']} needs approval", f"Student {p['user_id']}, {p['days']} days.", "info", "admin/mess")


@subscribe(mess.MESS_OUT_APPROVED)
def credit_rebate_ledger(cur, p):
    """Approved mess-out -> rebate ledger transaction + 'rebated' meal rows. Safe to run twice (unique keys)."""
    cur.execute("SELECT start_date, end_date, days_count, est_rebate_pkr, gate_pass_synced FROM mess_out_requests WHERE request_id=%s", (p["request_id"],))
    r = cur.fetchone()
    cur.execute("""INSERT INTO mess_rebates (rebate_id, user_id, start_date, end_date, days_count, rebate_amount_pkr, status, gate_pass_synced, mess_out_request_id)
                   VALUES ('REB-' || nextval('mess_rebate_seq'), %s,%s,%s,%s,%s,'approved',%s,%s) ON CONFLICT (mess_out_request_id) DO NOTHING""",
                (p["user_id"], r["start_date"], r["end_date"], r["days_count"], r["est_rebate_pkr"], r["gate_pass_synced"], p["request_id"]))
    credited = cur.rowcount
    d = r["start_date"]
    while d <= r["end_date"]:
        for meal in mess.MEALS:
            cur.execute("""INSERT INTO mess_ledger (user_id, meal_date, meal_type, status, source) VALUES (%s,%s,%s,'rebated','mess_out')
                           ON CONFLICT (user_id, meal_date, meal_type, status) DO NOTHING""", (p["user_id"], d, meal))
        d += datetime.timedelta(days=1)
    if credited:
        notify(cur, p["user_id"], "mess", f"Mess-out #{p['request_id']} approved",
               f"PKR {float(r['est_rebate_pkr']):.0f} rebate credited for {r['days_count']} days.", "success", "mess")


@subscribe(mess.MESS_OUT_REJECTED)
def mess_out_rejected(cur, p):
    notify(cur, p["user_id"], "mess", f"Mess-out #{p['request_id']} rejected", p.get("note") or "No reason given.", "warning", "mess")


@subscribe(mess.MESS_OUT_CANCELLED)
def reverse_rebate(cur, p):
    cur.execute("UPDATE mess_rebates SET status='reversed' WHERE mess_out_request_id=%s AND status='approved' RETURNING start_date, end_date", (p["request_id"],))
    row = cur.fetchone()
    if row:
        cur.execute("DELETE FROM mess_ledger WHERE user_id=%s AND status='rebated' AND source='mess_out' AND meal_date BETWEEN %s AND %s", (p["user_id"], row["start_date"], row["end_date"]))
    notify(cur, p["user_id"], "mess", f"Mess-out #{p['request_id']} cancelled", "Any credited rebate has been reversed." if row else "", "info", "mess")


@subscribe(mess.GATE_PASS_SYNCED)
def approve_pending_covered_by_gate_pass(cur, p):
    """Gate-pass synchronisation: pending mess-outs fully covered by a newly synced approved pass are auto-approved."""
    if not p["approved"]:
        return
    cur.execute("""SELECT request_id, start_date, end_date, days_count, est_rebate_pkr FROM mess_out_requests WHERE user_id=%s AND status='pending'
                   AND start_date >= %s AND end_date <= %s FOR UPDATE""", (p["user_id"], p["leave_start"], p["leave_end"]))
    for r in cur.fetchall():
        cur.execute("UPDATE mess_out_requests SET status='approved', gate_pass_synced=TRUE, decided_at=now(), decision_note='Auto-approved: gate-pass synchronised' WHERE request_id=%s", (r["request_id"],))
        payload = {"request_id": r["request_id"], "user_id": p["user_id"]}
        credit_rebate_ledger(cur, payload)


# ── Requisitions ────────────────────────────────────────────
@subscribe(requisitions.REQUISITION_SUBMITTED)
def requisition_submitted(cur, p):
    notify(cur, p["applicant_id"], "requisition", f"{p['requisition_id']} submitted", f"'{p['event_title']}' at {p['venue']} is with the Faculty Advisor.", "info", "requisitions")
    if p.get("advisor_id"):
        notify(cur, p["advisor_id"], "requisition", f"Approval needed: {p['requisition_id']}", f"'{p['event_title']}' awaits your Tier-1 review.", "info", "requisitions")


@subscribe(requisitions.REQUISITION_ADVANCED)
def requisition_advanced(cur, p):
    notify(cur, p["applicant_id"], "requisition", f"{p['requisition_id']} cleared Tier {p['tier']}", "Now awaiting Directorate of Student Affairs.", "info", "requisitions")
    notify_admins(cur, "requisition", "requisition_admin", f"Approval needed: {p['requisition_id']}", f"'{p['event_title']}' awaits Tier-2 clearance.", "info", "admin/requisitions")


@subscribe(requisitions.REQUISITION_APPROVED)
def requisition_approved(cur, p):
    notify(cur, p["applicant_id"], "requisition", f"{p['requisition_id']} approved", f"'{p['event_title']}' is confirmed at {p['venue']}; the venue slot is reserved.", "success", "requisitions")


@subscribe(requisitions.REQUISITION_REJECTED)
def requisition_rejected(cur, p):
    notify(cur, p["applicant_id"], "requisition", f"{p['requisition_id']} rejected (Tier {p['tier']})", p.get("comments") or "", "warning", "requisitions")


@subscribe(requisitions.REQUISITION_CANCELLED)
def requisition_cancelled(cur, p):
    notify(cur, p["applicant_id"], "requisition", f"{p['requisition_id']} cancelled", "Any venue hold was released.", "info", "requisitions")


@subscribe(requisitions.VENUE_BOOKED)
def venue_booked(cur, p):
    notify(cur, p["user_id"], "booking", f"Booking confirmed: {p['venue']}", f"{p['start'][:16].replace('T', ' ')} ({p['hours']}h).", "success", "sports")


@subscribe(requisitions.VENUE_RELEASED)
def venue_released(cur, p):
    if p["reason"] == "no_show":
        msg = f"Your {p['venue']} slot was auto-released (no check-in). Strike {p['strikes']}."
        notify(cur, p["user_id"], "booking", "Slot auto-released", msg + (" Booking privileges suspended." if p.get("suspended") else ""), "warning", "sports")
        if p.get("suspended"):
            notify_admins(cur, "requisition", "suspension", f"{p['user_id']} suspended", "Three no-show strikes reached.", "warning", "admin/sports")
    else:
        notify(cur, p["user_id"], "booking", f"Booking cancelled: {p['venue']}", f"{p['start'][:16].replace('T', ' ')} slot is available again.", "info", "sports")


@subscribe(equipment.EQUIPMENT_CHECKED_OUT)
def equipment_out(cur, p):
    notify(cur, p["user_id"], "equipment", f"Equipment issued: {p['name']}", f"Barcode {p['barcode']}, due {p['due_date']}.", "success", "equipment")


@subscribe(equipment.EQUIPMENT_RETURNED)
def equipment_in(cur, p):
    if p.get("user_id"):
        notify(cur, p["user_id"], "equipment", f"Equipment returned: {p['name']}", "Custody record closed." + (f" Returned {p['late_days']} day(s) late." if p["late_days"] else ""), "success", "equipment")
    if p["condition"] == "damaged":
        notify_admins(cur, "requisition", "equipment_damage", f"{p['name']} returned damaged", f"Barcode {p['barcode']} moved to repair.", "warning", "admin/equipment")


def register_all() -> None:
    """Importing this module registers every handler; kept for explicit startup wiring."""
    return None
