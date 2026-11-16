"""
G-Cortex Requisition Management Subagent
AI-powered campus resource and requisition operating system for GIKI built on Google ADK.
Directly interfaces with the PostgreSQL database for multi-tier digital approval chains,
fair-use sports facility reservations with 2-hour daily caps, lab equipment barcode custody tracking,
natural language venue recommendations, and zero-trust no-show auto-release enforcement.
"""
import os
import json
import time
import hashlib
import datetime
import logging
from pathlib import Path

# Resilient imports for dotenv and google-adk
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return True

try:
    from google.adk.agents.llm_agent import Agent
except ImportError:
    try:
        from google.adk.agents import Agent
    except ImportError:
        class Agent:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

_AGENT_DIR = Path(__file__).resolve().parent
_ENV_FILE = _AGENT_DIR / ".env"
_CREDS_FILE = _AGENT_DIR / "req_credentials.json"

if _ENV_FILE.exists():
    load_dotenv(dotenv_path=_ENV_FILE)
else:
    load_dotenv()

if _CREDS_FILE.exists() and _CREDS_FILE.stat().st_size > 0 and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_CREDS_FILE)

logger = logging.getLogger("g_cortex.requisition_agent")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost/gcortex_db",
)

# ═══════════════════════════════════════════════════════════════
#  Cross-Subagent Event Bus Integration
# ═══════════════════════════════════════════════════════════════
try:
    from app.core.event_bus import event_bus, VENUE_BOOKED, VENUE_RELEASED
except ImportError:
    event_bus = None
    VENUE_BOOKED = "venue.booked"
    VENUE_RELEASED = "venue.released"


def _publish_event(event_type: str, data: dict):
    if event_bus:
        try:
            event_bus.publish(event_type, data)
            logger.info("Published %s: %s", event_type, data)
        except Exception as exc:
            logger.warning("Event bus publication failed for %s: %s", event_type, exc)


# ═══════════════════════════════════════════════════════════════
#  Database Connection Helper (Direct Database Access)
# ═══════════════════════════════════════════════════════════════

def _get_db():
    """Return a fresh psycopg2 connection to gcortex_db."""
    import psycopg2
    url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(url, connect_timeout=5)
    return conn


# ═══════════════════════════════════════════════════════════════
#  Tool 1 — Submit Society Requisition (Multi-tier chain)
# ═══════════════════════════════════════════════════════════════

