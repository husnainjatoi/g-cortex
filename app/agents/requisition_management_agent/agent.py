"""
G-Cortex Requisition Management Subagent
AI-powered campus resource and requisition operating system for GIKI built on Google ADK.
Provides autonomous tools for multi-tier digital approval chains, fair-use sports reservations,
lab equipment barcode custody tracking, natural language venue recommendations, and zero-trust
no-show auto-release enforcement.
"""
import os
import json
import time
import hashlib
import datetime
import logging
from pathlib import Path
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return True

# Import Agent from Google ADK with robust fallback
try:
    from google.adk.agents.llm_agent import Agent
except ImportError:
    try:
        from google.adk.agents import Agent
    except ImportError:
        class Agent:  # Fallback stub for environments without google-adk
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

# Load agent-specific environment variables and service account credentials
_AGENT_DIR = Path(__file__).resolve().parent
_ENV_FILE = _AGENT_DIR / ".env"
_CREDS_FILE = _AGENT_DIR / "req_credentials.json"

if _ENV_FILE.exists():
    load_dotenv(dotenv_path=_ENV_FILE)
else:
    load_dotenv()

if _CREDS_FILE.exists() and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
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
    """Safely publish an event to the shared G-Cortex event bus."""
    if event_bus:
        try:
            event_bus.publish(event_type, data)
            logger.info("Published %s: %s", event_type, data)
        except Exception as exc:
            logger.warning("Event bus publication failed for %s: %s", event_type, exc)


# ═══════════════════════════════════════════════════════════════
#  Database Connection Helper (Dual-mode: PostgreSQL + In-Memory)
# ═══════════════════════════════════════════════════════════════
def _get_db():
    """Return a fresh psycopg2 connection to gcortex_db if available."""
    try:
        import psycopg2
        url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
        conn = psycopg2.connect(url, connect_timeout=3)
        return conn
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
#  Institutional Knowledge & State Registers
# ═══════════════════════════════════════════════════════════════

VENUES_CATALOG = {
    "AHA_AUDI": {
        "venue_id": "AHA_AUDI",
        "name": "AHA Auditorium",
        "capacity": 300,
        "requires_approval": True,
        "category": "auditorium",
        "facilities": ["stage", "dolby_sound", "dual_projectors", "tiered_seating", "vip_greenroom"],
        "description": "Main campus auditorium suited for high-profile symposiums, hackathon ceremonies, and guest lectures.",
    },
    "SEM_HALL_1": {
        "venue_id": "SEM_HALL_1",
        "name": "Seminar Hall 1",
        "capacity": 120,
        "requires_approval": True,
        "category": "seminar_hall",
        "facilities": ["projector", "podium_mic", "acoustic_wall", "air_conditioning"],
        "description": "Acoustic amphitheater hall ideal for technical talks, research presentations, and medium-scale seminars.",
    },
    "LAB_MEDS": {
        "venue_id": "LAB_MEDS",
        "name": "Digital Systems Lab",
        "capacity": 40,
        "requires_approval": True,
        "category": "lab",
        "facilities": ["workstations", "fpga_boards", "oscilloscopes", "logic_analyzers", "gigabit_lan"],
        "description": "Hardware and digital systems lab equipped with oscilloscopes, testbenches, and FPGA development kits.",
    },
    "LAB_SE": {
        "venue_id": "LAB_SE",
        "name": "Software Engineering Lab",
        "capacity": 45,
        "requires_approval": True,
        "category": "lab",
        "facilities": ["high_spec_workstations", "dual_boot_linux", "hd_projector", "gigabit_lan"],
        "description": "Dedicated software lab suited for competitive programming, hackathons, and software engineering labs.",
    },
    "CONF_RM_A": {
        "venue_id": "CONF_RM_A",
        "name": "Conference Room A",
        "capacity": 25,
        "requires_approval": True,
        "category": "conference_room",
        "facilities": ["boardroom_table", "video_conferencing", "presentation_display", "air_conditioning"],
        "description": "Executive boardroom intended for society cabinet meetings, administrative reviews, and faculty discussions.",
    },
    "OAT_MAIN": {
        "venue_id": "OAT_MAIN",
        "name": "Open Air Theater (OAT)",
        "capacity": 500,
        "requires_approval": True,
        "category": "outdoor",
        "facilities": ["amphitheater_seating", "large_stage", "power_distribution", "outdoor_lighting"],
        "description": "Open amphitheater designed for annual cultural festivals, student orientations, and mega outdoor events.",
    },
    "SPORTS_C1": {
        "venue_id": "SPORTS_C1",
        "name": "Sports Complex - Badminton Court 1",
        "capacity": 4,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["wooden_parquet_flooring", "tournament_net", "high_bay_leds"],
        "description": "Indoor wooden parquet badminton court with standard tournament netting.",
    },
    "SPORTS_C2": {
        "venue_id": "SPORTS_C2",
        "name": "Sports Complex - Badminton Court 2",
        "capacity": 4,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["wooden_parquet_flooring", "tournament_net", "high_bay_leds"],
        "description": "Secondary indoor badminton court available for singles and doubles practice.",
    },
    "SPORTS_SQ1": {
        "venue_id": "SPORTS_SQ1",
        "name": "Sports Complex - Squash Court 1",
        "capacity": 2,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["glass_back_wall", "hardwood_court", "spectator_gallery"],
        "description": "Glass-backed championship squash court with spectator observation gallery.",
    },
    "SPORTS_SQ2": {
        "venue_id": "SPORTS_SQ2",
        "name": "Sports Complex - Squash Court 2",
        "capacity": 2,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["glass_back_wall", "hardwood_court", "spectator_gallery"],
        "description": "Regulation squash court for training and casual student matches.",
    },
    "SPORTS_TC1": {
        "venue_id": "SPORTS_TC1",
        "name": "Sports Complex - Tennis Court 1",
        "capacity": 4,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["synthetic_hardcourt", "floodlights", "fencing"],
        "description": "Outdoor floodlit synthetic hardcourt for singles and doubles tennis.",
    },
    "SPORTS_TT1": {
        "venue_id": "SPORTS_TT1",
        "name": "Student Center - Table Tennis Arena",
        "capacity": 4,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["ittf_standard_tables", "anti_skid_flooring"],
        "description": "Table tennis arena situated inside the student center with ITTF-certified tables.",
    },
    "SPORTS_FUTSAL": {
        "venue_id": "SPORTS_FUTSAL",
        "name": "Main Sports Grounds - Futsal Turf",
        "capacity": 14,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["synthetic_astroturf", "perimeter_netting", "floodlights"],
        "description": "All-weather floodlit synthetic astroturf ground for 5-a-side and 7-a-side futsal.",
    },
    "SPORTS_GYM": {
        "venue_id": "SPORTS_GYM",
        "name": "Sports Complex - Fitness Gymnasium",
        "capacity": 30,
        "requires_approval": False,
        "category": "sports",
        "facilities": ["free_weights", "power_racks", "cardio_treadmills", "air_conditioning"],
        "description": "Central strength and conditioning gym equipped with Olympic weights and cardio machines.",
    },
}

