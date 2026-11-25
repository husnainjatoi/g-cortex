"""Mess Management tools - thin adapters over app.services.mess and app.services.billing (monthly bills, deposit)."""
from app.agents.runtime import context, propose, tool
from app.services import billing, clock, mess


@tool
def get_today() -> dict:
    """Today's date, weekday and current month. Use it to resolve 'tomorrow', 'next Friday', 'this month', 'last month'."""
    t = clock.today()
    return {"ok": True, "today": t.isoformat(), "weekday": t.strftime("%A"), "current_month": t.strftime("%Y-%m")}


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
def get_my_estimated_mess_bill(month: str = "") -> dict:
    """Your mess bill for a month as calculated by the backend. For the current/open month this is an ESTIMATE = total food expenditure of
    the period / number of distinct students who used the mess in that period (the backend returns both figures and the result); for a month
    already invoiced it is the finalized invoice. Report the figures and 'explanation' exactly, ALWAYS include the 'disclaimer' text for an
    estimate, never calculate prices yourself, and say so if the expenditure has not been entered yet.

    Args:
        month: Optional YYYY-MM (empty = current month). For 'last month' pass the previous month's YYYY-MM using get_today.
    """
    return {"ok": True, **billing.estimate_bill(context().actor, None, month or None)}


@tool
def get_my_mess_invoices() -> dict:
    """All your finalized monthly mess invoices with totals, paid amounts, balances and status (unpaid/partial/paid)."""
    inv = billing.my_invoices(context().actor)
    return {"ok": True, "invoices": [{k: i[k] for k in ("invoice_no", "billing_month_label", "days_count", "total_pkr", "paid_pkr", "balance_pkr", "status", "due_date", "overdue")} for i in inv]}


@tool
def get_my_mess_invoice(invoice_no: str) -> dict:
    """One finalized invoice with consumption lines, adjustments and payment history.

    Args:
        invoice_no: e.g. 'INV-202611-2024405'.
    """
    return {"ok": True, "invoice": billing.get_invoice(context().actor, invoice_no)}


@tool
def get_my_security_deposit() -> dict:
    """Your refundable mess security deposit: original amount, payment status, verified payments, deductions, refundable balance,
    outstanding bills, transaction history and any final-clearance status. The deposit is NOT prepaid credit."""
    d = billing.deposit_summary(context().actor)
    d["transactions"] = [{k: t[k] for k in ("kind", "amount_pkr", "status", "reference", "recorded_at")} for t in d["transactions"]]
    return {"ok": True, **d}


@tool
def get_my_outstanding_mess_dues() -> dict:
    """Total unpaid mess invoices plus what is still payable on each."""
    inv = [i for i in billing.my_invoices(context().actor) if i["balance_pkr"] > 0]
    return {"ok": True, "outstanding_total_pkr": round(sum(i["balance_pkr"] for i in inv), 2),
            "unpaid_invoices": [{k: i[k] for k in ("invoice_no", "billing_month_label", "balance_pkr", "due_date", "status", "overdue")} for i in inv]}


@tool
def submit_mess_payment_receipt(invoice_no: str, amount_pkr: float, reference: str) -> dict:
    """Submit a bank-challan payment receipt reference for one of your invoices (asks the user to confirm; the Mess Office then
    verifies it). This does NOT move money - G-Cortex does not process payments.

    Args:
        invoice_no: e.g. 'INV-202611-2024405'.
        amount_pkr: Amount paid.
        reference: The bank transaction/receipt reference.
    """
    inv = billing.get_invoice(context().actor, invoice_no)
    if inv["balance_pkr"] <= 0:
        return {"ok": False, "error": f"{inv['invoice_no']} has nothing outstanding."}
    return propose("submit_mess_payment_receipt", {"invoice_no": inv["invoice_no"], "amount_pkr": float(amount_pkr), "reference": reference},
                   f"Submit receipt {reference} for PKR {float(amount_pkr):.2f} against {inv['invoice_no']} (balance PKR {inv['balance_pkr']:.2f}) for Mess Office verification.")


