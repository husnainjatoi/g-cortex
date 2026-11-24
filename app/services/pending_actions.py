"""Server-side confirmation gate for consequential assistant actions.

The LLM can only *propose* an action (a tool call that passes a dry-run of the real business rules). The action is stored
here and only executes when the authenticated USER confirms it through the chat endpoint / confirmation button. The model
cannot confirm on the user's behalf, and execution goes through the exact same service functions as the manual portal.
"""
import datetime
import json

from app import db
from . import audit, billing, clock, courses, equipment, mess, requisitions, sports
from .actor import Actor
from .errors import Forbidden, NotFound, RuleViolation, ServiceError

TTL_MINUTES = 10

# tool name -> (service callable, verifier(actor, args, result) -> str|None  [None = verified, str = discrepancy])
def _v_register(a, args, res):
    codes = [c["course_code"] for c in courses.my_registrations(a)["courses"]]
    return None if args["course_code"].upper() in codes else "registration not found after execution"


def _v_drop(a, args, res):
    codes = [c["course_code"] for c in courses.my_registrations(a)["courses"]]
    return None if args["course_code"].upper() not in codes else "course still registered after execution"


def _v_mess_out(a, args, res):
    return None if any(r["request_id"] == res["request_id"] for r in mess.list_mess_out(a)) else "request not found after execution"


def _v_booking(a, args, res):
    return None if any(b["booking_code"] == res["booking_code"] and b["status"] in ("approved", "pending") for b in sports.my_bookings(a)) else "booking not found after execution"


def _v_cancel_booking(a, args, res):
    return None if any(b["booking_code"] == res["booking_code"] and b["status"] == "cancelled" for b in sports.my_bookings(a)) else "booking not cancelled"


def _v_requisition(a, args, res):
    return None if requisitions.get_requisition(a, res["requisition_id"]) else "requisition missing"


def _v_checkout(a, args, res):
    return None if any(e["barcode"] == res["barcode"] for e in equipment.my_custody(a)) else "custody record missing"


def _v_return(a, args, res):
    return None if not any(e["barcode"] == res["barcode"] for e in equipment.my_custody(a)) else "item still in custody"


def _v_cancel_mess(a, args, res):
    return None if any(r["request_id"] == res["request_id"] and r["status"] == "cancelled" for r in mess.list_mess_out(a)) else "request not cancelled"


def _v_cancel_req(a, args, res):
    return None if requisitions.get_requisition(a, res["requisition_id"])["status"] == "cancelled" else "requisition not cancelled"


def _v_payment(a, args, res):
    return None if any(p["payment_id"] == res["payment_id"] for p in billing.list_payments(a, None)) else "receipt not found after execution"


def _v_clearance(a, args, res):
    c = billing.deposit_summary(a)["clearance"]
    return None if c and c["clearance_id"] == res["clearance_id"] else "clearance not found after execution"


EXECUTORS = {
    "submit_mess_payment_receipt": (lambda a, **k: billing.submit_payment(a, k["invoice_no"], k["amount_pkr"], k["reference"]), _v_payment),
    "request_mess_clearance": (lambda a, **k: billing.request_clearance(a), _v_clearance),
    "register_for_course": (lambda a, **k: courses.register(a, k["course_code"]), _v_register),
    "drop_course": (lambda a, **k: courses.drop(a, k["course_code"]), _v_drop),
    "submit_mess_out_request": (lambda a, **k: mess.submit_mess_out(a, k["start_date"], k["end_date"], k.get("reason")), _v_mess_out),
    "cancel_mess_out_request": (lambda a, **k: mess.cancel_mess_out(a, int(k["request_id"])), _v_cancel_mess),
    "book_sports_facility": (lambda a, **k: sports.book(a, k["facility_id"], k["date"], k["start_time"], k.get("duration_hours", 1.0)), _v_booking),
    "cancel_sports_booking": (lambda a, **k: sports.cancel(a, k["booking_ref"], "cancelled via assistant"), _v_cancel_booking),
    "submit_society_requisition": (lambda a, **k: requisitions.submit_requisition(a, **k), _v_requisition),
    "cancel_society_requisition": (lambda a, **k: requisitions.cancel_requisition(a, k["requisition_id"]), _v_cancel_req),
    "checkout_lab_equipment": (lambda a, **k: equipment.checkout(a, k["barcode"], k["purpose"], k.get("days")), _v_checkout),
    "return_lab_equipment": (lambda a, **k: equipment.return_equipment(a, k["barcode"], k.get("condition", "good"), k.get("note", "")), _v_return),
}


