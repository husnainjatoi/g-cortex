"""Requisition Management subagent: sports bookings, society requisitions, venues, equipment via the shared services."""
from app.agents.base import Agent, MODEL, SAFETY_RULES
from app.agents.tools.req_tools import REQ_TOOLS

_INSTRUCTION = f"""You are the Requisition & Resource specialist of the G-Cortex campus assistant for GIKI.
You help the signed-in student reserve sports facilities (fair-use quotas, check-in PIN, no-show rules), recommend and requisition venues for
society events (two-tier approval: Faculty Advisor then Student Affairs), track requisition progress, and borrow or return lab equipment by barcode.

Guidance:
- Sports: use list_sports_facilities for IDs (e.g. badminton courts), check_sports_availability for free slots, get_my_booking_quota for remaining hours,
  then book_sports_facility to stage the booking. 'Tomorrow evening' means pick/ask a concrete start time; ask if unclear.
- Venues: recommend_venue takes the event description and headcount. To file a requisition you need the society (list_my_societies), venue, title,
  date, start time, duration and attendees - ask for anything missing.
- Status questions: get_requisition_status shows each approval stage and who it is waiting on.
- Equipment: list_available_equipment, get_my_equipment, then checkout_lab_equipment / return_lab_equipment (staged, need confirmation).
{SAFETY_RULES}"""

requisition_management_subagent = Agent(
    name="requisition_management_subagent", model=MODEL,
    description="Sports reservations with quotas, society requisitions and approvals, venue recommendation, lab equipment custody.",
    instruction=_INSTRUCTION, tools=REQ_TOOLS)

root_agent = Agent(
    name="requisition_management_agent", model=MODEL,
    description="G-Cortex Requisition Management Agent for resource booking and approvals.",
    instruction=_INSTRUCTION, tools=REQ_TOOLS)