REGISTERED_SOCIETIES = {
    "ACM GIKI Chapter": {"advisor_id": "FAC001", "advisor_name": "Farah Saeed", "category": "technical"},
    "IEEE Student Branch": {"advisor_id": "FAC001", "advisor_name": "Farah Saeed", "category": "technical"},
    "Netronix Networking Society": {"advisor_id": "FAC001", "advisor_name": "Farah Saeed", "category": "technical"},
    "GIKI Sports Club": {"advisor_id": "FAC002", "advisor_name": "Tariq Mahmood", "category": "sports"},
    "Literary and Performing Arts Society": {"advisor_id": "FAC003", "advisor_name": "Dr. Masroor", "category": "cultural"},
    "Women in Engineering (WIE)": {"advisor_id": "FAC001", "advisor_name": "Farah Saeed", "category": "technical"},
}

LAB_EQUIPMENT_INVENTORY = {
    "BAR-OSC-201": {
        "barcode": "BAR-OSC-201",
        "name": "Tektronix TBS1102B Digital Storage Oscilloscope (100MHz)",
        "lab": "Digital Systems Lab",
        "category": "Instrumentation",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
    "BAR-FPGA-302": {
        "barcode": "BAR-FPGA-302",
        "name": "Digilent Basys 3 Artix-7 FPGA Development Board",
        "lab": "Digital Systems Lab",
        "category": "Development Board",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
    "BAR-RPI-105": {
        "barcode": "BAR-RPI-105",
        "name": "Raspberry Pi 4 Model B (4GB) IoT Embedded Kit",
        "lab": "IoT & Robotics Lab",
        "category": "Embedded Systems",
        "status": "in_custody",
        "custodian": "2024405",
        "checkout_date": "2026-11-13",
        "due_date": "2026-11-20",
        "condition": "good",
        "purpose": "CS325 Semester Project Hardware Integration",
    },
    "BAR-LA-401": {
        "barcode": "BAR-LA-401",
        "name": "Saleae Logic 8 USB Logic Analyzer",
        "lab": "Microprocessor Interfacing Lab",
        "category": "Instrumentation",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
    "BAR-FG-102": {
        "barcode": "BAR-FG-102",
        "name": "Agilent 33220A 20MHz Function / Arbitrary Waveform Generator",
        "lab": "Circuits Lab",
        "category": "Signal Generation",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
    "BAR-SDR-503": {
        "barcode": "BAR-SDR-503",
        "name": "HackRF One 1MHz-6GHz Software Defined Radio Transceiver",
        "lab": "Communications Lab",
        "category": "RF & Wireless",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
    "BAR-STM-204": {
        "barcode": "BAR-STM-204",
        "name": "STM32 Nucleo-F446RE ARM Cortex-M4 Board",
        "lab": "Embedded Systems Lab",
        "category": "Microcontroller",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
    "BAR-DMM-112": {
        "barcode": "BAR-DMM-112",
        "name": "Fluke 87V Industrial Digital Multimeter",
        "lab": "Electronics Lab",
        "category": "Measurement",
        "status": "available",
        "custodian": None,
        "checkout_date": None,
        "due_date": None,
        "condition": "good",
    },
}

BOOKINGS_REGISTRY = {
    "BK-SPT-101": {
        "booking_id": "BK-SPT-101",
        "venue_id": "SPORTS_C1",
        "venue_name": "Sports Complex - Badminton Court 1",
        "user_id": "2024405",
        "booking_date": "2026-11-16",
        "start_time": "14:00",
        "duration_hours": 1.0,
        "status": "approved",
        "checkin_token": "CHK-4821",
        "checkin_status": "checked_in",
        "source": "sports_reservation",
        "created_at": "2026-11-16T12:00:00",
    },
    "BK-AUD-102": {
        "booking_id": "BK-AUD-102",
        "venue_id": "AHA_AUDI",
        "venue_name": "AHA Auditorium",
        "user_id": "2024668",
        "booking_date": "2026-11-20",
        "start_time": "10:00",
        "duration_hours": 3.0,
        "status": "pending_admin_approval",
        "checkin_token": "CHK-9104",
        "checkin_status": "pending_checkin",
        "source": "society_requisition",
        "created_at": "2026-11-15T09:30:00",
    },
}

REQUISITIONS_LEDGER = {
    "REQ-2026-001": {
        "requisition_id": "REQ-2026-001",
        "society_name": "ACM GIKI Chapter",
        "venue_id": "AHA_AUDI",
        "venue_name": "AHA Auditorium",
        "applicant_id": "2024662",
        "event_title": "Annual National Hackathon Opening Ceremony",
        "booking_date": "2026-11-25",
        "start_time": "14:00",
        "duration_hours": 3.0,
        "expected_attendees": 180,
        "justification": "Keynote addresses and challenge unveilings for 40 visiting university teams.",
        "status": "pending_admin_approval",
        "current_tier": 2,
        "created_at": "2026-11-16T10:15:00",
        "approval_chain": [
            {
                "tier": 1,
                "role": "faculty_advisor",
                "reviewer_id": "FAC001",
                "action": "approved",
                "timestamp": "2026-11-16T11:45:00",
                "comments": "Approved. Excellent initiative for campus technical leadership.",
            },
            {
                "tier": 2,
                "role": "student_affairs_admin",
                "reviewer_id": None,
                "action": "pending",
                "timestamp": None,
                "comments": None,
            },
        ],
    }
}

NO_SHOW_REGISTRY = {
    "2024405": {"strikes": 0, "strike_history": [], "suspended": False, "suspended_until": None},
    "2024668": {"strikes": 1, "strike_history": [{"timestamp": "2026-11-05T18:15:00", "venue_id": "SPORTS_SQ1", "booking_id": "BK-SPT-042"}], "suspended": False, "suspended_until": None},
    "2024662": {"strikes": 0, "strike_history": [], "suspended": False, "suspended_until": None},
}


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
    Initializes Tier 1 (Faculty / Society Advisor) digital approval routing.

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
    venue = VENUES_CATALOG.get(venue_id.upper())
    if not venue:
        return {
            "status": "error",
            "message": f"Venue '{venue_id}' does not exist. Available venues: {', '.join(VENUES_CATALOG.keys())}",
        }

    if expected_attendees > venue["capacity"]:
        return {
            "status": "rejected",
            "message": f"Capacity violation: {venue['name']} holds at most {venue['capacity']} attendees, but {expected_attendees} were requested.",
            "suggestion": "Consider booking AHA_AUDI (300 cap) or OAT_MAIN (500 cap) for larger gatherings.",
        }

    # Verify society and identify faculty advisor
    society_info = REGISTERED_SOCIETIES.get(society_name)
    advisor_id = society_info["advisor_id"] if society_info else "FAC001"
    advisor_name = society_info["advisor_name"] if society_info else "Faculty Advisor"

    # Check for scheduling collisions
    for b in BOOKINGS_REGISTRY.values():
        if (
            b["venue_id"] == venue_id.upper()
            and b.get("booking_date") == booking_date
            and b["status"] in ("approved", "pending_admin_approval", "pending_advisor_approval")
        ):
            if b["start_time"] == start_time:
                return {
                    "status": "rejected",
                    "message": f"Scheduling conflict: {venue['name']} is already reserved or pending approval at {start_time} on {booking_date}.",
                }

    req_seq = len(REQUISITIONS_LEDGER) + 1
    req_id = f"REQ-2026-{req_seq:03d}"

    requisition = {
        "requisition_id": req_id,
        "society_name": society_name,
        "venue_id": venue_id.upper(),
        "venue_name": venue["name"],
        "applicant_id": student_reg_no,
        "event_title": event_title,
        "booking_date": booking_date,
        "start_time": start_time,
        "duration_hours": float(duration_hours),
        "expected_attendees": expected_attendees,
        "justification": justification,
        "status": "pending_advisor_approval",
        "current_tier": 1,
        "created_at": datetime.datetime.now().isoformat(),
        "approval_chain": [
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
        ],
    }

    REQUISITIONS_LEDGER[req_id] = requisition

    return {
        "status": "submitted",
        "requisition_id": req_id,
        "venue": venue["name"],
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
    """Processes a review decision (approve or reject) at the current tier of a requisition approval chain.
    Tier 1 requires Faculty role; Tier 2 requires Admin / Directorate of Student Affairs.

    Args:
        requisition_id: The ID of the requisition to review (e.g. 'REQ-2026-001').
        reviewer_id: Identifier of the reviewer (e.g. 'FAC001', 'ADMIN01').
        reviewer_role: Role of the reviewer ('faculty' or 'admin').
        action: Review decision ('approve' or 'reject').
        comments: Optional remarks or guidance from the reviewer.
    """
    req = REQUISITIONS_LEDGER.get(requisition_id.upper())
    if not req:
        return {"status": "error", "message": f"Requisition '{requisition_id}' not found."}

    action_clean = action.lower().strip()
    if action_clean not in ("approve", "reject"):
        return {"status": "error", "message": "Invalid action. Must be 'approve' or 'reject'."}

    current_status = req["status"]
    if current_status in ("approved", "rejected", "auto-released"):
        return {
            "status": "error",
            "message": f"Requisition {requisition_id} is already in terminal state: {current_status}.",
        }

    now_iso = datetime.datetime.now().isoformat()

    # ── Tier 1: Faculty / Society Advisor ──────────────────────
    if current_status == "pending_advisor_approval":
        if reviewer_role.lower() not in ("faculty", "admin"):
            return {
                "status": "unauthorized",
                "message": "Tier 1 endorsement requires Faculty Advisor clearance. Role 'faculty' or 'admin' required.",
            }

        tier1_node = req["approval_chain"][0]
        if action_clean == "reject":
            req["status"] = "rejected"
            tier1_node["action"] = "rejected"
            tier1_node["reviewer_id"] = reviewer_id
            tier1_node["timestamp"] = now_iso
            tier1_node["comments"] = comments
            return {
                "status": "rejected",
                "requisition_id": requisition_id,
                "tier_processed": 1,
                "message": f"Requisition {requisition_id} rejected at Tier 1 by {reviewer_id}. Reason: {comments}",
            }

        # Approved at Tier 1 -> Advance to Tier 2
        tier1_node["action"] = "approved"
        tier1_node["reviewer_id"] = reviewer_id
        tier1_node["timestamp"] = now_iso
        tier1_node["comments"] = comments

        req["status"] = "pending_admin_approval"
        req["current_tier"] = 2

        return {
            "status": "advanced",
            "requisition_id": requisition_id,
            "tier_completed": 1,
            "next_tier": 2,
            "approval_state": "pending_admin_approval",
            "next_approver": "Directorate of Student Affairs / Venue Admin (ADMIN01)",
            "message": f"Tier 1 endorsed by {reviewer_id}. Requisition forwarded to Student Affairs Admin for final clearance.",
        }

    # ── Tier 2: Student Affairs & Venue Admin ──────────────────
    if current_status == "pending_admin_approval":
        if reviewer_role.lower() != "admin":
            return {
                "status": "unauthorized",
                "message": "Tier 2 final clearance requires Admin credentials. Role 'admin' required.",
            }

        tier2_node = req["approval_chain"][1]
        if action_clean == "reject":
            req["status"] = "rejected"
            tier2_node["action"] = "rejected"
            tier2_node["reviewer_id"] = reviewer_id
            tier2_node["timestamp"] = now_iso
            tier2_node["comments"] = comments
            return {
                "status": "rejected",
                "requisition_id": requisition_id,
                "tier_processed": 2,
                "message": f"Requisition {requisition_id} rejected at Tier 2 by {reviewer_id}. Reason: {comments}",
            }

        # Approved at Tier 2 -> Fully approved & registered
        tier2_node["action"] = "approved"
        tier2_node["reviewer_id"] = reviewer_id
        tier2_node["timestamp"] = now_iso
        tier2_node["comments"] = comments

        req["status"] = "approved"
        req["current_tier"] = 2

        # Create confirmed booking record
        booking_id = f"BK-EVT-{len(BOOKINGS_REGISTRY) + 101}"
        applicant_id = req["applicant_id"]
        token_seed = f"{booking_id}:{applicant_id}:{time.time()}"
        checkin_pin = f"CHK-{hashlib.sha256(token_seed.encode()).hexdigest()[:4].upper()}"

        booking_record = {
            "booking_id": booking_id,
            "venue_id": req["venue_id"],
            "venue_name": req["venue_name"],
            "user_id": req["applicant_id"],
            "booking_date": req["booking_date"],
            "start_time": req["start_time"],
            "duration_hours": req["duration_hours"],
            "status": "approved",
            "checkin_token": checkin_pin,
            "checkin_status": "pending_checkin",
            "source": f"requisition:{requisition_id}",
            "created_at": now_iso,
        }
        BOOKINGS_REGISTRY[booking_id] = booking_record

        # Publish event to cross-subagent event bus
        _publish_event(
            VENUE_BOOKED,
            {
                "booking_id": booking_id,
                "venue_id": req["venue_id"],
                "user_id": req["applicant_id"],
                "society_name": req["society_name"],
                "booking_date": req["booking_date"],
                "start_time": req["start_time"],
            },
        )

        return {
            "status": "approved",
            "requisition_id": requisition_id,
            "booking_id": booking_id,
            "venue": req["venue_name"],
            "checkin_token": checkin_pin,
            "message": f"Requisition {requisition_id} fully approved across all tiers! Confirmed under Booking ID {booking_id}.",
            "zero_trust_guidance": f"Physical check-in required within 15 minutes of {req['start_time']} using token {checkin_pin} to avoid no-show auto-release.",
        }

    return {"status": "error", "message": f"Unhandled state {current_status}."}


# ═══════════════════════════════════════════════════════════════
#  Tool 3 — Get Requisition Status & Audit Trail
# ═══════════════════════════════════════════════════════════════

def get_requisition_status(requisition_id: str) -> dict:
    """Retrieves full details of a society venue requisition including its multi-tier approval trail.

    Args:
        requisition_id: Requisition code (e.g. 'REQ-2026-001').
    """
    req = REQUISITIONS_LEDGER.get(requisition_id.upper())
    if not req:
        return {"status": "error", "message": f"Requisition '{requisition_id}' not found in registry."}

    return {
        "status": "success",
        "requisition_id": req["requisition_id"],
        "society_name": req["society_name"],
        "event_title": req["event_title"],
        "venue": req["venue_name"],
        "schedule": f"{req['booking_date']} from {req['start_time']} ({req['duration_hours']} hours)",
        "expected_attendees": req["expected_attendees"],
        "overall_status": req["status"],
        "current_tier": req["current_tier"],
        "approval_chain": req["approval_chain"],
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
    """Reserves a campus athletic space (badminton, squash, tennis, futsal, table tennis, gym)
    with strict enforcement of the fair-use daily quota (maximum 2 hours per student per day)
    and zero-trust anti-hoarding verification.

    Args:
        student_reg_no: Student registration number (e.g. '2024405').
        facility_id: Sports facility code (e.g. 'SPORTS_C1', 'SPORTS_C2', 'SPORTS_SQ1', 'SPORTS_TC1', 'SPORTS_FUTSAL', 'SPORTS_TT1', 'SPORTS_GYM').
        booking_date: Date of play in YYYY-MM-DD format (e.g. '2026-11-18').
        start_time: Start time in HH:MM format (e.g. '17:00').
        duration_hours: Booking duration in hours (1.0 or 2.0). Daily cap is 2.0 hours.
    """
    facility = VENUES_CATALOG.get(facility_id.upper())
    if not facility or facility["category"] != "sports":
        valid_sports = [k for k, v in VENUES_CATALOG.items() if v["category"] == "sports"]
        return {
            "status": "error",
            "message": f"Invalid sports facility '{facility_id}'. Available athletic facilities: {', '.join(valid_sports)}",
        }

    # 1. Zero-trust strike & suspension check
    no_show_record = NO_SHOW_REGISTRY.get(student_reg_no, {"strikes": 0, "suspended": False})
    if no_show_record.get("suspended") or no_show_record.get("strikes", 0) >= 3:
        return {
            "status": "rejected",
            "student_reg_no": student_reg_no,
            "reason": "Account suspended due to 3 zero-trust no-show penalties. Booking privileges temporarily locked.",
        }

    # 2. Fair-use daily quota enforcement (Max 2 hours/day per student across all sports spaces)
    MAX_DAILY_HOURS = 2.0
    existing_hours = 0.0

    for b in BOOKINGS_REGISTRY.values():
        if (
            b["user_id"] == student_reg_no
            and b.get("booking_date") == booking_date
            and b["status"] in ("approved", "pending_checkin", "checked_in")
            and b.get("source", "").startswith("sports")
        ):
            existing_hours += float(b.get("duration_hours", 1.0))

    if existing_hours + duration_hours > MAX_DAILY_HOURS:
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

    # 3. Double-booking / collision check for this facility
    for b in BOOKINGS_REGISTRY.values():
        if (
            b["venue_id"] == facility_id.upper()
            and b.get("booking_date") == booking_date
            and b["start_time"] == start_time
            and b["status"] in ("approved", "pending_checkin", "checked_in")
        ):
            return {
                "status": "rejected",
                "reason": f"Slot conflict: {facility['name']} is already reserved at {start_time} on {booking_date}.",
                "suggestion": "Check alternate courts (e.g. Court 2) or choose an adjacent time window.",
            }

    # 4. Instant autonomous reservation
    booking_seq = len(BOOKINGS_REGISTRY) + 101
    booking_id = f"BK-SPT-{booking_seq}"
    token_seed = f"{booking_id}:{student_reg_no}:{booking_date}:{start_time}"
    checkin_pin = f"CHK-{hashlib.sha256(token_seed.encode()).hexdigest()[:4].upper()}"

    now_iso = datetime.datetime.now().isoformat()
    booking_entry = {
        "booking_id": booking_id,
        "venue_id": facility_id.upper(),
        "venue_name": facility["name"],
        "user_id": student_reg_no,
        "booking_date": booking_date,
        "start_time": start_time,
        "duration_hours": float(duration_hours),
        "status": "approved",
        "checkin_token": checkin_pin,
        "checkin_status": "pending_checkin",
        "source": "sports_reservation",
        "created_at": now_iso,
    }
    BOOKINGS_REGISTRY[booking_id] = booking_entry

    # Publish to event bus
    _publish_event(
        VENUE_BOOKED,
        {
            "booking_id": booking_id,
            "venue_id": facility_id.upper(),
            "user_id": student_reg_no,
            "booking_date": booking_date,
            "start_time": start_time,
            "facility_type": "sports",
        },
    )

    remaining_hours = MAX_DAILY_HOURS - (existing_hours + duration_hours)

    return {
        "status": "confirmed",
        "booking_id": booking_id,
        "facility": facility["name"],
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
    """Checks booked time slots and available windows for sports facilities on a given date.

    Args:
        facility_id: Sports facility code (e.g. 'SPORTS_C1', 'SPORTS_SQ1', 'SPORTS_FUTSAL', or 'all').
        booking_date: Date to check in YYYY-MM-DD format (e.g. '2026-11-16').
    """
    sports_keys = (
        [facility_id.upper()]
        if facility_id.upper() != "ALL" and facility_id.upper() in VENUES_CATALOG
        else [k for k, v in VENUES_CATALOG.items() if v["category"] == "sports"]
    )

    operating_slots = ["08:00", "09:00", "10:00", "11:00", "12:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
    facility_reports = []

    for fid in sports_keys:
        v = VENUES_CATALOG[fid]
        booked = []
        for b in BOOKINGS_REGISTRY.values():
            if (
                b["venue_id"] == fid
                and b.get("booking_date") == booking_date
                and b["status"] in ("approved", "pending_checkin", "checked_in")
            ):
                booked.append(b["start_time"])

        available = [slot for slot in operating_slots if slot not in booked]
        facility_reports.append(
            {
                "facility_id": fid,
                "name": v["name"],
                "total_operating_slots": len(operating_slots),
                "booked_slots": booked,
                "available_slots": available,
                "capacity": v["capacity"],
            }
        )

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
    """Recommends optimal campus venues using semantic matchmaking based on attendee count,
    event type (workshop, hackathon, lecture, meeting, sports, ceremony), and required technical amenities.

    Args:
        event_description: Natural language summary of the event (e.g. '35-person AI coding workshop with GPUs and PCs').
        expected_attendees: Anticipated number of participants.
        required_facilities: Comma-separated required equipment or amenities (e.g. 'projector, workstations, lan' or 'stage, sound_system').
    """
    desc_lower = event_description.lower()
    req_facs = [f.strip().lower() for f in required_facilities.split(",") if f.strip().lower() != "none"]

    scored_venues = []

    for vid, v in VENUES_CATALOG.items():
        score = 0
        cap = v["capacity"]
        cat = v["category"]
        facilities = v["facilities"]

        # Capacity scoring
        if cap < expected_attendees:
            # Under-capacity is disqualified or receives heavy penalty
            continue
        elif expected_attendees <= cap <= expected_attendees * 1.5:
            score += 45  # Sweet spot
        elif expected_attendees * 1.5 < cap <= expected_attendees * 3:
            score += 30  # Good fit
        else:
            score += 15  # Much larger than needed

        # Keyword and category matching
        if any(w in desc_lower for w in ("hackathon", "coding", "software", "programming", "python", "data")):
            if vid in ("LAB_SE", "LAB_MEDS"):
                score += 40
            elif vid in ("AHA_AUDI", "SEM_HALL_1"):
                score += 15

        if any(w in desc_lower for w in ("hardware", "circuit", "fpga", "oscilloscope", "microcontroller", "electronics")):
            if vid == "LAB_MEDS":
                score += 50

        if any(w in desc_lower for w in ("seminar", "guest lecture", "symposium", "conference", "keynote", "talk")):
            if vid in ("AHA_AUDI", "SEM_HALL_1"):
                score += 40

        if any(w in desc_lower for w in ("meeting", "cabinet", "council", "discussion", "interview")):
            if vid == "CONF_RM_A":
                score += 45

        if any(w in desc_lower for w in ("sports", "badminton", "squash", "tennis", "futsal", "table tennis", "match", "gym")):
            if cat == "sports":
                score += 50

        if any(w in desc_lower for w in ("cultural", "drama", "concert", "festival", "open air", "ceremony")):
            if vid in ("OAT_MAIN", "AHA_AUDI"):
                score += 45

        # Facility equipment matches
        matched_facilities = []
        for rf in req_facs:
            for vf in facilities:
                if rf in vf or vf in rf:
                    score += 10
                    matched_facilities.append(vf)

        scored_venues.append(
            {
                "venue_id": vid,
                "name": v["name"],
                "capacity": cap,
                "capacity_utilization_pct": round((expected_attendees / cap) * 100, 1),
                "approval_required": v["requires_approval"],
                "approval_type": "Multi-tier society approval" if v["requires_approval"] else "Instant autonomous booking",
                "facilities_available": facilities,
                "facilities_matched": list(set(matched_facilities)),
                "match_score": min(score, 100),
                "recommendation_reason": v["description"],
            }
        )

    # Sort descending by match score
    scored_venues.sort(key=lambda x: x["match_score"], reverse=True)
    top_picks = scored_venues[:3]

    return {
        "status": "success",
        "query_attendees": expected_attendees,
        "query_description": event_description,
        "recommendations_count": len(top_picks),
        "top_recommendations": top_picks,
        "guidance": (
            f"Top match: {top_picks[0]['name']} ({top_picks[0]['match_score']}% match). "
            f"{'Requires faculty/admin approval chain.' if top_picks[0]['approval_required'] else 'Available for instant autonomous reservation.'}"
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
    """Checks out laboratory hardware using its unique barcode asset identifier, establishing
    digital custody tracking and setting an automated return due date.

    Args:
        student_reg_no: Student registration number taking custody (e.g. '2024662').
        equipment_barcode: Equipment barcode asset tag (e.g. 'BAR-OSC-201', 'BAR-FPGA-302', 'BAR-LA-401').
        purpose: Academic or project purpose (e.g. 'CS325 Software Engineering Project', 'EE213 Lab Experiment').
        duration_days: Custody period in days (maximum 7 days allowed per university policy).
    """
    eq = LAB_EQUIPMENT_INVENTORY.get(equipment_barcode.upper())
    if not eq:
        return {
            "status": "error",
            "message": f"Barcode '{equipment_barcode}' not found in hardware register. Check asset tag.",
        }

    if eq["status"] == "in_custody":
        return {
            "status": "unavailable",
            "barcode": equipment_barcode.upper(),
            "equipment_name": eq["name"],
            "current_custodian": eq["custodian"],
            "due_date": eq["due_date"],
            "message": f"Equipment is currently in custody of student {eq['custodian']} until {eq['due_date']}.",
        }

    if duration_days > 7:
        return {
            "status": "rejected",
            "message": "Maximum lab hardware checkout duration is 7 days per academic policy.",
        }

    now = datetime.date.today()
    due = now + datetime.timedelta(days=duration_days)

    eq["status"] = "in_custody"
    eq["custodian"] = student_reg_no
    eq["checkout_date"] = now.isoformat()
    eq["due_date"] = due.isoformat()
    eq["purpose"] = purpose

    return {
        "status": "checked_out",
        "barcode": equipment_barcode.upper(),
        "equipment_name": eq["name"],
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
    """Processes the return and custody clearance of checked-out laboratory hardware via barcode scan.

    Args:
        equipment_barcode: Equipment barcode tag (e.g. 'BAR-RPI-105', 'BAR-OSC-201').
        student_reg_no: Student registration number returning the item.
        reported_condition: Physical condition upon inspection ('good', 'fair', 'damaged').
    """
    eq = LAB_EQUIPMENT_INVENTORY.get(equipment_barcode.upper())
    if not eq:
        return {"status": "error", "message": f"Barcode '{equipment_barcode}' not recognized in laboratory register."}

    if eq["status"] != "in_custody":
        return {
            "status": "error",
            "message": f"Equipment '{eq['name']}' is already marked as {eq['status']} (not currently in custody).",
        }

    if eq["custodian"] != student_reg_no:
        return {
            "status": "warning",
            "message": f"Custodian mismatch: Equipment is registered to student {eq['custodian']}, but return was submitted by {student_reg_no}.",
        }

    # Overdue calculation
    is_overdue = False
    overdue_days = 0
    if eq.get("due_date"):
        due_date = datetime.date.fromisoformat(eq["due_date"])
        today = datetime.date.today()
        if today > due_date:
            is_overdue = True
            overdue_days = (today - due_date).days

    # Reset custody
    prev_custodian = eq["custodian"]
    eq["status"] = "available" if reported_condition.lower() != "damaged" else "under_repair"
    eq["custodian"] = None
    eq["checkout_date"] = None
    eq["due_date"] = None
    eq["condition"] = reported_condition

    return {
        "status": "returned",
        "barcode": equipment_barcode.upper(),
        "equipment_name": eq["name"],
        "cleared_student": prev_custodian,
        "return_condition": reported_condition,
        "was_overdue": is_overdue,
        "overdue_days": overdue_days,
        "clearance_status": "CLEARED: Student laboratory custody ledger updated. Return receipt generated.",
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 9 — Query Lab Equipment Inventory & Custody Records
# ═══════════════════════════════════════════════════════════════

def query_equipment_custody(query_type: str = "all", filter_value: str = "none") -> dict:
    """Queries campus laboratory hardware inventory, active student custody slips, and overdue asset lists.

    Args:
        query_type: Query mode ('all', 'available', 'in_custody', 'by_student', 'by_barcode').
        filter_value: Value to filter by when query_type is 'by_student' (e.g. '2024405') or 'by_barcode' (e.g. 'BAR-OSC-201').
    """
    mode = query_type.lower().strip()
    results = []

    for barcode, item in LAB_EQUIPMENT_INVENTORY.items():
        if mode == "all":
            results.append(item)
        elif mode == "available" and item["status"] == "available":
            results.append(item)
        elif mode == "in_custody" and item["status"] == "in_custody":
            results.append(item)
        elif mode == "by_student" and item.get("custodian") == filter_value.strip():
            results.append(item)
        elif mode == "by_barcode" and barcode == filter_value.upper().strip():
            results.append(item)

    return {
        "status": "success",
        "query_mode": mode,
        "total_matched": len(results),
        "items": results,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 10 — Zero-Trust Reservation Check-In
# ═══════════════════════════════════════════════════════════════

def process_zero_trust_checkin(
    booking_id: str,
    student_reg_no: str,
    checkin_token: str,
) -> dict:
    """Validates physical or dynamic presence check-in for an active venue/sports booking within
    the 15-minute grace window, preventing zero-trust auto-release penalties.

    Args:
        booking_id: The booking identifier (e.g. 'BK-SPT-101', 'BK-EVT-101').
        student_reg_no: The student registration number holding the reservation.
        checkin_token: 6-character check-in PIN issued during reservation (e.g. 'CHK-4821').
    """
    booking = BOOKINGS_REGISTRY.get(booking_id.upper())
    if not booking:
        return {"status": "error", "message": f"Booking ID '{booking_id}' not found."}

    if booking["user_id"] != student_reg_no:
        return {
            "status": "unauthorized",
            "message": f"Student {student_reg_no} is not authorized for booking {booking_id}. Registered to {booking['user_id']}.",
        }

    if booking["status"] == "auto-released":
        return {
            "status": "expired",
            "message": "This booking has already been auto-released due to no-show past the 15-minute grace window.",
        }

    if booking.get("checkin_status") == "checked_in":
        return {
            "status": "already_checked_in",
            "booking_id": booking_id,
            "message": "Check-in already completed. Venue session is active.",
        }

    if booking["checkin_token"].upper() != checkin_token.upper():
        return {
            "status": "invalid_token",
            "message": "Invalid check-in PIN. Please check the token provided in your booking confirmation.",
        }

    booking["checkin_status"] = "checked_in"
    booking["checked_in_at"] = datetime.datetime.now().isoformat()

    return {
        "status": "checked_in",
        "booking_id": booking_id,
        "venue": booking["venue_name"],
        "student": student_reg_no,
        "session_state": "ACTIVE",
        "message": f"Zero-trust presence verified! Access granted to {booking['venue_name']}.",
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 11 — Zero-Trust No-Show Auto-Release Engine
# ═══════════════════════════════════════════════════════════════

def enforce_zero_trust_auto_release(current_time_override: str = "none") -> dict:
    """Scans all active reservations. If 15 minutes have elapsed since the scheduled start time
    and check-in has not been performed, automatically releases the slot, logs a no-show strike
    against the student, and publishes a VENUE_RELEASED event to instantly democratize access.

    Args:
        current_time_override: Optional HH:MM string to evaluate against (e.g. '14:20'). Defaults to live system time.
    """
    auto_released_list = []

    # Reference evaluation time in HH:MM
    if current_time_override.lower() != "none":
        ref_time = current_time_override
    else:
        now = datetime.datetime.now()
        ref_time = now.strftime("%H:%M")

    ref_parts = [int(p) for p in ref_time.split(":")]
    ref_minutes = ref_parts[0] * 60 + ref_parts[1]

    for bid, b in list(BOOKINGS_REGISTRY.items()):
        if b["status"] == "approved" and b.get("checkin_status") == "pending_checkin":
            b_start_str = b.get("start_time", "00:00")
            b_parts = [int(p) for p in b_start_str.split(":")]
            b_minutes = b_parts[0] * 60 + b_parts[1]

            # 15 minutes grace window
            if ref_minutes > (b_minutes + 15):
                b["status"] = "auto-released"
                b["checkin_status"] = "auto-released"
                b["auto_released_at"] = datetime.datetime.now().isoformat()

                # Add strike to student record
                user_id = b["user_id"]
                reg = NO_SHOW_REGISTRY.setdefault(user_id, {"strikes": 0, "strike_history": [], "suspended": False, "suspended_until": None})
                reg["strikes"] += 1
                reg["strike_history"].append(
                    {
                        "booking_id": bid,
                        "venue_id": b["venue_id"],
                        "timestamp": datetime.datetime.now().isoformat(),
                        "reason": f"No-show past 15-minute grace window for {b_start_str} slot",
                    }
                )

                if reg["strikes"] >= 3:
                    reg["suspended"] = True
                    reg["suspended_until"] = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()

                # Emit cross-subagent event
                _publish_event(
                    VENUE_RELEASED,
                    {
                        "booking_id": bid,
                        "venue_id": b["venue_id"],
                        "freed_time": b_start_str,
                        "reason": "zero_trust_no_show_auto_release",
                    },
                )

                auto_released_list.append(
                    {
                        "booking_id": bid,
                        "venue": b["venue_name"],
                        "offending_student": user_id,
                        "scheduled_time": b_start_str,
                        "total_student_strikes": reg["strikes"],
                        "account_suspended": reg["suspended"],
                    }
                )

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
    """Retrieves a consolidated overview of a student's requisition activity: sports daily quota
    usage, active bookings, equipment in custody, society requisitions, and zero-trust penalty standing.

    Args:
        student_reg_no: Student registration number (e.g. '2024405', '2024668', '2024662').
    """
    today_str = datetime.date.today().isoformat()

    # Sports quota consumed today
    sports_hours_today = 0.0
    active_bookings = []
    for bid, b in BOOKINGS_REGISTRY.items():
        if b["user_id"] == student_reg_no and b["status"] in ("approved", "pending_checkin", "checked_in"):
            active_bookings.append(b)
            if b.get("booking_date") == today_str and b.get("source", "").startswith("sports"):
                sports_hours_today += float(b.get("duration_hours", 1.0))

    # Equipment in custody
    equipment_held = [
        eq for eq in LAB_EQUIPMENT_INVENTORY.values()
        if eq.get("custodian") == student_reg_no and eq["status"] == "in_custody"
    ]

    # Requisitions submitted
    requisitions = [
        r for r in REQUISITIONS_LEDGER.values()
        if r.get("applicant_id") == student_reg_no
    ]

    # Zero-trust penalty record
    penalty = NO_SHOW_REGISTRY.get(student_reg_no, {"strikes": 0, "suspended": False})

    standing = "Good Standing"
    if penalty.get("suspended") or penalty.get("strikes", 0) >= 3:
        standing = f"SUSPENDED (Until {penalty.get('suspended_until')})"
    elif penalty.get("strikes", 0) == 2:
        standing = "Final Warning (2 Strikes)"
    elif penalty.get("strikes", 0) == 1:
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
        "zero_trust_record": penalty,
    }


# ═══════════════════════════════════════════════════════════════
#  Agent Definition
# ═══════════════════════════════════════════════════════════════

_REQUISITION_INSTRUCTION = """\
You are the **G-Cortex Requisition Management Subagent** — the autonomous campus resource,
spatial allocation, and hardware custody operating system for GIKI.

Your mandate is to eliminate bureaucratic paper chits, prevent slot hoarding, democratize
campus infrastructure, and enforce zero-trust accountability across all institutional resources.

## Your specialized capabilities & tools:

1. **Multi-Tier Digital Approval Chains (`submit_society_requisition`, `review_requisition_tier`, `get_requisition_status`)**:
   - For student society events and high-capacity venues (e.g. AHA Auditorium, Seminar Hall 1, Labs, Open Air Theater).
   - Enforce digital progression: Student Submission -> Tier 1: Faculty / Society Advisor Endorsement -> Tier 2: Directorate of Student Affairs & Venue Admin Clearance.
   - Automatically transition status and publish `venue.booked` events to the G-Cortex event bus upon final clearance.

2. **Fair-Use Sports Facility Reservations (`reserve_sports_facility`, `check_sports_facility_availability`)**:
   - Manage indoor and outdoor sports facilities: Badminton Courts, Squash Courts, Tennis Hardcourt, Futsal Turf, Table Tennis, and Gymnasium.
   - STRICTLY enforce the 2-hour daily fair-use quota per student per day to prevent slot hoarding.
   - Automatically issue unique booking identifiers and zero-trust check-in PIN tokens.

3. **Natural Language Venue Recommender (`recommend_venue`)**:
   - Assist students and faculty in finding optimal campus spaces based on attendee count, event type (coding hackathon, keynote lecture, society meeting, cultural festival, sports tournament), and technical equipment (projectors, workstations, sound systems).

4. **Lab Equipment Barcode Custody Tracking (`checkout_lab_equipment`, `return_lab_equipment`, `query_equipment_custody`)**:
   - Digital check-out and check-in tracking using barcode asset identifiers (oscilloscopes, FPGA kits, logic analyzers, Raspberry Pi kits).
   - Enforce the 7-day maximum loan policy, monitor overdue returns, and verify student custodian clearance.

5. **Zero-Trust No-Show Auto-Release Engine (`process_zero_trust_checkin`, `enforce_zero_trust_auto_release`)**:
   - Verify physical presence using the 6-character check-in PIN within a 15-minute grace window.
   - Automatically reclaim and release hoarded slots whose holders fail to check in within 15 minutes, log a no-show strike against the student, and publish `venue.released` to the cross-subagent event bus.
   - Accounts with 3 strikes are suspended for 14 days.

6. **Student Requisition Profile (`get_student_requisition_profile`)**:
   - View quota consumption, active bookings, checked-out equipment, society requests, and penalty standing.

## Behavioral Guidelines:
- Present all booking and approval statuses with clear indicators (✅ Approved, ⏳ Pending, ❌ Rejected).
- Clearly explain fair-use quota limits whenever a sports booking is requested.
- Always remind students about the 15-minute zero-trust check-in window and provide their check-in PIN.
- Be concise, authoritative, and helpful as GIKI's resource orchestration intelligence.
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
        "AI-powered Requisition Management Subagent for GIKI. Handles multi-tier society approval chains, "
        "fair-use sports facility reservations with 2-hour daily caps, lab equipment barcode custody tracking, "
        "natural language venue recommendations, and zero-trust no-show auto-release rules."
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