def submit_society_requisition(
    student_reg_no: str,
    society_name: str,
    venue_id: str,
    event_title: str,
    booking_date: str,
    start_time: str,
    duration_hours: float,
    expected_attendees: int,
    justification: str,
) -> dict:
    """Submits a multi-tier digital requisition for a student society event or major campus venue.
    Queries venue capacity and society advisor details directly from the database, then logs
    the requisition into the society_requisitions database table.

    Args:
        student_reg_no: The student registration number submitting the request (e.g. '2024662').
        society_name: Name of the student society (e.g. 'ACM GIKI Chapter', 'IEEE Student Branch').
        venue_id: Venue code (e.g. 'AHA_AUDI', 'SEM_HALL_1', 'LAB_MEDS', 'LAB_SE', 'OAT_MAIN').
        event_title: Descriptive name of the planned event.
        booking_date: Date of the event in YYYY-MM-DD format (e.g. '2026-11-28').
        start_time: Starting time in HH:MM format (e.g. '15:00').
        duration_hours: Event duration in hours (e.g. 2.5).
        expected_attendees: Estimated number of participants.
        justification: Brief statement of purpose and academic/extracurricular relevance.
    """
    venue_up = venue_id.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT name, capacity FROM venues WHERE venue_id = %s",
        (venue_up,),
    )
    v_row = cur.fetchone()
    if not v_row:
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Venue '{venue_id}' does not exist in database."}

    venue_name, capacity = v_row[0], v_row[1]
    if expected_attendees > capacity:
        cur.close()
        conn.close()
        return {
            "status": "rejected",
            "message": f"Capacity violation: {venue_name} holds at most {capacity} attendees, but {expected_attendees} were requested.",
            "suggestion": "Consider booking AHA_AUDI (300 cap) or OAT_MAIN (500 cap) for larger gatherings.",
        }

    # Query society advisor from campus_societies table
    cur.execute(
        "SELECT advisor_id, advisor_name FROM campus_societies WHERE society_name = %s",
        (society_name,),
    )
    soc_row = cur.fetchone()
    advisor_id = soc_row[0] if soc_row else "FAC001"
    advisor_name = soc_row[1] if soc_row else "Faculty Advisor"

    # Check for scheduling collision in bookings table
    cur.execute(
        """
        SELECT booking_id FROM bookings
        WHERE venue_id = %s AND booking_date = %s AND start_time = %s AND status IN ('approved', 'pending')
        """,
        (venue_up, booking_date, start_time),
    )
    if cur.fetchone():
        cur.close()
        conn.close()
        return {
            "status": "rejected",
            "message": f"Scheduling conflict: {venue_name} is already reserved or pending approval at {start_time} on {booking_date}.",
        }

    # Generate sequential requisition ID
    cur.execute("SELECT COUNT(*) FROM society_requisitions")
    req_count = cur.fetchone()[0]
    req_id = f"REQ-2026-{req_count + 1:03d}"

    approval_chain = [
        {
            "tier": 1,
            "role": "faculty_advisor",
            "assigned_to": advisor_id,
            "advisor_name": advisor_name,
            "action": "pending",
            "timestamp": None,
            "comments": None,
        },
        {
            "tier": 2,
            "role": "student_affairs_admin",
            "assigned_to": "ADMIN01",
            "action": "pending",
            "timestamp": None,
            "comments": None,
        },
    ]

    cur.execute(
        """
        INSERT INTO society_requisitions
        (requisition_id, society_name, venue_id, applicant_id, event_title, booking_date, start_time, duration_hours, expected_attendees, justification, current_tier, status, approval_chain)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'pending_advisor_approval', %s)
        """,
        (
            req_id,
            society_name,
            venue_up,
            student_reg_no,
            event_title,
            booking_date,
            start_time,
            duration_hours,
            expected_attendees,
            justification,
            json.dumps(approval_chain),
        ),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "submitted",
        "requisition_id": req_id,
        "venue": venue_name,
        "approval_state": "pending_advisor_approval",
        "current_tier": 1,
        "next_approver": f"{advisor_name} ({advisor_id})",
        "routing_pipeline": "Tier 1: Faculty Advisor Review -> Tier 2: Directorate of Student Affairs & Venue Admin",
        "message": f"Requisition {req_id} for '{event_title}' logged. Digitally routed to {advisor_name} for Tier-1 endorsement.",
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 2 — Review Requisition Tier (Advancing multi-tier approval)
# ═══════════════════════════════════════════════════════════════

def review_requisition_tier(
    requisition_id: str,
    reviewer_id: str,
    reviewer_role: str,
    action: str,
    comments: str = "Proceed as requested",
) -> dict:
    """Processes a review decision (approve or reject) at the current tier of a requisition approval chain
    and updates the database record accordingly.

    Args:
        requisition_id: The ID of the requisition to review (e.g. 'REQ-2026-001').
        reviewer_id: Identifier of the reviewer (e.g. 'FAC001', 'ADMIN01').
        reviewer_role: Role of the reviewer ('faculty' or 'admin').
        action: Review decision ('approve' or 'reject').
        comments: Optional remarks or guidance from the reviewer.
    """
    req_up = requisition_id.upper().strip()
    action_clean = action.lower().strip()
    if action_clean not in ("approve", "reject"):
        return {"status": "error", "message": "Invalid action. Must be 'approve' or 'reject'."}

    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT requisition_id, society_name, venue_id, applicant_id, event_title,
               booking_date, start_time, duration_hours, status, current_tier, approval_chain
        FROM society_requisitions
        WHERE requisition_id = %s
        """,
        (req_up,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Requisition '{requisition_id}' not found in database."}

    req_id, soc_name, venue_id, applicant_id, title, b_date, s_time, dur, current_status, tier, chain_raw = row
    approval_chain = json.loads(chain_raw) if chain_raw else []
    now_iso = datetime.datetime.now().isoformat()

    if current_status in ("approved", "rejected", "auto-released"):
        cur.close()
        conn.close()
        return {
            "status": "error",
            "message": f"Requisition {requisition_id} is already in terminal state: {current_status}.",
        }

    # ── Tier 1: Faculty / Society Advisor ──────────────────────
    if current_status == "pending_advisor_approval":
        if reviewer_role.lower() not in ("faculty", "admin"):
            cur.close()
            conn.close()
            return {
                "status": "unauthorized",
                "message": "Tier 1 endorsement requires Faculty Advisor clearance. Role 'faculty' or 'admin' required.",
            }

        tier1_node = approval_chain[0]
        if action_clean == "reject":
            tier1_node["action"] = "rejected"
            tier1_node["reviewer_id"] = reviewer_id
            tier1_node["timestamp"] = now_iso
            tier1_node["comments"] = comments

            cur.execute(
                "UPDATE society_requisitions SET status = 'rejected', approval_chain = %s WHERE requisition_id = %s",
                (json.dumps(approval_chain), req_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            return {
                "status": "rejected",
                "requisition_id": req_id,
                "tier_processed": 1,
                "message": f"Requisition {req_id} rejected at Tier 1 by {reviewer_id}. Reason: {comments}",
            }

        # Advance to Tier 2
        tier1_node["action"] = "approved"
        tier1_node["reviewer_id"] = reviewer_id
        tier1_node["timestamp"] = now_iso
        tier1_node["comments"] = comments

        cur.execute(
            """
            UPDATE society_requisitions
            SET status = 'pending_admin_approval', current_tier = 2, approval_chain = %s
            WHERE requisition_id = %s
            """,
            (json.dumps(approval_chain), req_id),
        )
        conn.commit()
        cur.close()
        conn.close()

        return {
            "status": "advanced",
            "requisition_id": req_id,
            "tier_completed": 1,
            "next_tier": 2,
            "approval_state": "pending_admin_approval",
            "next_approver": "Directorate of Student Affairs / Venue Admin (ADMIN01)",
            "message": f"Tier 1 endorsed by {reviewer_id}. Requisition forwarded to Student Affairs Admin for final clearance.",
        }

    # ── Tier 2: Student Affairs & Venue Admin ──────────────────
    if current_status == "pending_admin_approval":
        if reviewer_role.lower() != "admin":
            cur.close()
            conn.close()
            return {
                "status": "unauthorized",
                "message": "Tier 2 final clearance requires Admin credentials. Role 'admin' required.",
            }

        tier2_node = approval_chain[1]
        if action_clean == "reject":
            tier2_node["action"] = "rejected"
            tier2_node["reviewer_id"] = reviewer_id
            tier2_node["timestamp"] = now_iso
            tier2_node["comments"] = comments

            cur.execute(
                "UPDATE society_requisitions SET status = 'rejected', approval_chain = %s WHERE requisition_id = %s",
                (json.dumps(approval_chain), req_id),
            )
            conn.commit()
            cur.close()
            conn.close()
            return {
                "status": "rejected",
                "requisition_id": req_id,
                "tier_processed": 2,
                "message": f"Requisition {req_id} rejected at Tier 2 by {reviewer_id}. Reason: {comments}",
            }

        # Approved at Tier 2 -> Fully approved & registered into bookings table
        tier2_node["action"] = "approved"
        tier2_node["reviewer_id"] = reviewer_id
        tier2_node["timestamp"] = now_iso
        tier2_node["comments"] = comments

        cur.execute("SELECT COUNT(*) FROM bookings")
        b_count = cur.fetchone()[0]
        booking_code = f"BK-EVT-{b_count + 101}"

        token_seed = f"{booking_code}:{applicant_id}:{time.time()}"
        checkin_pin = f"CHK-{hashlib.sha256(token_seed.encode()).hexdigest()[:4].upper()}"

        cur.execute(
            """
            UPDATE society_requisitions
            SET status = 'approved', current_tier = 2, approval_chain = %s
            WHERE requisition_id = %s
            """,
            (json.dumps(approval_chain), req_id),
        )

        cur.execute(
            """
            INSERT INTO bookings
            (booking_code, venue_id, user_id, booking_time, booking_date, start_time, duration_hours, status, checkin_token, checkin_status, source)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, 'approved', %s, 'pending_checkin', %s)
            """,
            (booking_code, venue_id, applicant_id, b_date, s_time, dur, checkin_pin, f"requisition:{req_id}"),
        )
        conn.commit()
        cur.close()
        conn.close()

        # Publish cross-subagent event
        _publish_event(
            VENUE_BOOKED,
            {
                "booking_code": booking_code,
                "venue_id": venue_id,
                "user_id": applicant_id,
                "society_name": soc_name,
                "booking_date": str(b_date),
                "start_time": s_time,
            },
        )

        return {
            "status": "approved",
            "requisition_id": req_id,
            "booking_id": booking_code,
            "checkin_token": checkin_pin,
            "message": f"Requisition {req_id} fully approved across all tiers! Confirmed under Booking {booking_code}.",
            "zero_trust_guidance": f"Physical check-in required within 15 minutes of {s_time} using token {checkin_pin} to avoid no-show auto-release.",
        }

    cur.close()
    conn.close()
    return {"status": "error", "message": f"Unhandled state {current_status}."}


# ═══════════════════════════════════════════════════════════════
#  Tool 3 — Get Requisition Status & Audit Trail
# ═══════════════════════════════════════════════════════════════

def get_requisition_status(requisition_id: str) -> dict:
    """Retrieves full details of a society venue requisition including its multi-tier approval trail directly from the database.

    Args:
        requisition_id: Requisition code (e.g. 'REQ-2026-001').
    """
    req_up = requisition_id.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT sr.requisition_id, sr.society_name, sr.event_title, v.name,
               sr.booking_date, sr.start_time, sr.duration_hours, sr.expected_attendees,
               sr.status, sr.current_tier, sr.approval_chain
        FROM society_requisitions sr
        JOIN venues v ON v.venue_id = sr.venue_id
        WHERE sr.requisition_id = %s
        """,
        (req_up,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return {"status": "error", "message": f"Requisition '{requisition_id}' not found in database."}

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


# ═══════════════════════════════════════════════════════════════
#  Tool 4 — Reserve Sports Facility (Fair-Use Quotas)
# ═══════════════════════════════════════════════════════════════

def reserve_sports_facility(
    student_reg_no: str,
    facility_id: str,
    booking_date: str,
    start_time: str,
    duration_hours: float = 1.0,
) -> dict:
    """Reserves a campus athletic space with strict enforcement of the fair-use daily quota
    (maximum 2 hours per student per day) and zero-trust anti-hoarding verification directly in the database.

    Args:
        student_reg_no: Student registration number (e.g. '2024405').
        facility_id: Sports facility code (e.g. 'SPORTS_C1', 'SPORTS_C2', 'SPORTS_SQ1', 'SPORTS_TC1', 'SPORTS_FUTSAL', 'SPORTS_TT1', 'SPORTS_GYM').
        booking_date: Date of play in YYYY-MM-DD format (e.g. '2026-11-18').
        start_time: Start time in HH:MM format (e.g. '17:00').
        duration_hours: Booking duration in hours (1.0 or 2.0). Daily cap is 2.0 hours.
    """
    fid_up = facility_id.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    # 1. Verify facility existence and category
    cur.execute(
        "SELECT name, category FROM venues WHERE venue_id = %s",
        (fid_up,),
    )
    v_row = cur.fetchone()
    if not v_row or v_row[1] != "sports":
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Invalid sports facility '{facility_id}'."}

    facility_name = v_row[0]

    # 2. Check no-show strikes and suspension standing in database
    cur.execute(
        "SELECT strikes, suspended FROM no_show_records WHERE user_id = %s",
        (student_reg_no,),
    )
    ns_row = cur.fetchone()
    if ns_row and (ns_row[1] or ns_row[0] >= 3):
        cur.close()
        conn.close()
        return {
            "status": "rejected",
            "student_reg_no": student_reg_no,
            "reason": "Account suspended due to 3 zero-trust no-show penalties. Booking privileges temporarily locked.",
        }

    # 3. Fair-use daily quota check (Max 2.0 hours per day per student)
    MAX_DAILY_HOURS = 2.0
    cur.execute(
        """
        SELECT COALESCE(SUM(duration_hours), 0)
        FROM bookings
        WHERE user_id = %s AND booking_date = %s AND status IN ('approved', 'checked_in', 'pending')
              AND source = 'sports_reservation'
        """,
        (student_reg_no, booking_date),
    )
    existing_hours = float(cur.fetchone()[0])

    if existing_hours + duration_hours > MAX_DAILY_HOURS:
        cur.close()
        conn.close()
        remaining_quota = max(0.0, MAX_DAILY_HOURS - existing_hours)
        return {
            "status": "rejected",
            "student_reg_no": student_reg_no,
            "daily_limit_hours": MAX_DAILY_HOURS,
            "already_booked_today_hours": existing_hours,
            "remaining_quota_hours": remaining_quota,
            "reason": (
                f"Fair-use quota exceeded: You already have {existing_hours:.1f} hour(s) booked on {booking_date}. "
                f"University fair-use policy limits each student to {MAX_DAILY_HOURS:.1f} hours/day to prevent slot hoarding."
            ),
        }

    # 4. Check slot conflict for this facility in database
    cur.execute(
        """
        SELECT booking_id FROM bookings
        WHERE venue_id = %s AND booking_date = %s AND start_time = %s AND status IN ('approved', 'checked_in')
        """,
        (fid_up, booking_date, start_time),
    )
    if cur.fetchone():
        cur.close()
        conn.close()
        return {
            "status": "rejected",
            "reason": f"Slot conflict: {facility_name} is already reserved at {start_time} on {booking_date}.",
        }

    # 5. Insert booking into database
    cur.execute("SELECT COUNT(*) FROM bookings")
    b_count = cur.fetchone()[0]
    booking_code = f"BK-SPT-{b_count + 101}"

    token_seed = f"{booking_code}:{student_reg_no}:{booking_date}:{start_time}"
    checkin_pin = f"CHK-{hashlib.sha256(token_seed.encode()).hexdigest()[:4].upper()}"

    cur.execute(
        """
        INSERT INTO bookings
        (booking_code, venue_id, user_id, booking_time, booking_date, start_time, duration_hours, status, checkin_token, checkin_status, source)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, 'approved', %s, 'pending_checkin', 'sports_reservation')
        """,
        (booking_code, fid_up, student_reg_no, booking_date, start_time, duration_hours, checkin_pin),
    )
    conn.commit()
    cur.close()
    conn.close()

    # Publish cross-subagent event
    _publish_event(
        VENUE_BOOKED,
        {
            "booking_code": booking_code,
            "venue_id": fid_up,
            "user_id": student_reg_no,
            "booking_date": booking_date,
            "start_time": start_time,
            "facility_type": "sports",
        },
    )

    remaining_hours = MAX_DAILY_HOURS - (existing_hours + duration_hours)

    return {
        "status": "confirmed",
        "booking_id": booking_code,
        "facility": facility_name,
        "date": booking_date,
        "time": f"{start_time} ({duration_hours} hr)",
        "checkin_token": checkin_pin,
        "daily_quota_consumed_hours": existing_hours + duration_hours,
        "daily_quota_remaining_hours": remaining_hours,
        "zero_trust_warning": (
            f"Zero-trust check-in required within 15 minutes of {start_time} using PIN '{checkin_pin}'. "
            "Unclaimed slots are auto-released to prevent slot hoarding, and accrue a penalty strike."
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 5 — Check Sports Facility Availability
# ═══════════════════════════════════════════════════════════════

def check_sports_facility_availability(facility_id: str, booking_date: str) -> dict:
    """Checks booked time slots and available windows for sports facilities directly from the database.

    Args:
        facility_id: Sports facility code (e.g. 'SPORTS_C1', 'SPORTS_SQ1', 'SPORTS_FUTSAL', or 'all').
        booking_date: Date to check in YYYY-MM-DD format (e.g. '2026-11-16').
    """
    conn = _get_db()
    cur = conn.cursor()

    if facility_id.upper() == "ALL":
        cur.execute("SELECT venue_id, name, capacity FROM venues WHERE category = 'sports' ORDER BY venue_id")
    else:
        cur.execute("SELECT venue_id, name, capacity FROM venues WHERE venue_id = %s", (facility_id.upper().strip(),))

    facilities = cur.fetchall()
    operating_slots = ["08:00", "09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
    facility_reports = []

    for fid, fname, cap in facilities:
        cur.execute(
            """
            SELECT start_time FROM bookings
            WHERE venue_id = %s AND booking_date = %s AND status IN ('approved', 'checked_in')
            """,
            (fid, booking_date),
        )
        booked = [r[0] for r in cur.fetchall()]
        available = [slot for slot in operating_slots if slot not in booked]

        facility_reports.append(
            {
                "facility_id": fid,
                "name": fname,
                "total_operating_slots": len(operating_slots),
                "booked_slots": booked,
                "available_slots": available,
                "capacity": cap,
            }
        )

    cur.close()
    conn.close()

    return {
        "status": "success",
        "date": booking_date,
        "facilities_queried": len(facility_reports),
        "reports": facility_reports,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 6 — Natural Language Venue Recommender
# ═══════════════════════════════════════════════════════════════

def recommend_venue(
    event_description: str,
    expected_attendees: int,
    required_facilities: str = "none",
) -> dict:
    """Recommends optimal campus venues by querying venue capacities, categories, and amenities
    from the venues database table and performing semantic suitability scoring.

    Args:
        event_description: Natural language summary of the event (e.g. '35-person AI coding workshop with GPUs and PCs').
        expected_attendees: Anticipated number of participants.
        required_facilities: Comma-separated required equipment or amenities (e.g. 'projector, workstations, lan' or 'stage, sound_system').
    """
    desc_lower = event_description.lower()
    req_facs = [f.strip().lower() for f in required_facilities.split(",") if f.strip().lower() != "none"]

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT venue_id, name, capacity, requires_approval, category, facilities, description FROM venues"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    scored_venues = []
    for vid, name, cap, req_app, cat, facs_raw, description in rows:
        facs_list = [f.strip() for f in (facs_raw or "").split(",") if f.strip()]
        if cap < expected_attendees:
            continue

        score = 0
        if expected_attendees <= cap <= expected_attendees * 1.5:
            score += 45
        elif expected_attendees * 1.5 < cap <= expected_attendees * 3:
            score += 30
        else:
            score += 15

        if any(w in desc_lower for w in ("hackathon", "coding", "software", "programming", "python")):
            if vid in ("LAB_SE", "LAB_MEDS"):
                score += 40
            elif vid in ("AHA_AUDI", "SEM_HALL_1"):
                score += 15

        if any(w in desc_lower for w in ("hardware", "circuit", "fpga", "oscilloscope", "microcontroller")):
            if vid == "LAB_MEDS":
                score += 50

        if any(w in desc_lower for w in ("seminar", "guest lecture", "symposium", "conference", "keynote", "talk")):
            if vid in ("AHA_AUDI", "SEM_HALL_1"):
                score += 40

        if any(w in desc_lower for w in ("sports", "badminton", "squash", "tennis", "futsal", "match")):
            if cat == "sports":
                score += 50

        matched_facilities = []
        for rf in req_facs:
            for vf in facs_list:
                if rf in vf.lower():
                    score += 10
                    matched_facilities.append(vf)

        scored_venues.append(
            {
                "venue_id": vid,
                "name": name,
                "capacity": cap,
                "capacity_utilization_pct": round((expected_attendees / cap) * 100, 1),
                "approval_required": req_app,
                "facilities_available": facs_list,
                "facilities_matched": list(set(matched_facilities)),
                "match_score": min(score, 100),
                "recommendation_reason": description,
            }
        )

    scored_venues.sort(key=lambda x: x["match_score"], reverse=True)
    top_picks = scored_venues[:3]

    return {
        "status": "success",
        "query_attendees": expected_attendees,
        "query_description": event_description,
        "recommendations_count": len(top_picks),
        "top_recommendations": top_picks,
        "guidance": (
            f"Top match: {top_picks[0]['name']} ({top_picks[0]['match_score']}% match)."
            if top_picks
            else "No venue found meeting specified attendee threshold."
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 7 — Lab Equipment Barcode Check-Out
# ═══════════════════════════════════════════════════════════════

def checkout_lab_equipment(
    student_reg_no: str,
    equipment_barcode: str,
    purpose: str,
    duration_days: int = 3,
) -> dict:
    """Checks out laboratory hardware using its unique barcode asset identifier directly in the database.

    Args:
        student_reg_no: Student registration number taking custody (e.g. '2024662').
        equipment_barcode: Equipment barcode asset tag (e.g. 'BAR-OSC-201', 'BAR-FPGA-302', 'BAR-LA-401').
        purpose: Academic or project purpose (e.g. 'CS325 Software Engineering Project', 'EE213 Lab Experiment').
        duration_days: Custody period in days (maximum 7 days allowed per university policy).
    """
    b_code = equipment_barcode.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT name, status, custodian_id, due_date FROM lab_equipment WHERE barcode = %s",
        (b_code,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Barcode '{equipment_barcode}' not found in database."}

    eq_name, status, custodian, due_date = row[0], row[1], row[2], row[3]
    if status == "in_custody":
        cur.close()
        conn.close()
        return {
            "status": "unavailable",
            "barcode": b_code,
            "equipment_name": eq_name,
            "current_custodian": custodian,
            "due_date": str(due_date),
            "message": f"Equipment is currently in custody of student {custodian} until {due_date}.",
        }

    if duration_days > 7:
        cur.close()
        conn.close()
        return {"status": "rejected", "message": "Maximum lab hardware checkout duration is 7 days per academic policy."}

    now = datetime.date.today()
    due = now + datetime.timedelta(days=duration_days)

    cur.execute(
        """
        UPDATE lab_equipment
        SET status = 'in_custody', custodian_id = %s, checkout_date = %s, due_date = %s, purpose = %s
        WHERE barcode = %s
        """,
        (student_reg_no, now, due, purpose, b_code),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "checked_out",
        "barcode": b_code,
        "equipment_name": eq_name,
        "assigned_to": student_reg_no,
        "checkout_date": now.isoformat(),
        "due_date": due.isoformat(),
        "purpose": purpose,
        "custody_terms": "Student assumes personal custody responsibility. Return by due date to avoid late fines.",
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 8 — Lab Equipment Barcode Check-In (Return & Clearance)
# ═══════════════════════════════════════════════════════════════

def return_lab_equipment(
    equipment_barcode: str,
    student_reg_no: str,
    reported_condition: str = "good",
) -> dict:
    """Processes the return and custody clearance of checked-out laboratory hardware via barcode scan
    directly in the database.

    Args:
        equipment_barcode: Equipment barcode tag (e.g. 'BAR-RPI-105', 'BAR-OSC-201').
        student_reg_no: Student registration number returning the item.
        reported_condition: Physical condition upon inspection ('good', 'fair', 'damaged').
    """
    b_code = equipment_barcode.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT name, status, custodian_id, due_date FROM lab_equipment WHERE barcode = %s",
        (b_code,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Barcode '{equipment_barcode}' not found in database."}

    eq_name, status, custodian, due_date = row[0], row[1], row[2], row[3]
    if status != "in_custody":
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Equipment '{eq_name}' is not currently in custody ({status})."}

    if custodian != student_reg_no:
        cur.close()
        conn.close()
        return {
            "status": "warning",
            "message": f"Custodian mismatch: Registered to {custodian}, return submitted by {student_reg_no}.",
        }

    is_overdue = False
    overdue_days = 0
    if due_date:
        today = datetime.date.today()
        if today > due_date:
            is_overdue = True
            overdue_days = (today - due_date).days

    next_status = "available" if reported_condition.lower() != "damaged" else "under_repair"

    cur.execute(
        """
        UPDATE lab_equipment
        SET status = %s, custodian_id = NULL, checkout_date = NULL, due_date = NULL, condition = %s
        WHERE barcode = %s
        """,
        (next_status, reported_condition, b_code),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "returned",
        "barcode": b_code,
        "equipment_name": eq_name,
        "cleared_student": student_reg_no,
        "return_condition": reported_condition,
        "was_overdue": is_overdue,
        "overdue_days": overdue_days,
        "clearance_status": "CLEARED: Student laboratory custody ledger updated in database.",
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 9 — Query Lab Equipment Inventory & Custody Records
# ═══════════════════════════════════════════════════════════════

def query_equipment_custody(query_type: str = "all", filter_value: str = "none") -> dict:
    """Queries campus laboratory hardware inventory, active student custody slips, and overdue asset lists
    directly from the database.

    Args:
        query_type: Query mode ('all', 'available', 'in_custody', 'by_student', 'by_barcode').
        filter_value: Value to filter by when query_type is 'by_student' (e.g. '2024405') or 'by_barcode' (e.g. 'BAR-OSC-201').
    """
    mode = query_type.lower().strip()
    conn = _get_db()
    cur = conn.cursor()

    if mode == "all":
        cur.execute("SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment ORDER BY barcode")
    elif mode == "available":
        cur.execute("SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment WHERE status = 'available' ORDER BY barcode")
    elif mode == "in_custody":
        cur.execute("SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment WHERE status = 'in_custody' ORDER BY barcode")
    elif mode == "by_student":
        cur.execute("SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment WHERE custodian_id = %s", (filter_value.strip(),))
    elif mode == "by_barcode":
        cur.execute("SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment WHERE barcode = %s", (filter_value.upper().strip(),))
    else:
        cur.execute("SELECT barcode, name, lab, category, status, custodian_id, due_date, condition FROM lab_equipment ORDER BY barcode")

    rows = cur.fetchall()
    cur.close()
    conn.close()

    items = [
        {
            "barcode": r[0],
            "name": r[1],
            "lab": r[2],
            "category": r[3],
            "status": r[4],
            "custodian": r[5],
            "due_date": str(r[6]) if r[6] else None,
            "condition": r[7],
        }
        for r in rows
    ]

    return {
        "status": "success",
        "query_mode": mode,
        "total_matched": len(items),
        "items": items,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 10 — Zero-Trust Reservation Check-In
# ═══════════════════════════════════════════════════════════════

def process_zero_trust_checkin(
    booking_id: str,
    student_reg_no: str,
    checkin_token: str,
) -> dict:
    """Validates physical presence check-in for an active reservation within the 15-minute grace window
    directly in the database.

    Args:
        booking_id: The booking identifier (e.g. 'BK-SPT-101', 'BK-EVT-101').
        student_reg_no: The student registration number holding the reservation.
        checkin_token: 6-character check-in PIN issued during reservation (e.g. 'CHK-4821').
    """
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT b.booking_code, b.user_id, b.status, b.checkin_status, b.checkin_token, v.name
        FROM bookings b
        JOIN venues v ON v.venue_id = b.venue_id
        WHERE b.booking_code = %s
        """,
        (booking_id.upper().strip(),),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Booking ID '{booking_id}' not found in database."}

    b_code, user_id, status, checkin_status, token, v_name = row

    if user_id != student_reg_no:
        cur.close()
        conn.close()
        return {
            "status": "unauthorized",
            "message": f"Student {student_reg_no} is not authorized for booking {booking_id}. Registered to {user_id}.",
        }

    if status == "auto-released":
        cur.close()
        conn.close()
        return {
            "status": "expired",
            "message": "This booking has already been auto-released due to no-show past the 15-minute grace window.",
        }

    if checkin_status == "checked_in":
        cur.close()
        conn.close()
        return {
            "status": "already_checked_in",
            "booking_id": booking_id,
            "message": "Check-in already completed. Venue session is active.",
        }

    if token and token.upper() != checkin_token.upper().strip():
        cur.close()
        conn.close()
        return {
            "status": "invalid_token",
            "message": "Invalid check-in PIN. Please check the token provided in your booking confirmation.",
        }

    cur.execute(
        "UPDATE bookings SET checkin_status = 'checked_in' WHERE booking_code = %s",
        (b_code,),
    )
    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "checked_in",
        "booking_id": booking_id,
        "venue": v_name,
        "student": student_reg_no,
        "session_state": "ACTIVE",
        "message": f"Zero-trust presence verified! Access granted to {v_name}.",
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 11 — Zero-Trust No-Show Auto-Release Engine
# ═══════════════════════════════════════════════════════════════

def enforce_zero_trust_auto_release(current_time_override: str = "none") -> dict:
    """Scans all active reservations in the database. If 15 minutes have elapsed since the scheduled
    start time and check-in has not been performed, automatically releases the slot, logs a no-show strike
    against the student, and publishes a VENUE_RELEASED event.

    Args:
        current_time_override: Optional HH:MM string to evaluate against (e.g. '14:20'). Defaults to live system time.
    """
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT b.booking_code, b.venue_id, b.user_id, b.start_time, v.name
        FROM bookings b
        JOIN venues v ON v.venue_id = b.venue_id
        WHERE b.status = 'approved' AND b.checkin_status = 'pending_checkin' AND b.booking_date = CURRENT_DATE
        """
    )
    active_rows = cur.fetchall()

    if current_time_override.lower() != "none":
        ref_time = current_time_override
    else:
        ref_time = datetime.datetime.now().strftime("%H:%M")

    ref_parts = [int(p) for p in ref_time.split(":")]
    ref_minutes = ref_parts[0] * 60 + ref_parts[1]

    auto_released_list = []
    for b_code, v_id, user_id, start_time_str, v_name in active_rows:
        b_parts = [int(p) for p in (start_time_str or "00:00").split(":")]
        b_minutes = b_parts[0] * 60 + b_parts[1]

        if ref_minutes > (b_minutes + 15):
            cur.execute(
                "UPDATE bookings SET status = 'auto-released', checkin_status = 'auto-released' WHERE booking_code = %s",
                (b_code,),
            )
            cur.execute(
                """
                INSERT INTO no_show_records (user_id, strikes, suspended)
                VALUES (%s, 1, false)
                ON CONFLICT (user_id) DO UPDATE SET strikes = no_show_records.strikes + 1;
                """,
                (user_id,),
            )
            cur.execute("SELECT strikes FROM no_show_records WHERE user_id = %s", (user_id,))
            strikes = cur.fetchone()[0]

            is_suspended = strikes >= 3
            if is_suspended:
                cur.execute(
                    "UPDATE no_show_records SET suspended = true, suspended_until = CURRENT_DATE + 14 WHERE user_id = %s",
                    (user_id,),
                )

            _publish_event(
                VENUE_RELEASED,
                {
                    "booking_code": b_code,
                    "venue_id": v_id,
                    "freed_time": start_time_str,
                    "reason": "zero_trust_no_show_auto_release",
                },
            )

            auto_released_list.append(
                {
                    "booking_id": b_code,
                    "venue": v_name,
                    "offending_student": user_id,
                    "scheduled_time": start_time_str,
                    "total_student_strikes": strikes,
                    "account_suspended": is_suspended,
                }
            )

    conn.commit()
    cur.close()
    conn.close()

    return {
        "status": "success",
        "evaluation_time": ref_time,
        "total_auto_released": len(auto_released_list),
        "auto_released_bookings": auto_released_list,
        "message": (
            f"Zero-trust check completed: {len(auto_released_list)} hoarded slots auto-released to campus pool and penalized."
            if auto_released_list
            else "All active reservations are checked-in or within allowable grace period."
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 12 — Get Student Requisition Profile & Standing
# ═══════════════════════════════════════════════════════════════

def get_student_requisition_profile(student_reg_no: str) -> dict:
    """Retrieves a consolidated overview of a student's requisition activity directly from the database:
    sports daily quota usage, active bookings, equipment in custody, society requisitions, and penalty standing.

    Args:
        student_reg_no: Student registration number (e.g. '2024405', '2024668', '2024662').
    """
    conn = _get_db()
    cur = conn.cursor()

    # Sports quota used today
    cur.execute(
        """
        SELECT COALESCE(SUM(duration_hours), 0)
        FROM bookings
        WHERE user_id = %s AND booking_date = CURRENT_DATE AND status IN ('approved', 'checked_in')
              AND source = 'sports_reservation'
        """,
        (student_reg_no,),
    )
    sports_hours_today = float(cur.fetchone()[0])

    # Active reservations
    cur.execute(
        """
        SELECT booking_code, venue_id, start_time, status, checkin_status
        FROM bookings
        WHERE user_id = %s AND status IN ('approved', 'pending')
        """,
        (student_reg_no,),
    )
    active_bookings = [
        {"booking_id": r[0], "venue_id": r[1], "start_time": r[2], "status": r[3], "checkin_status": r[4]}
        for r in cur.fetchall()
    ]

    # Equipment in custody
    cur.execute(
        """
        SELECT barcode, name, lab, due_date, condition
        FROM lab_equipment
        WHERE custodian_id = %s AND status = 'in_custody'
        """,
        (student_reg_no,),
    )
    equipment_held = [
        {"barcode": r[0], "name": r[1], "lab": r[2], "due_date": str(r[3]), "condition": r[4]}
        for r in cur.fetchall()
    ]

    # Society requisitions
    cur.execute(
        """
        SELECT requisition_id, society_name, event_title, booking_date, status, current_tier
        FROM society_requisitions
        WHERE applicant_id = %s
        """,
        (student_reg_no,),
    )
    requisitions = [
        {"requisition_id": r[0], "society": r[1], "title": r[2], "date": str(r[3]), "status": r[4], "tier": r[5]}
        for r in cur.fetchall()
    ]

    # No-show penalty record
    cur.execute(
        "SELECT strikes, suspended, suspended_until FROM no_show_records WHERE user_id = %s",
        (student_reg_no,),
    )
    ns_row = cur.fetchone()
    strikes = ns_row[0] if ns_row else 0
    suspended = ns_row[1] if ns_row else False
    suspended_until = str(ns_row[2]) if (ns_row and ns_row[2]) else None
    cur.close()
    conn.close()

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
        "sports_daily_quota": {
            "max_hours": 2.0,
            "used_today_hours": sports_hours_today,
            "remaining_today_hours": max(0.0, 2.0 - sports_hours_today),
        },
        "active_reservations_count": len(active_bookings),
        "active_reservations": active_bookings,
        "equipment_in_custody_count": len(equipment_held),
        "equipment_in_custody": equipment_held,
        "submitted_requisitions_count": len(requisitions),
        "submitted_requisitions": requisitions,
        "zero_trust_record": {"strikes": strikes, "suspended": suspended},
    }


# ═══════════════════════════════════════════════════════════════
#  Agent Definition
# ═══════════════════════════════════════════════════════════════

_REQUISITION_INSTRUCTION = """\
You are the **G-Cortex Requisition Management Subagent** — the autonomous campus resource,
spatial allocation, and hardware custody operating system for GIKI.
All venue records, bookings, society requisitions, and lab equipment custody ledgers are
queried directly from the G-Cortex PostgreSQL database.

## Your specialized capabilities & tools:

1. **Multi-Tier Digital Approval Chains (`submit_society_requisition`, `review_requisition_tier`, `get_requisition_status`)**:
   - Manage society event proposals directly in the database.
   - Enforce Tier 1 (Faculty Advisor) and Tier 2 (Directorate of Student Affairs) approvals.

2. **Fair-Use Sports Reservations (`reserve_sports_facility`, `check_sports_facility_availability`)**:
   - Enforce the strict 2.0-hour daily quota per student per day across athletic facilities.
   - Prevent slot hoarding and record reservations directly in the database bookings table.

3. **Natural Language Venue Recommender (`recommend_venue`)**:
   - Semantically score venues from the database based on attendee count, event nature, and amenities.

4. **Lab Equipment Barcode Custody (`checkout_lab_equipment`, `return_lab_equipment`, `query_equipment_custody`)**:
   - Manage digital custody and 7-day loan limits directly against the lab_equipment database table.

5. **Zero-Trust No-Show Auto-Release (`process_zero_trust_checkin`, `enforce_zero_trust_auto_release`)**:
   - Validate physical presence within 15 minutes of start time using a 6-character check-in PIN.
   - Automatically reclaim hoarded slots and log penalty strikes in the database.

6. **Student Requisition Profile (`get_student_requisition_profile`)**:
   - Query a student's active bookings, daily sports quota, equipment loans, and strike standing.

## Behaviour guidelines:
- Present all booking and approval statuses with clear indicators (✅ Approved, ⏳ Pending, ❌ Rejected).
- Clearly explain fair-use quota limits whenever a sports booking is requested.
- Always remind students about the 15-minute zero-trust check-in window and provide their check-in PIN.
"""

ALL_REQUISITION_TOOLS = [
    submit_society_requisition,
    review_requisition_tier,
    get_requisition_status,
    reserve_sports_facility,
    check_sports_facility_availability,
    recommend_venue,
    checkout_lab_equipment,
    return_lab_equipment,
    query_equipment_custody,
    process_zero_trust_checkin,
    enforce_zero_trust_auto_release,
    get_student_requisition_profile,
]

requisition_management_subagent = Agent(
    name="requisition_management_subagent",
    model="gemini-3.5-flash-lite",
    description=(
        "AI-powered Requisition Management Subagent for GIKI. Directly interfaces with the database for "
        "multi-tier society approval chains, fair-use sports facility reservations with 2-hour daily caps, "
        "lab equipment barcode custody tracking, natural language venue recommendations, and zero-trust no-show auto-release rules."
    ),
    instruction=_REQUISITION_INSTRUCTION,
    tools=ALL_REQUISITION_TOOLS,
)

root_agent = Agent(
    name="requisition_management_agent",
    model="gemini-3.5-flash-lite",
    description="G-Cortex Requisition Management Agent for campus resource allocation and digital requisitions.",
    instruction=_REQUISITION_INSTRUCTION,
    tools=ALL_REQUISITION_TOOLS,
)