@tool
def request_final_mess_clearance() -> dict:
    """Start the final mess clearance (graduation) workflow: shows the settlement preview and, after the user confirms, files the
    request for Mess Office review. No refund is issued automatically."""
    p = billing.clearance_preview(context().actor)
    return propose("request_mess_clearance", {}, f"File a final mess clearance request. Current preview: deposit PKR {p['deposit_balance_pkr']:.2f}, dues PKR {p['total_dues_pkr']:.2f}, "
                   f"refundable PKR {p['refundable_pkr']:.2f}, additional due PKR {p['additional_due_pkr']:.2f}. The Mess Office must review and process it.")


@tool
def preview_mess_out(start_date: str, end_date: str) -> dict:
    """Check a mess-out (absence declaration) for validity. It is only informational for kitchen planning and has NO effect on billing.

    Args:
        start_date: First day away, YYYY-MM-DD.
        end_date: Last day away, YYYY-MM-DD.
    """
    return {"ok": True, **mess.preview_mess_out(context().actor, start_date, end_date)}


@tool
def submit_mess_out_request(start_date: str, end_date: str, reason: str = "") -> dict:
    """Declare a mess-out (period you will be away) for kitchen planning; asks the user to confirm. It does not change the bill -
    students are billed only for meals actually eaten.

    Args:
        start_date: First day away, YYYY-MM-DD.
        end_date: Last day away, YYYY-MM-DD.
        reason: Optional short reason.
    """
    ev = mess.preview_mess_out(context().actor, start_date, end_date)
    if not ev["eligible"]:
        return {"ok": False, "refused_by_backend": True, "error": " ".join(ev["problems"]), "details": ev}
    return propose("submit_mess_out_request", {"start_date": ev["start_date"], "end_date": ev["end_date"], "reason": reason or None},
                   f"Record a mess-out for {ev['start_date']} to {ev['end_date']} ({ev['days']} days). This does not affect your bill.")


@tool
def get_my_mess_out_history() -> dict:
    """Your mess-out declarations (past, ongoing, upcoming)."""
    return {"ok": True, "records": mess.list_mess_out(context().actor)}


@tool
def cancel_mess_out_request(request_id: int) -> dict:
    """Cancel a mess-out declaration that has not started (asks the user to confirm first).

    Args:
        request_id: The mess-out number.
    """
    mine = {r["request_id"]: r for r in mess.list_mess_out(context().actor)}
    r = mine.get(int(request_id))
    if not r:
        return {"ok": False, "error": f"You have no mess-out #{request_id}."}
    return propose("cancel_mess_out_request", {"request_id": int(request_id)}, f"Cancel mess-out #{request_id} ({r['start_date']} to {r['end_date']}).")


@tool
def get_my_meal_records(days: int = 14) -> dict:
    """Your recent verified meal records with daily nutritional totals (what you actually ate).

    Args:
        days: How many past days to include (1-180).
    """
    return {"ok": True, **mess.meal_records(context().actor, None, days)}


@tool
def get_my_mess_profile() -> dict:
    """Allergies, gate-passes, mess-out records, current-month bill snapshot and outstanding bills for you."""
    return {"ok": True, **mess.profile(context().actor)}


@tool
def forecast_kitchen_demand(meal: str, date: str = "") -> dict:
    """Predicted dining headcount for a meal (Mess Office staff and faculty only).

    Args:
        meal: breakfast, lunch or dinner.
        date: Optional YYYY-MM-DD (default tomorrow).
    """
    return {"ok": True, **mess.forecast(context().actor, meal, date or None)}


MESS_TOOLS = [get_today, get_menu_and_macros, get_my_estimated_mess_bill, get_my_mess_invoices, get_my_mess_invoice, get_my_security_deposit,
              get_my_outstanding_mess_dues, submit_mess_payment_receipt, request_final_mess_clearance, preview_mess_out, submit_mess_out_request,
              get_my_mess_out_history, cancel_mess_out_request, get_my_meal_records, get_my_mess_profile, forecast_kitchen_demand]
