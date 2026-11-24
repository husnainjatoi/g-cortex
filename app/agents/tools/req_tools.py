"""Requisition & Resource tools - thin adapters over the requisition, sports and equipment services."""
from app.agents.runtime import context, propose, tool
from app.services import clock, equipment, requisitions, sports


@tool
def get_today() -> dict:
    """Today's date and weekday and the current time. Use it to resolve 'tomorrow', 'this evening', 'next Friday'."""
    n = clock.now()
    return {"ok": True, "today": n.date().isoformat(), "weekday": n.strftime("%A"), "time": n.strftime("%H:%M")}


@tool
def list_sports_facilities() -> dict:
    """Bookable sports facilities (courts, gym, ...) with their IDs."""
    return {"ok": True, "facilities": sports.facilities(context().actor)}


@tool
def check_sports_availability(facility_id: str, date: str) -> dict:
    """Hourly availability for a sports facility on a date.

    Args:
        facility_id: e.g. 'SPORTS_C1' (badminton court 1). Use list_sports_facilities for IDs.
        date: YYYY-MM-DD.
    """
    return {"ok": True, **sports.availability(context().actor, facility_id, date)}


@tool
def get_my_booking_quota(date: str = "") -> dict:
    """Your remaining fair-use quota (daily/weekly hours) and no-show strike status.

    Args:
        date: Optional YYYY-MM-DD (default today).
    """
    return {"ok": True, **sports.quota(context().actor, None, date or None)}


@tool
def book_sports_facility(facility_id: str, date: str, start_time: str, duration_hours: float = 1.0) -> dict:
    """Request a sports reservation. Validates quota, opening hours, suspensions and slot availability, then asks the
    user to confirm before booking.

    Args:
        facility_id: e.g. 'SPORTS_C1'.
        date: YYYY-MM-DD.
        start_time: HH:MM 24-hour (on the hour or half hour).
        duration_hours: 0.5 step, default 1.
    """
    res = sports.book(context().actor, facility_id, date, start_time, duration_hours, dry_run=True)
    return propose("book_sports_facility", {"facility_id": facility_id.upper(), "date": date, "start_time": start_time, "duration_hours": float(duration_hours)},
                   f"Book {res['facility']} on {res['date']} {res['start_time']}-{res['end_time']} ({res['duration_hours']:g}h). "
                   f"Daily quota left afterwards: {res['daily_remaining_hours_after']:g}h.")


@tool
def get_my_bookings(upcoming_only: bool = True) -> dict:
    """Your sports and venue bookings.

    Args:
        upcoming_only: Only future active bookings when true.
    """
    rows = sports.my_bookings(context().actor, None, upcoming_only)
    return {"ok": True, "bookings": [{k: r[k] for k in ("booking_code", "venue_name", "start_ts", "end_ts", "status", "can_cancel")} for r in rows]}


@tool
def cancel_sports_booking(booking_code: str) -> dict:
    """Cancel one of your bookings (asks the user to confirm first; cutoff rules still apply).

    Args:
        booking_code: e.g. 'BK-SPT-1003'.
    """
    mine = {b["booking_code"]: b for b in sports.my_bookings(context().actor)}
    b = mine.get((booking_code or "").upper())
    if not b:
        return {"ok": False, "error": f"You have no booking {booking_code}."}
    return propose("cancel_sports_booking", {"booking_ref": b["booking_code"]}, f"Cancel your booking {b['booking_code']} at {b['venue_name']} ({b['start_ts']:%Y-%m-%d %H:%M}).")


@tool
def recommend_venue(event_description: str, expected_attendees: int, required_facilities: str = "") -> dict:
    """Natural-language venue recommendation for an event.

    Args:
        event_description: What the event is (e.g. 'society hackathon with demos').
        expected_attendees: Headcount.
        required_facilities: Optional comma-separated needs (projector, stage, microphone).
    """
    return {"ok": True, **requisitions.recommend_venue(context().actor, event_description, expected_attendees, required_facilities)}


@tool
def list_my_societies() -> dict:
    """Societies you belong to (only these can be used for a requisition)."""
    return {"ok": True, "societies": requisitions.my_societies(context().actor)}


