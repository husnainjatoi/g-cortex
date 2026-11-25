"""Cross-module event handlers (the event-driven pipeline). Every handler is idempotent and runs in its own transaction."""
from . import billing, courses, equipment, events, mess, requisitions
from .notifications import notify, notify_admins
from .events import subscribe


# ── Course ──────────────────────────────────────────────────
@subscribe(courses.COURSE_ENROLLED)
def notify_course_enrolled(cur, p):
    notify(cur, p["user_id"], "course", f"Registered for {p['course_code']}", f"{p['title']} - {p['term']}", "success", "courses")


@subscribe(courses.COURSE_DROPPED)
def notify_course_dropped(cur, p):
    notify(cur, p["user_id"], "course", f"Dropped {p['course_code']}", f"Your {p['term']} registration was removed.", "info", "courses")


# ── Mess: declarations (informational) and monthly billing ──
@subscribe(mess.MESS_OUT_SUBMITTED)
def mess_out_recorded(cur, p):
    notify(cur, p["user_id"], "mess", f"Mess-out #{p['request_id']} recorded", f"{p['start_date']} to {p['end_date']}. You are billed only for meals you eat.", "info", "mess")


@subscribe(mess.MESS_OUT_CANCELLED)
def mess_out_cancelled(cur, p):
    notify(cur, p["user_id"], "mess", f"Mess-out #{p['request_id']} cancelled", "", "info", "mess")


@subscribe(billing.BILL_INVOICE_GENERATED)
def invoice_generated(cur, p):
    notify(cur, p["user_id"], "billing", f"Mess invoice {p['invoice_no']} for {p['month']}", f"PKR {p['amount_pkr']:.2f} due by {p['due_date']}. Generate a challan from the Mess page.", "info", "mess")


@subscribe(billing.BILL_PAYMENT_SUBMITTED)
def payment_submitted(cur, p):
    notify(cur, p["user_id"], "billing", f"Receipt submitted for {p['invoice_no']}", f"PKR {p['amount_pkr']:.2f} awaiting Mess Office verification.", "info", "mess")
    notify_admins(cur, "mess", "billing_admin", f"Payment to verify: {p['invoice_no']}", f"Student {p['user_id']} submitted PKR {p['amount_pkr']:.2f}.", "info", "admin/mess")


@subscribe(billing.BILL_PAYMENT_VERIFIED)
def payment_verified(cur, p):
    notify(cur, p["user_id"], "billing", f"Payment verified: {p['invoice_no']}", f"PKR {p['amount_pkr']:.2f} accepted. Invoice is now {p['invoice_status']}.", "success", "mess")


@subscribe(billing.BILL_PAYMENT_REJECTED)
def payment_rejected(cur, p):
    notify(cur, p["user_id"], "billing", f"Payment rejected: {p['invoice_no']}", p.get("note") or "The receipt could not be verified.", "warning", "mess")


@subscribe(billing.BILL_INVOICE_ADJUSTED)
def invoice_adjusted(cur, p):
    notify(cur, p["user_id"], "billing", f"Invoice {p['invoice_no']} adjusted", f"PKR {p['amount_pkr']:+.2f}: {p['reason']}", "info", "mess")


@subscribe(billing.DEPOSIT_RECORDED)
def deposit_recorded(cur, p):
    notify(cur, p["user_id"], "billing", f"Security deposit payment {p['status']}", f"PKR {p['amount_pkr']:.2f}", "success" if p["status"] == "verified" else "info", "mess")


@subscribe(billing.CLEARANCE_UPDATED)
def clearance_updated(cur, p):
    msg = {"requested": "Your final mess clearance request was received.", "approved": "Your clearance was approved; settlement is next.",
           "rejected": "Your clearance request was rejected. " + (p.get("note") or ""), "settled": f"Settlement recorded: refundable PKR {p.get('refundable_pkr', 0):.2f}, additional dues PKR {p.get('additional_due_pkr', 0):.2f}.",
           "returned": "Your refundable security deposit has been returned."}[p["status"]]
    notify(cur, p["user_id"], "billing", f"Mess clearance {p['status']}", msg, "info", "mess")
    if p["status"] == "requested":
        notify_admins(cur, "mess", "clearance_admin", "Mess clearance to review", f"Student {p['user_id']}", "info", "admin/mess")


# ── Requisitions ────────────────────────────────────────────
@subscribe(requisitions.REQUISITION_SUBMITTED)
def requisition_submitted(cur, p):
    notify(cur, p["applicant_id"], "requisition", f"{p['requisition_id']} submitted", f"'{p['event_title']}' at {p['venue']} is with the Faculty Advisor.", "info", "requisitions")
    if p.get("advisor_id"):
        notify(cur, p["advisor_id"], "requisition", f"Approval needed: {p['requisition_id']}", f"'{p['event_title']}' awaits your Tier-1 review.", "info", "approvals")
    else:
        notify_admins(cur, "requisition", "requisition_admin", f"Approval needed: {p['requisition_id']}", f"{p.get('reservation_type', 'society').capitalize()} reservation '{p['event_title']}' awaits clearance.", "info", "admin/requisitions")


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
