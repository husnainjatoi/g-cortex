"""Mess Management tools - thin adapters over app.services.mess."""
from app.agents.runtime import context, propose, tool
from app.services import clock, mess


@tool
def get_menu_and_macros(meal: str = "") -> dict:
    """Menus with calories/protein/carbs/fat and the allergen shield evaluated against YOUR declared allergies.

    Args:
        meal: Optional 'breakfast', 'lunch' or 'dinner'. Empty returns all meals.
    """
    data = mess.menu(context().actor)
    if meal:
        data["meals"] = [m for m in data["meals"] if m["meal_period"] == meal.lower().strip()]
    return {"ok": True, **data}


@tool
def preview_mess_out(start_date: str, end_date: str) -> dict:
    """Check a mess-out period against the rules (notice period, minimum days, overlaps) and compute the rebate.
    Changes nothing. Dates are YYYY-MM-DD; today is provided by get_today.

    Args:
        start_date: First day away, YYYY-MM-DD.
        end_date: Last day away, YYYY-MM-DD.
    """
    return {"ok": True, **mess.preview_mess_out(context().actor, start_date, end_date)}


@tool
def submit_mess_out_request(start_date: str, end_date: str, reason: str = "") -> dict:
    """Request a mess-out for the dates. Validates the rules, then asks the user to confirm; the request is only
    created after confirmation.

    Args:
        start_date: First day away, YYYY-MM-DD.
        end_date: Last day away, YYYY-MM-DD.
        reason: Optional short reason.
    """
    ev = mess.preview_mess_out(context().actor, start_date, end_date)
    if not ev["eligible"]:
        return {"ok": False, "refused_by_backend": True, "error": " ".join(ev["problems"]), "details": ev}
    return propose("submit_mess_out_request", {"start_date": ev["start_date"], "end_date": ev["end_date"], "reason": reason or None},
                   f"Submit a mess-out for {ev['start_date']} to {ev['end_date']} ({ev['days']} days). Rebate PKR {ev['rebate_pkr']:.0f}; "
                   f"{ev['outcome_if_submitted']}.")


@tool
def get_my_mess_out_history() -> dict:
    """Your previous, ongoing, upcoming and pending mess-out requests with rebate amounts."""
    return {"ok": True, "requests": mess.list_mess_out(context().actor)}


@tool
def cancel_mess_out_request(request_id: int) -> dict:
    """Cancel one of your mess-out requests that has not started yet (asks the user to confirm first).

    Args:
        request_id: The mess-out request number.
    """
    mine = {r["request_id"]: r for r in mess.list_mess_out(context().actor)}
    r = mine.get(int(request_id))
    if not r:
        return {"ok": False, "error": f"You have no mess-out request #{request_id}."}
    return propose("cancel_mess_out_request", {"request_id": int(request_id)}, f"Cancel mess-out #{request_id} ({r['start_date']} to {r['end_date']}).")


@tool
def get_my_rebate_ledger() -> dict:
    """Your rebate transactions and the total rebate credited."""
    return {"ok": True, **mess.rebate_ledger(context().actor)}


@tool
def get_my_meal_records(days: int = 14) -> dict:
    """Your recent meal records (consumed / rebated) with daily nutritional totals.

    Args:
        days: How many past days to include (1-180).
    """
    return {"ok": True, **mess.meal_records(context().actor, None, days)}


@tool
def get_my_mess_profile() -> dict:
    """Allergies, gate-passes, active mess-outs and rebate summary for you."""
    return {"ok": True, **mess.profile(context().actor)}


@tool
def forecast_kitchen_demand(meal: str, date: str = "") -> dict:
    """Predicted dining headcount for a meal (Mess Office staff and faculty only).

    Args:
        meal: breakfast, lunch or dinner.
        date: Optional YYYY-MM-DD (default tomorrow).
    """
    return {"ok": True, **mess.forecast(context().actor, meal, date or None)}


@tool
def get_today() -> dict:
    """Today's date, weekday and the earliest valid mess-out start date. Use it to resolve 'tomorrow', 'next Friday', etc."""
    t = clock.today()
    return {"ok": True, "today": t.isoformat(), "weekday": t.strftime("%A")}


MESS_TOOLS = [get_today, get_menu_and_macros, preview_mess_out, submit_mess_out_request, get_my_mess_out_history, cancel_mess_out_request,
              get_my_rebate_ledger, get_my_meal_records, get_my_mess_profile, forecast_kitchen_demand]
