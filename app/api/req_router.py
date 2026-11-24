"""
Requisition Management API Router
REST endpoints for society venue requisitions with multi-tier approval chains,
fair-use sports facility reservations, natural language venue recommendations,
lab equipment barcode custody tracking, and zero-trust no-show auto-release.
"""
import json
import time
import hashlib
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel, Field

from app.database import SessionLocal

router = APIRouter()


def get_db():
    """Provide a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schemas ────────────────────────────────────────────────────

class RequisitionRequest(BaseModel):
    student_reg_no: str
    society_name: str
    venue_id: str
    event_title: str
    booking_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM")
    duration_hours: float = Field(default=2.0, ge=0.5)
    expected_attendees: int = Field(..., ge=1)
    justification: str


class RequisitionReviewRequest(BaseModel):
    reviewer_id: str
    reviewer_role: str = Field(..., description="faculty | admin")
    action: str = Field(..., description="approve | reject")
    comments: str = "Proceed as requested"


class SportsFacilityRequest(BaseModel):
    student_reg_no: str
    facility_id: str
    booking_date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM")
    duration_hours: float = Field(default=1.0, ge=0.5, le=2.0)


class EquipmentCheckoutRequest(BaseModel):
    student_reg_no: str
    purpose: str
    duration_days: int = Field(default=3, ge=1, le=7)


class EquipmentReturnRequest(BaseModel):
    student_reg_no: str
    reported_condition: str = Field(default="good", description="good | fair | damaged")


class CheckinRequest(BaseModel):
    student_reg_no: str
    checkin_token: str


class VenueRecommendRequest(BaseModel):
    event_description: str
    expected_attendees: int = Field(..., ge=1)
    required_facilities: str = Field(default="none")


# ── Endpoints ──────────────────────────────────────────────────

@router.post("/society-requisitions")
def submit_requisition(body: RequisitionRequest, db: Session = Depends(get_db)):
    """Submit a multi-tier society venue requisition."""
    venue_up = body.venue_id.upper().strip()
    v_row = db.execute(
        text("SELECT name, capacity FROM venues WHERE venue_id = :vid"),
        {"vid": venue_up},
    ).fetchone()

    if not v_row:
        raise HTTPException(status_code=404, detail=f"Venue '{body.venue_id}' not found.")

    venue_name, capacity = v_row[0], v_row[1]
    if body.expected_attendees > capacity:
        raise HTTPException(
            status_code=409,
            detail=f"Capacity violation: {venue_name} holds at most {capacity} but {body.expected_attendees} requested.",
        )

    soc_row = db.execute(
        text("SELECT advisor_id, advisor_name FROM campus_societies WHERE society_name = :sn"),
        {"sn": body.society_name},
    ).fetchone()
    advisor_id = soc_row[0] if soc_row else "FAC001"
    advisor_name = soc_row[1] if soc_row else "Faculty Advisor"

    conflict = db.execute(
        text(
            "SELECT booking_id FROM bookings"
            " WHERE venue_id = :vid AND booking_date = :bd AND start_time = :st"
            " AND status IN ('approved', 'pending')"
        ),
        {"vid": venue_up, "bd": body.booking_date, "st": body.start_time},
    ).fetchone()

    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Conflict: {venue_name} is already reserved at {body.start_time} on {body.booking_date}.",
        )

    count_row = db.execute(text("SELECT COUNT(*) FROM society_requisitions")).fetchone()
    req_id = f"REQ-2026-{(count_row[0] if count_row else 0) + 1:03d}"

    approval_chain = [
        {"tier": 1, "role": "faculty_advisor", "assigned_to": advisor_id, "advisor_name": advisor_name, "action": "pending", "timestamp": None, "comments": None},
        {"tier": 2, "role": "student_affairs_admin", "assigned_to": "ADMIN01", "action": "pending", "timestamp": None, "comments": None},
    ]

    db.execute(
        text(
            "INSERT INTO society_requisitions"
            " (requisition_id, society_name, venue_id, applicant_id, event_title, booking_date, start_time,"
            "  duration_hours, expected_attendees, justification, current_tier, status, approval_chain)"
            " VALUES (:rid, :sn, :vid, :uid, :et, :bd, :st, :dh, :ea, :just, 1, 'pending_advisor_approval', :chain)"
        ),
        {
            "rid": req_id, "sn": body.society_name, "vid": venue_up, "uid": body.student_reg_no,
            "et": body.event_title, "bd": body.booking_date, "st": body.start_time,
            "dh": body.duration_hours, "ea": body.expected_attendees, "just": body.justification,
            "chain": json.dumps(approval_chain),
        },
    )
    db.commit()

    return {
        "status": "submitted",
        "requisition_id": req_id,
        "venue": venue_name,
        "approval_state": "pending_advisor_approval",
        "current_tier": 1,
        "next_approver": f"{advisor_name} ({advisor_id})",
        "routing_pipeline": "Tier 1: Faculty Advisor → Tier 2: Directorate of Student Affairs",
        "message": f"Requisition {req_id} for '{body.event_title}' logged and digitally routed to {advisor_name}.",
    }


@router.get("/society-requisitions/{requisition_id}")
def get_requisition_status(requisition_id: str, db: Session = Depends(get_db)):
    """Get full status and approval trail for a society requisition."""
    req_up = requisition_id.upper().strip()
    row = db.execute(
        text(
            "SELECT sr.requisition_id, sr.society_name, sr.event_title, v.name,"
            "       sr.booking_date, sr.start_time, sr.duration_hours, sr.expected_attendees,"
            "       sr.status, sr.current_tier, sr.approval_chain"
            " FROM society_requisitions sr"
            " JOIN venues v ON v.venue_id = sr.venue_id"
            " WHERE sr.requisition_id = :rid"
        ),
        {"rid": req_up},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Requisition '{requisition_id}' not found.")

    return {
        "status": "success",
        "requisition_id": row[0],
        "society_name": row[1],
        "event_title": row[2],
        "venue": row[3],
        "schedule": f"{row[4]} from {row[5]} ({row[6]} hours)",
        "expected_attendees": row[7],
        "overall_status": row[8],
        "current_tier": row[9],
        "approval_chain": json.loads(row[10]) if row[10] else [],
    }


@router.post("/society-requisitions/{requisition_id}/review")
def review_requisition(requisition_id: str, body: RequisitionReviewRequest, db: Session = Depends(get_db)):
    """Process a Tier-1 or Tier-2 approval/rejection decision."""
    req_up = requisition_id.upper().strip()
    if body.action.lower() not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'.")

    row = db.execute(
        text(
            "SELECT requisition_id, society_name, venue_id, applicant_id, event_title,"
            "       booking_date, start_time, duration_hours, status, current_tier, approval_chain"
            " FROM society_requisitions WHERE requisition_id = :rid"
        ),
        {"rid": req_up},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Requisition '{requisition_id}' not found.")

    req_id, soc_name, venue_id, applicant_id, title, b_date, s_time, dur, current_status, tier, chain_raw = row
    approval_chain = json.loads(chain_raw) if chain_raw else []
    now_iso = datetime.datetime.now().isoformat()
    action_clean = body.action.lower()

    if current_status in ("approved", "rejected", "auto-released"):
        raise HTTPException(status_code=409, detail=f"Requisition is already in terminal state: {current_status}.")

    if current_status == "pending_advisor_approval":
        if body.reviewer_role.lower() not in ("faculty", "admin"):
            raise HTTPException(status_code=403, detail="Tier 1 requires Faculty Advisor clearance.")

        approval_chain[0]["action"] = action_clean
        approval_chain[0]["reviewer_id"] = body.reviewer_id
        approval_chain[0]["timestamp"] = now_iso
        approval_chain[0]["comments"] = body.comments

        if action_clean == "reject":
            db.execute(
                text("UPDATE society_requisitions SET status = 'rejected', approval_chain = :chain WHERE requisition_id = :rid"),
                {"chain": json.dumps(approval_chain), "rid": req_id},
            )
            db.commit()
            return {"status": "rejected", "requisition_id": req_id, "tier_processed": 1,
                    "message": f"Rejected at Tier 1 by {body.reviewer_id}. Reason: {body.comments}"}

        db.execute(
            text("UPDATE society_requisitions SET status = 'pending_admin_approval', current_tier = 2, approval_chain = :chain WHERE requisition_id = :rid"),
            {"chain": json.dumps(approval_chain), "rid": req_id},
        )
        db.commit()
        return {
            "status": "advanced", "requisition_id": req_id, "tier_completed": 1, "next_tier": 2,
            "approval_state": "pending_admin_approval",
            "next_approver": "Directorate of Student Affairs (ADMIN01)",
            "message": f"Tier 1 endorsed by {body.reviewer_id}. Forwarded to Student Affairs.",
        }

    if current_status == "pending_admin_approval":
        if body.reviewer_role.lower() != "admin":
            raise HTTPException(status_code=403, detail="Tier 2 requires Admin credentials.")

        approval_chain[1]["action"] = action_clean
        approval_chain[1]["reviewer_id"] = body.reviewer_id
        approval_chain[1]["timestamp"] = now_iso
        approval_chain[1]["comments"] = body.comments

        if action_clean == "reject":
            db.execute(
                text("UPDATE society_requisitions SET status = 'rejected', approval_chain = :chain WHERE requisition_id = :rid"),
                {"chain": json.dumps(approval_chain), "rid": req_id},
            )
            db.commit()
            return {"status": "rejected", "requisition_id": req_id, "tier_processed": 2,
                    "message": f"Rejected at Tier 2 by {body.reviewer_id}. Reason: {body.comments}"}

        b_count_row = db.execute(text("SELECT COUNT(*) FROM bookings")).fetchone()
        booking_code = f"BK-EVT-{(b_count_row[0] if b_count_row else 0) + 101}"
        token_seed = f"{booking_code}:{applicant_id}:{time.time()}"
        checkin_pin = f"CHK-{hashlib.sha256(token_seed.encode()).hexdigest()[:4].upper()}"

        db.execute(
            text("UPDATE society_requisitions SET status = 'approved', approval_chain = :chain WHERE requisition_id = :rid"),
            {"chain": json.dumps(approval_chain), "rid": req_id},
        )
        db.execute(
            text(
                "INSERT INTO bookings (booking_code, venue_id, user_id, booking_time, booking_date, start_time,"
                " duration_hours, status, checkin_token, checkin_status, source)"
                " VALUES (:bc, :vid, :uid, CURRENT_TIMESTAMP, :bd, :st, :dh, 'approved', :pin, 'pending_checkin', :src)"
            ),
            {"bc": booking_code, "vid": venue_id, "uid": applicant_id, "bd": b_date,
             "st": s_time, "dh": dur, "pin": checkin_pin, "src": f"requisition:{req_id}"},
        )
        db.commit()

        return {
            "status": "approved", "requisition_id": req_id, "booking_id": booking_code,
            "checkin_token": checkin_pin,
            "message": f"Requisition {req_id} fully approved! Confirmed under Booking {booking_code}.",
            "zero_trust_guidance": f"Check-in required within 15 minutes of {s_time} using token {checkin_pin}.",
        }

    raise HTTPException(status_code=500, detail=f"Unhandled requisition state: {current_status}")


@router.post("/sports/reserve")
def reserve_sports(body: SportsFacilityRequest, db: Session = Depends(get_db)):
    """Reserve a sports facility with fair-use 2-hour daily cap enforcement."""
    fid_up = body.facility_id.upper().strip()
    v_row = db.execute(
        text("SELECT name, category FROM venues WHERE venue_id = :vid"),
        {"vid": fid_up},
    ).fetchone()

    if not v_row or v_row[1] != "sports":
        raise HTTPException(status_code=404, detail=f"Invalid sports facility '{body.facility_id}'.")

    ns_row = db.execute(
        text("SELECT strikes, suspended FROM no_show_records WHERE user_id = :uid"),
        {"uid": body.student_reg_no},
    ).fetchone()

    if ns_row and (ns_row[1] or ns_row[0] >= 3):
        raise HTTPException(status_code=403, detail="Account suspended due to 3 no-show penalties.")

    existing_hours_row = db.execute(
        text(
            "SELECT COALESCE(SUM(duration_hours), 0) FROM bookings"
            " WHERE user_id = :uid AND booking_date = :bd AND status IN ('approved', 'checked_in', 'pending')"
            " AND source = 'sports_reservation'"
        ),
        {"uid": body.student_reg_no, "bd": body.booking_date},
    ).fetchone()
    existing_hours = float(existing_hours_row[0]) if existing_hours_row else 0.0

    MAX_DAILY = 2.0
    if existing_hours + body.duration_hours > MAX_DAILY:
        raise HTTPException(
            status_code=409,
            detail=f"Fair-use quota exceeded: {existing_hours:.1f}h booked today. Max is {MAX_DAILY:.1f}h/day.",
        )

    conflict = db.execute(
        text(
            "SELECT booking_id FROM bookings"
            " WHERE venue_id = :vid AND booking_date = :bd AND start_time = :st"
            " AND status IN ('approved', 'checked_in')"
        ),
        {"vid": fid_up, "bd": body.booking_date, "st": body.start_time},
    ).fetchone()

    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Slot conflict: {v_row[0]} is already reserved at {body.start_time} on {body.booking_date}.",
        )

    b_count = db.execute(text("SELECT COUNT(*) FROM bookings")).fetchone()[0]
    booking_code = f"BK-SPT-{b_count + 101}"
    token_seed = f"{booking_code}:{body.student_reg_no}:{body.booking_date}:{body.start_time}"
    checkin_pin = f"CHK-{hashlib.sha256(token_seed.encode()).hexdigest()[:4].upper()}"

    db.execute(
        text(
            "INSERT INTO bookings (booking_code, venue_id, user_id, booking_time, booking_date, start_time,"
            " duration_hours, status, checkin_token, checkin_status, source)"
            " VALUES (:bc, :vid, :uid, CURRENT_TIMESTAMP, :bd, :st, :dh, 'approved', :pin, 'pending_checkin', 'sports_reservation')"
        ),
        {"bc": booking_code, "vid": fid_up, "uid": body.student_reg_no,
         "bd": body.booking_date, "st": body.start_time, "dh": body.duration_hours, "pin": checkin_pin},
    )
    db.commit()

    return {
        "status": "confirmed",
        "booking_id": booking_code,
        "facility": v_row[0],
        "date": body.booking_date,
        "time": f"{body.start_time} ({body.duration_hours}h)",
        "checkin_token": checkin_pin,
        "daily_quota_consumed_hours": existing_hours + body.duration_hours,
        "daily_quota_remaining_hours": max(0.0, MAX_DAILY - (existing_hours + body.duration_hours)),
        "zero_trust_warning": (
            f"Check-in required within 15 minutes of {body.start_time} using PIN '{checkin_pin}'. "
            "Unclaimed slots are auto-released and accrue a penalty strike."
        ),
    }


@router.get("/sports/availability")
def check_sports_availability(facility_id: str = "all", booking_date: str = None, db: Session = Depends(get_db)):
    """Check booked slots and available windows for sports facilities."""
    if booking_date is None:
        booking_date = datetime.date.today().isoformat()

    if facility_id.upper() == "ALL":
        facilities = db.execute(
            text("SELECT venue_id, name, capacity FROM venues WHERE category = 'sports' ORDER BY venue_id")
        ).fetchall()
    else:
        facilities = db.execute(
            text("SELECT venue_id, name, capacity FROM venues WHERE venue_id = :vid"),
            {"vid": facility_id.upper().strip()},
        ).fetchall()

    operating_slots = ["08:00", "09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
    reports = []

    for fid, fname, cap in facilities:
        booked = [
            r[0] for r in db.execute(
                text("SELECT start_time FROM bookings WHERE venue_id = :vid AND booking_date = :bd AND status IN ('approved', 'checked_in')"),
                {"vid": fid, "bd": booking_date},
            ).fetchall()
        ]
        available = [s for s in operating_slots if s not in booked]
        reports.append({
            "facility_id": fid,
            "name": fname,
            "capacity": cap,
            "booked_slots": booked,
            "available_slots": available,
        })

    return {"status": "success", "date": booking_date, "facilities_queried": len(reports), "reports": reports}


@router.post("/venues/recommend")
def recommend_venue(body: VenueRecommendRequest, db: Session = Depends(get_db)):
    """Get top-3 venue recommendations via natural language event description."""
    desc_lower = body.event_description.lower()
    req_facs = [f.strip().lower() for f in body.required_facilities.split(",") if f.strip().lower() != "none"]

    rows = db.execute(
        text("SELECT venue_id, name, capacity, requires_approval, category, facilities, description FROM venues")
    ).fetchall()

    scored = []
    for vid, name, cap, req_app, cat, facs_raw, description in rows:
        if cap < body.expected_attendees:
            continue
        facs_list = [f.strip() for f in (facs_raw or "").split(",") if f.strip()]
        score = 45 if body.expected_attendees <= cap <= body.expected_attendees * 1.5 else (30 if cap <= body.expected_attendees * 3 else 15)

        if any(w in desc_lower for w in ("hackathon", "coding", "software", "programming")):
            score += 40 if vid in ("LAB_SE", "LAB_MEDS") else (15 if vid in ("AHA_AUDI", "SEM_HALL_1") else 0)
        if any(w in desc_lower for w in ("hardware", "circuit", "fpga", "oscilloscope")):
            score += 50 if vid == "LAB_MEDS" else 0
        if any(w in desc_lower for w in ("seminar", "lecture", "symposium", "conference")):
            score += 40 if vid in ("AHA_AUDI", "SEM_HALL_1") else 0
        if any(w in desc_lower for w in ("sports", "badminton", "squash", "tennis", "futsal")):
            score += 50 if cat == "sports" else 0

        matched = []
        for rf in req_facs:
            for vf in facs_list:
                if rf in vf.lower():
                    score += 10
                    matched.append(vf)

        scored.append({
            "venue_id": vid,
            "name": name,
            "capacity": cap,
            "approval_required": req_app,
            "facilities_available": facs_list,
            "facilities_matched": list(set(matched)),
            "match_score": min(score, 100),
            "recommendation_reason": description,
        })

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    top = scored[:3]

    return {
        "status": "success",
        "query_attendees": body.expected_attendees,
        "recommendations_count": len(top),
        "top_recommendations": top,
        "guidance": f"Top match: {top[0]['name']} ({top[0]['match_score']}% match)." if top else "No suitable venue found.",
    }


@router.post("/equipment/{barcode}/checkout")
def checkout_equipment(barcode: str, body: EquipmentCheckoutRequest, db: Session = Depends(get_db)):
    """Checkout lab equipment via barcode asset tag."""
    b_code = barcode.upper().strip()
    row = db.execute(
        text("SELECT name, status, custodian_id, due_date FROM lab_equipment WHERE barcode = :bc"),
        {"bc": b_code},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Barcode '{barcode}' not found.")

    eq_name, status, custodian, due_date = row
    if status == "in_custody":
        raise HTTPException(status_code=409, detail=f"Equipment in custody of {custodian} until {due_date}.")

    if body.duration_days > 7:
        raise HTTPException(status_code=400, detail="Maximum checkout duration is 7 days.")

    now = datetime.date.today()
    due = now + datetime.timedelta(days=body.duration_days)

    db.execute(
        text(
            "UPDATE lab_equipment SET status = 'in_custody', custodian_id = :uid,"
            " checkout_date = :cd, due_date = :dd, purpose = :pur WHERE barcode = :bc"
        ),
        {"uid": body.student_reg_no, "cd": now, "dd": due, "pur": body.purpose, "bc": b_code},
    )
    db.commit()

    return {
        "status": "checked_out",
        "barcode": b_code,
        "equipment_name": eq_name,
        "assigned_to": body.student_reg_no,
        "checkout_date": now.isoformat(),
        "due_date": due.isoformat(),
        "purpose": body.purpose,
        "custody_terms": "Return by due date to avoid late fines. Max 7 days per policy.",
    }


@router.post("/equipment/{barcode}/return")
def return_equipment(barcode: str, body: EquipmentReturnRequest, db: Session = Depends(get_db)):
    """Return lab equipment and clear barcode custody record."""
    b_code = barcode.upper().strip()
    row = db.execute(
        text("SELECT name, status, custodian_id, due_date FROM lab_equipment WHERE barcode = :bc"),
        {"bc": b_code},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Barcode '{barcode}' not found.")

    eq_name, status, custodian, due_date = row
    if status != "in_custody":
        raise HTTPException(status_code=409, detail=f"Equipment '{eq_name}' is not currently in custody ({status}).")
    if custodian != body.student_reg_no:
        raise HTTPException(status_code=403, detail=f"Custodian mismatch: registered to {custodian}.")

    today = datetime.date.today()
    is_overdue = due_date and today > due_date
    overdue_days = (today - due_date).days if is_overdue else 0
    next_status = "available" if body.reported_condition.lower() != "damaged" else "under_repair"

    db.execute(
        text(
            "UPDATE lab_equipment SET status = :st, custodian_id = NULL, checkout_date = NULL,"
            " due_date = NULL, condition = :cond WHERE barcode = :bc"
        ),
        {"st": next_status, "cond": body.reported_condition, "bc": b_code},
    )
    db.commit()

    return {
        "status": "returned",
        "barcode": b_code,
        "equipment_name": eq_name,
        "cleared_student": body.student_reg_no,
        "return_condition": body.reported_condition,
        "was_overdue": is_overdue,
        "overdue_days": overdue_days,
        "clearance_status": "CLEARED: Laboratory custody ledger updated.",
    }


@router.get("/equipment")
def query_equipment(query_type: str = "all", filter_value: str = "none", db: Session = Depends(get_db)):
    """Query lab equipment inventory and custody records."""
    mode = query_type.lower().strip()
    base_q = "SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment"

    if mode == "available":
        rows = db.execute(text(f"{base_q} WHERE status = 'available' ORDER BY barcode")).fetchall()
    elif mode == "in_custody":
        rows = db.execute(text(f"{base_q} WHERE status = 'in_custody' ORDER BY barcode")).fetchall()
    elif mode == "by_student":
        rows = db.execute(text(f"{base_q} WHERE custodian_id = :fv"), {"fv": filter_value.strip()}).fetchall()
    elif mode == "by_barcode":
        rows = db.execute(text(f"{base_q} WHERE barcode = :fv"), {"fv": filter_value.upper().strip()}).fetchall()
    else:
        rows = db.execute(text(f"{base_q} ORDER BY barcode")).fetchall()

    items = [
        {"barcode": r[0], "name": r[1], "lab": r[2], "category": r[3],
         "status": r[4], "custodian": r[5], "due_date": str(r[6]) if r[6] else None, "condition": r[7]}
        for r in rows
    ]
    return {"status": "success", "query_mode": mode, "total_matched": len(items), "items": items}


@router.post("/bookings/{booking_id}/checkin")
def checkin_booking(booking_id: str, body: CheckinRequest, db: Session = Depends(get_db)):
    """Process zero-trust physical presence check-in for a reservation."""
    row = db.execute(
        text(
            "SELECT b.booking_code, b.user_id, b.status, b.checkin_status, b.checkin_token, v.name"
            " FROM bookings b JOIN venues v ON v.venue_id = b.venue_id"
            " WHERE b.booking_code = :bid"
        ),
        {"bid": booking_id.upper().strip()},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Booking '{booking_id}' not found.")

    b_code, user_id, status, checkin_status, token, v_name = row

    if user_id != body.student_reg_no:
        raise HTTPException(status_code=403, detail=f"Student {body.student_reg_no} is not authorized for booking {booking_id}.")

    if status == "auto-released":
        raise HTTPException(status_code=410, detail="Booking auto-released due to no-show past 15-minute grace window.")

    if checkin_status == "checked_in":
        return {"status": "already_checked_in", "booking_id": booking_id, "message": "Check-in already completed."}

    if token and token.upper() != body.checkin_token.upper().strip():
        raise HTTPException(status_code=401, detail="Invalid check-in PIN.")

    db.execute(
        text("UPDATE bookings SET checkin_status = 'checked_in' WHERE booking_code = :bc"),
        {"bc": b_code},
    )
    db.commit()

    return {
        "status": "checked_in",
        "booking_id": booking_id,
        "venue": v_name,
        "student": body.student_reg_no,
        "session_state": "ACTIVE",
        "message": f"Zero-trust presence verified! Access granted to {v_name}.",
    }


@router.post("/bookings/auto-release")
def auto_release_no_shows(current_time_override: str = "none", db: Session = Depends(get_db)):
    """Scan and auto-release overdue bookings past the 15-minute check-in grace window."""
    rows = db.execute(
        text(
            "SELECT b.booking_code, b.venue_id, b.user_id, b.start_time, v.name"
            " FROM bookings b JOIN venues v ON v.venue_id = b.venue_id"
            " WHERE b.status = 'approved' AND b.checkin_status = 'pending_checkin' AND b.booking_date = CURRENT_DATE"
        )
    ).fetchall()

    ref_time = current_time_override if current_time_override.lower() != "none" else datetime.datetime.now().strftime("%H:%M")
    ref_parts = [int(p) for p in ref_time.split(":")]
    ref_minutes = ref_parts[0] * 60 + ref_parts[1]

    released = []
    for b_code, v_id, user_id, start_time_str, v_name in rows:
        b_parts = [int(p) for p in (start_time_str or "00:00").split(":")]
        b_minutes = b_parts[0] * 60 + b_parts[1]

        if ref_minutes > (b_minutes + 15):
            db.execute(
                text("UPDATE bookings SET status = 'auto-released', checkin_status = 'auto-released' WHERE booking_code = :bc"),
                {"bc": b_code},
            )
            db.execute(
                text(
                    "INSERT INTO no_show_records (user_id, strikes, suspended) VALUES (:uid, 1, false)"
                    " ON CONFLICT (user_id) DO UPDATE SET strikes = no_show_records.strikes + 1"
                ),
                {"uid": user_id},
            )
            strikes_row = db.execute(text("SELECT strikes FROM no_show_records WHERE user_id = :uid"), {"uid": user_id}).fetchone()
            strikes = strikes_row[0] if strikes_row else 1

            if strikes >= 3:
                db.execute(
                    text("UPDATE no_show_records SET suspended = true, suspended_until = CURRENT_DATE + 14 WHERE user_id = :uid"),
                    {"uid": user_id},
                )

            released.append({
                "booking_id": b_code,
                "venue": v_name,
                "offending_student": user_id,
                "scheduled_time": start_time_str,
                "total_strikes": strikes,
                "account_suspended": strikes >= 3,
            })

    db.commit()

    return {
        "status": "success",
        "evaluation_time": ref_time,
        "total_auto_released": len(released),
        "auto_released_bookings": released,
        "message": (
            f"{len(released)} hoarded slot(s) auto-released and penalized."
            if released
            else "All active reservations are within the grace period."
        ),
    }


@router.get("/profile/{student_reg_no}")
def get_requisition_profile(student_reg_no: str, db: Session = Depends(get_db)):
    """Retrieve a student's consolidated requisition profile and penalty standing."""
    sports_hours = float(
        db.execute(
            text(
                "SELECT COALESCE(SUM(duration_hours), 0) FROM bookings"
                " WHERE user_id = :uid AND booking_date = CURRENT_DATE"
                " AND status IN ('approved', 'checked_in') AND source = 'sports_reservation'"
            ),
            {"uid": student_reg_no},
        ).fetchone()[0]
    )

    active_bookings = [
        {"booking_id": r[0], "venue_id": r[1], "start_time": r[2], "status": r[3], "checkin_status": r[4]}
        for r in db.execute(
            text("SELECT booking_code, venue_id, start_time, status, checkin_status FROM bookings WHERE user_id = :uid AND status IN ('approved', 'pending')"),
            {"uid": student_reg_no},
        ).fetchall()
    ]

    equipment_held = [
        {"barcode": r[0], "name": r[1], "lab": r[2], "due_date": str(r[3]), "condition": r[4]}
        for r in db.execute(
            text("SELECT barcode, name, lab, due_date, condition FROM lab_equipment WHERE custodian_id = :uid AND status = 'in_custody'"),
            {"uid": student_reg_no},
        ).fetchall()
    ]

    requisitions = [
        {"requisition_id": r[0], "society": r[1], "title": r[2], "date": str(r[3]), "status": r[4], "tier": r[5]}
        for r in db.execute(
            text("SELECT requisition_id, society_name, event_title, booking_date, status, current_tier FROM society_requisitions WHERE applicant_id = :uid"),
            {"uid": student_reg_no},
        ).fetchall()
    ]

    ns_row = db.execute(text("SELECT strikes, suspended, suspended_until FROM no_show_records WHERE user_id = :uid"), {"uid": student_reg_no}).fetchone()
    strikes = ns_row[0] if ns_row else 0
    suspended = ns_row[1] if ns_row else False
    suspended_until = str(ns_row[2]) if (ns_row and ns_row[2]) else None

    standing = "Good Standing"
    if suspended or strikes >= 3:
        standing = f"SUSPENDED (Until {suspended_until})"
    elif strikes == 2:
        standing = "Final Warning (2 Strikes)"
    elif strikes == 1:
        standing = "Warning (1 Strike)"

    return {
        "status": "success",
        "student_reg_no": student_reg_no,
        "standing": standing,
        "sports_daily_quota": {"max_hours": 2.0, "used_today_hours": sports_hours, "remaining_today_hours": max(0.0, 2.0 - sports_hours)},
        "active_reservations_count": len(active_bookings),
        "active_reservations": active_bookings,
        "equipment_in_custody_count": len(equipment_held),
        "equipment_in_custody": equipment_held,
        "submitted_requisitions_count": len(requisitions),
        "submitted_requisitions": requisitions,
        "zero_trust_record": {"strikes": strikes, "suspended": suspended},
    }