@tool
def submit_society_requisition(society_name: str, venue_id: str, event_title: str, booking_date: str, start_time: str,
                               duration_hours: float, expected_attendees: int, justification: str = "") -> dict:
    """Submit a society event requisition into the two-tier approval chain (Faculty Advisor -> Student Affairs). Validates
    membership, capacity, notice and venue conflicts, then asks the user to confirm.

    Args:
        society_name: Exact society name from list_my_societies.
        venue_id: e.g. 'SEM_HALL_1'.
        event_title: Event name.
        booking_date: YYYY-MM-DD.
        start_time: HH:MM.
        duration_hours: Hours.
        expected_attendees: Headcount.
        justification: Short purpose statement.
    """
    args = dict(society_name=society_name, venue_id=venue_id, event_title=event_title, booking_date=booking_date, start_time=start_time,
                duration_hours=float(duration_hours), expected_attendees=int(expected_attendees), justification=justification)
    res = requisitions.submit_requisition(context().actor, **args, dry_run=True)
    return propose("submit_society_requisition", args,
                   f"Submit requisition for '{event_title}' ({res['society']}) at {res['venue']} on {res['date']} {res['start_time']}-{res['end_time']} "
                   f"for {res['attendees']} people. First approver: {res['first_approver']}.")


@tool
def get_requisition_status(requisition_id: str = "") -> dict:
    """Status, approval stages and pending authority of one requisition, or all your requisitions if no ID is given.

    Args:
        requisition_id: Optional, e.g. 'REQ-2026-1001'.
    """
    a = context().actor
    if requisition_id:
        return {"ok": True, "requisition": requisitions.get_requisition(a, requisition_id)}
    rows = requisitions.list_requisitions(a, "mine")
    return {"ok": True, "requisitions": [{k: r[k] for k in ("requisition_id", "event_title", "society_name", "venue_name", "booking_date", "status", "pending_authority")} for r in rows]}


@tool
def list_available_equipment(search: str = "") -> dict:
    """Lab equipment inventory with barcodes and availability.

    Args:
        search: Optional text (name, barcode, category).
    """
    rows = equipment.list_equipment(context().actor, q=search or None)
    return {"ok": True, "equipment": [{k: r[k] for k in ("barcode", "name", "lab", "status", "condition", "due_date", "overdue") if k in r} for r in rows]}


@tool
def get_my_equipment() -> dict:
    """Equipment currently in your custody with due dates."""
    return {"ok": True, "equipment": equipment.my_custody(context().actor)}


@tool
def checkout_lab_equipment(barcode: str, purpose: str, days: int = 7) -> dict:
    """Request to borrow equipment by barcode (asks the user to confirm; loan limits and overdue rules apply).

    Args:
        barcode: e.g. 'BAR-FPGA-302'.
        purpose: Why it is needed.
        days: Loan length in days.
    """
    res = equipment.checkout(context().actor, barcode, purpose, days, dry_run=True)
    return propose("checkout_lab_equipment", {"barcode": res["barcode"], "purpose": purpose, "days": int(days)},
                   f"Borrow {res['name']} ({res['barcode']}, {res['lab']}) until {res['due_date']} for: {purpose}.")


@tool
def return_lab_equipment(barcode: str, condition: str = "good") -> dict:
    """Return equipment you hold (asks the user to confirm).

    Args:
        barcode: Equipment barcode.
        condition: good, fair or damaged.
    """
    mine = {e["barcode"]: e for e in equipment.my_custody(context().actor)}
    e = mine.get((barcode or "").upper())
    if not e:
        return {"ok": False, "error": f"You do not hold {barcode}."}
    return propose("return_lab_equipment", {"barcode": e["barcode"], "condition": condition}, f"Return {e['name']} ({e['barcode']}) in {condition} condition.")


REQ_TOOLS = [get_today, list_sports_facilities, check_sports_availability, get_my_booking_quota, book_sports_facility, get_my_bookings,
             cancel_sports_booking, recommend_venue, list_my_societies, submit_society_requisition, get_requisition_status,
             list_available_equipment, get_my_equipment, checkout_lab_equipment, return_lab_equipment]