def propose(actor: Actor, session_id: str, tool: str, args: dict, summary: str) -> dict:
    if tool not in EXECUTORS:
        raise NotFound("Unknown action.")
    with db.tx() as cur:
        # a newer proposal in the same chat supersedes older unconfirmed ones
        cur.execute("UPDATE assistant_pending_actions SET status='cancelled' WHERE user_id=%s AND session_id=%s AND status='pending'", (actor.user_id, session_id))
        cur.execute("""INSERT INTO assistant_pending_actions (user_id, session_id, tool, args, summary, expires_at)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING action_id""",
                    (actor.user_id, session_id, tool, json.dumps(args), summary, clock.now() + datetime.timedelta(minutes=TTL_MINUTES)))
        action_id = str(cur.fetchone()["action_id"])
        audit.log(cur, actor.as_channel("assistant"), "assistant.propose", "assistant_action", action_id, detail={"tool": tool, "args": args})
    return {"action_id": action_id, "tool": tool, "summary": summary, "expires_in_minutes": TTL_MINUTES}


def latest_pending(actor: Actor, session_id: str) -> dict | None:
    return db.query_one("""SELECT action_id::text, tool, args, summary, created_at FROM assistant_pending_actions
                           WHERE user_id=%s AND session_id=%s AND status='pending' AND expires_at > %s ORDER BY created_at DESC LIMIT 1""",
                        (actor.user_id, session_id, clock.now()))


def pending_for_session(actor: Actor, session_id: str) -> list[dict]:
    return db.query("""SELECT action_id::text, tool, args, summary, status FROM assistant_pending_actions
                       WHERE user_id=%s AND session_id=%s AND status='pending' AND expires_at > %s""", (actor.user_id, session_id, clock.now()))


def _claim(actor: Actor, action_id: str) -> dict:
    with db.tx() as cur:
        cur.execute("SELECT * FROM assistant_pending_actions WHERE action_id=%s FOR UPDATE", (action_id,))
        row = cur.fetchone()
        if not row or row["user_id"] != actor.user_id:
            raise NotFound("No such pending action.")           # same answer for other users' ids: no information leak
        if row["status"] != "pending":
            raise RuleViolation(f"This action was already {row['status']}.")
        if row["expires_at"] < clock.now():
            cur.execute("UPDATE assistant_pending_actions SET status='expired' WHERE action_id=%s", (action_id,))
            cur.connection.commit()
            raise RuleViolation("This confirmation expired. Please ask again.")
        # claim atomically so a double click can never run it twice
        cur.execute("UPDATE assistant_pending_actions SET status='confirmed' WHERE action_id=%s", (action_id,))
        return dict(row)


def confirm(actor: Actor, action_id: str) -> dict:
    row = _claim(actor, action_id)
    assistant_actor = actor.as_channel("assistant")
    run, verify = EXECUTORS[row["tool"]]
    args = row["args"] if isinstance(row["args"], dict) else json.loads(row["args"])
    try:
        result = run(assistant_actor, **args)
        problem = verify(assistant_actor, args, result)
        status = "confirmed" if problem is None else "failed"
        out = {"ok": problem is None, "executed": True, "verified": problem is None, "result": result,
               "message": result.get("message") if problem is None else f"The operation ran but verification failed: {problem}."}
    except ServiceError as exc:
        status, out = "failed", {"ok": False, "executed": False, "verified": False, "code": exc.code, "message": exc.message}
    except Exception as exc:  # unexpected - never report success
        status, out = "failed", {"ok": False, "executed": False, "verified": False, "code": "internal_error", "message": "The operation could not be completed due to a system error."}
        audit.log_independent(assistant_actor, "assistant.execute", "assistant_action", action_id, "failed", {"error": str(exc)[:300]})
    with db.tx() as cur:
        cur.execute("UPDATE assistant_pending_actions SET status=%s, result=%s WHERE action_id=%s", (status, json.dumps(out, default=str), action_id))
    return out


def cancel(actor: Actor, action_id: str) -> dict:
    with db.tx() as cur:
        cur.execute("UPDATE assistant_pending_actions SET status='cancelled' WHERE action_id=%s AND user_id=%s AND status='pending'", (action_id, actor.user_id))
        if cur.rowcount == 0:
            raise NotFound("No pending action to cancel.")
    return {"ok": True, "message": "Cancelled. Nothing was changed."}
