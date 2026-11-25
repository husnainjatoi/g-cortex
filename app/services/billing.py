"""Monthly mess billing, payments (challan + receipt verification) and the refundable security deposit.

Model: students pay NOTHING in advance. They eat during the month; each verified turnstile entry is one consumed meal in
`mess_ledger`. At month end an immutable invoice is generated from those records at the configured rates. The refundable
security deposit is held separately for the stay, is never used as prepaid credit, and is only netted against unpaid dues
during an administrator-processed final clearance.
"""
import datetime
import json
from decimal import Decimal

from app import db
from . import audit, clock, events, settings
from .actor import Actor
from .audit import op
from .errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed

BILL_INVOICE_GENERATED = "mess.invoice_generated"
BILL_PAYMENT_SUBMITTED = "mess.payment_submitted"
BILL_PAYMENT_VERIFIED = "mess.payment_verified"
BILL_PAYMENT_REJECTED = "mess.payment_rejected"
BILL_INVOICE_ADJUSTED = "mess.invoice_adjusted"
DEPOSIT_RECORDED = "mess.deposit_recorded"
CLEARANCE_UPDATED = "mess.clearance_updated"

MEALS = ("breakfast", "lunch", "dinner")
Q = Decimal("0.01")


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Q)


def month_start(value=None) -> datetime.date:
    """'2026-11' | '2026-11-15' | date | None(today) -> first day of that month."""
    if value is None or value == "":
        d = clock.today()
    elif isinstance(value, datetime.date):
        d = value
    else:
        s = str(value).strip()
        try:
            d = datetime.date.fromisoformat(s + "-01") if len(s) == 7 else datetime.date.fromisoformat(s)
        except ValueError:
            raise ValidationFailed("Month must be YYYY-MM.")
    return d.replace(day=1)


def _month_end(ms: datetime.date) -> datetime.date:
    return (ms.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)


# ── Rates (configured by administrators; nothing is assumed) ──

def _rate_history(cur) -> dict[str, list[tuple[datetime.date, Decimal]]]:
    cur.execute("SELECT meal_type, rate_pkr, effective_from FROM mess_rates ORDER BY effective_from")
    h: dict[str, list] = {}
    for r in cur.fetchall():
        h.setdefault(r["meal_type"], []).append((r["effective_from"], _d(r["rate_pkr"])))
    return h


def _rate_on(hist, meal: str, day: datetime.date) -> Decimal | None:
    rate = None
    for eff, val in hist.get(meal, []):
        if eff <= day:
            rate = val
    return rate


def current_rates(actor: Actor) -> dict:
    with db.tx() as cur:
        hist = _rate_history(cur)
        mode = settings.get(cur, "mess_billing_mode")
        today = clock.today()
        cur.execute("SELECT meal_type, rate_pkr, effective_from, set_by, created_at FROM mess_rates ORDER BY effective_from DESC, meal_type")
        return {"billing_mode": mode, "effective_today": {m: (float(r) if (r := _rate_on(hist, m, today)) is not None else None) for m in (*MEALS, "daily")},
                "history": [dict(r) for r in cur.fetchall()], "configured": _configured(hist, mode, today)}


def _configured(hist, mode, day) -> bool:
    if mode == "shared_cost":
        return True                                   # uses recorded food expenditure instead of per-meal rates
    need = ("daily",) if mode == "per_day" else MEALS
    return all(_rate_on(hist, m, day) is not None for m in need)


@op("mess.rate_set", "rate", "meal_type")
def set_rate(actor: Actor, meal_type: str, rate_pkr: float, effective_from: str | None = None) -> dict:
    actor.require_scope("mess")
    if meal_type not in (*MEALS, "daily"):
        raise ValidationFailed("meal_type must be breakfast, lunch, dinner or daily.")
    try:
        rate = _d(rate_pkr)
    except Exception:
        raise ValidationFailed("Rate must be a number.")
    if rate < 0 or rate > 100000:
        raise ValidationFailed("Rate must be between 0 and 100000 PKR.")
    eff = clock.parse_date(effective_from, "effective_from") if effective_from else clock.today()
    with db.tx() as cur:
        # A rate can never be changed retroactively for a month that already has finalized invoices.
        cur.execute("SELECT 1 FROM mess_invoices WHERE billing_month >= date_trunc('month', %s::date)::date LIMIT 1", (eff,))
        if cur.fetchone():
            raise RuleViolation("Invoices are already finalized for that period; rates cannot be changed retroactively. Choose a later effective date.")
        cur.execute("""INSERT INTO mess_rates (meal_type, rate_pkr, effective_from, set_by) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (meal_type, effective_from) DO UPDATE SET rate_pkr=EXCLUDED.rate_pkr, set_by=EXCLUDED.set_by, created_at=now()""",
                    (meal_type, rate, eff, actor.user_id))
        audit.log(cur, actor, "mess.rate_set", "rate", meal_type, detail={"rate_pkr": str(rate), "effective_from": str(eff)})
    return current_rates(actor)


# ── Consumption & estimate ──────────────────────────────────

def _consumption(cur, user_id: str, ms: datetime.date) -> list[dict]:
    """Verified consumption only: the unique index on (user, date, meal, 'consumed') makes duplicate scans impossible."""
    cur.execute("""SELECT meal_date, meal_type FROM mess_ledger WHERE user_id=%s AND status='consumed'
                   AND meal_date >= %s AND meal_date <= %s ORDER BY meal_date, meal_type""", (user_id, ms, _month_end(ms)))
    return [dict(r) for r in cur.fetchall()]


def _price(rows: list[dict], hist, mode: str) -> dict:
    counts = {m: sum(1 for r in rows if r["meal_type"] == m) for m in MEALS}
    days = sorted({r["meal_date"] for r in rows})
    lines, total, missing = [], Decimal(0), []
    if mode == "per_day":
        by_rate: dict[Decimal | None, int] = {}
        for d in days:
            by_rate[_rate_on(hist, "daily", d)] = by_rate.get(_rate_on(hist, "daily", d), 0) + 1
        for rate, n in by_rate.items():
            if rate is None:
                missing.append("daily")
                lines.append({"label": "Days with meals", "count": n, "rate_pkr": None, "amount_pkr": None})
            else:
                lines.append({"label": "Days with meals", "count": n, "rate_pkr": float(rate), "amount_pkr": float(rate * n)}); total += rate * n
    else:
        for m in MEALS:
            by_rate = {}
            for r in rows:
                if r["meal_type"] == m:
                    rt = _rate_on(hist, m, r["meal_date"]); by_rate[rt] = by_rate.get(rt, 0) + 1
            for rate, n in by_rate.items():
                if rate is None:
                    missing.append(m); lines.append({"label": m.capitalize(), "count": n, "rate_pkr": None, "amount_pkr": None})
                else:
                    lines.append({"label": m.capitalize(), "count": n, "rate_pkr": float(rate), "amount_pkr": float(rate * n)}); total += rate * n
    return {"days_count": len(days), "meals": counts, "lines": lines, "amount_pkr": None if missing else total, "missing_rates": sorted(set(missing))}


def _invoice(cur, invoice_id=None, *, user_id=None, ms=None) -> dict | None:
    where, p = ("i.invoice_id=%s", (invoice_id,)) if invoice_id is not None else ("i.user_id=%s AND i.billing_month=%s", (user_id, ms))
    cur.execute(f"""SELECT i.*, u.full_name,
        COALESCE((SELECT SUM(amount_pkr) FROM mess_invoice_adjustments a WHERE a.invoice_id=i.invoice_id),0) AS adjustments_pkr,
        COALESCE((SELECT SUM(amount_pkr) FROM mess_payments p WHERE p.invoice_id=i.invoice_id AND p.status='verified'),0) AS paid_pkr,
        COALESCE((SELECT SUM(amount_pkr) FROM mess_payments p WHERE p.invoice_id=i.invoice_id AND p.status='submitted'),0) AS pending_pkr
        FROM mess_invoices i JOIN users u USING (user_id) WHERE {where}""", p)
    r = cur.fetchone()
    if not r:
        return None
    r = dict(r)
    r["total_pkr"] = float(_d(r["amount_pkr"]) + _d(r["adjustments_pkr"]))
    r["balance_pkr"] = float(_d(r["total_pkr"]) - _d(r["paid_pkr"]))
    for k in ("amount_pkr", "adjustments_pkr", "paid_pkr", "pending_pkr"):
        r[k] = float(r[k])
    r["billing_month_label"] = r["billing_month"].strftime("%Y-%m")
    r["overdue"] = bool(r["balance_pkr"] > 0 and r["due_date"] < clock.today())
    return r


def _outstanding_total(cur, user_id: str) -> float:
    cur.execute("SELECT invoice_id FROM mess_invoices WHERE user_id=%s AND status <> 'paid'", (user_id,))
    return round(sum(max(0.0, _invoice(cur, r["invoice_id"])["balance_pkr"]) for r in cur.fetchall()), 2)


ESTIMATE_DISCLAIMER = ("This is an estimated mess bill calculated using the total food expenditure divided by the number of students using the mess. "
                       "Your actual monthly bill may differ. This estimate is provided only to help you understand and plan your expected expenses.")


def _cost_share(cur, ms: datetime.date) -> dict:
    """Shared-cost figures for ONE billing period: total food expenditure and the number of DISTINCT students with verified meals in that same period."""
    cur.execute("SELECT COALESCE(SUM(amount_pkr),0) AS total, COUNT(*) AS lines FROM mess_expenses WHERE period=%s", (ms,))
    e = cur.fetchone()
    cur.execute("""SELECT COUNT(DISTINCT user_id) AS n FROM mess_ledger WHERE status='consumed' AND meal_date BETWEEN %s AND %s""", (ms, _month_end(ms)))
    users = cur.fetchone()["n"]
    total = _d(e["total"])
    share = (total / users).quantize(Q) if (e["lines"] and users) else None
    return {"total_expenditure": total, "expense_entries": e["lines"], "students_using": users, "per_student": share}


def month_cost_summary(actor: Actor, month: str | None = None) -> dict:
    ms = month_start(month)
    with db.tx() as cur:
        c = _cost_share(cur, ms)
    return {"month": ms.strftime("%Y-%m"), "total_food_expenditure_pkr": float(c["total_expenditure"]), "students_using_mess": c["students_using"],
            "estimated_per_student_pkr": float(c["per_student"]) if c["per_student"] is not None else None, "expense_entries": c["expense_entries"], "disclaimer": ESTIMATE_DISCLAIMER}


def estimate_bill(actor: Actor, student_id: str | None = None, month: str | None = None) -> dict:
    """ESTIMATE for a month = total food expenditure / distinct students who used the mess in that same month (both from live data),
    or, when the month has already been invoiced, the finalized invoice. An estimate is never a liability and never a bill."""
    sid = actor.resolve_student(student_id, "mess")
    ms = month_start(month)
    with db.tx() as cur:
        mode, hist = settings.get(cur, "mess_billing_mode"), _rate_history(cur)
        inv = _invoice(cur, user_id=sid, ms=ms)
        prev = _invoice(cur, user_id=sid, ms=(ms - datetime.timedelta(days=1)).replace(day=1))
        rows = _consumption(cur, sid, ms)
        counts = {m: sum(1 for r in rows if r["meal_type"] == m) for m in MEALS}
        days = len({r["meal_date"] for r in rows})
        cost = _cost_share(cur, ms)
        out = {"student_id": sid, "month": ms.strftime("%Y-%m"), "billing_period": {"month": ms.strftime("%Y-%m"), "start": str(ms), "end": str(_month_end(ms))},
               "billing_mode": mode, "days_consumed": days, "meals": counts, "outstanding_total_pkr": _outstanding_total(cur, sid), "disclaimer": ESTIMATE_DISCLAIMER,
               "total_food_expenditure_pkr": float(cost["total_expenditure"]), "students_using_mess": cost["students_using"], "estimated_per_student_pkr": float(cost["per_student"]) if cost["per_student"] is not None else None,
               "expense_entries": cost["expense_entries"], "lines": [],
               "previous_invoice": ({k: prev[k] for k in ("invoice_no", "billing_month_label", "total_pkr", "balance_pkr", "status", "due_date")} if prev else None)}
        if inv:
            out.update(kind="finalized", amount_pkr=inv["total_pkr"], invoice=inv, lines=inv["lines"],
                       explanation=f"Finalized invoice {inv['invoice_no']} for {out['month']}: PKR {inv['total_pkr']:.2f} (balance PKR {inv['balance_pkr']:.2f}, {inv['status']}). This is the confirmed amount; estimates no longer apply to this month.")
            return out
        out.update(kind="estimate", invoice=None)
        if cost["per_student"] is None:
            out["amount_pkr"] = None
            out["explanation"] = ("The estimate cannot be calculated yet because " + ("the Mess Office has not entered the food expenditure for this period." if not cost["expense_entries"] else "no student has used the mess in this period.")
                                  + " Estimate = total food expenditure / students using the mess.")
            return out
        per = float(cost["per_student"])
        out["amount_pkr"] = per if days > 0 else 0.0
        out["lines"] = [{"label": "Total food expenditure", "amount_pkr": float(cost["total_expenditure"])}, {"label": "Students using the mess", "count": cost["students_using"]},
                        {"label": "Share per student", "amount_pkr": per}]
        calc = f"PKR {float(cost['total_expenditure']):,.2f} / {cost['students_using']} students = PKR {per:,.2f}"
        out["explanation"] = (f"ESTIMATE (not an invoice) for {out['month']}: {calc}. " + ("You have eaten on %d day(s) this period. " % days if days else "You have no verified meals in this period yet, so your estimate is PKR 0.00. ")
                              + "Your actual monthly bill may differ.")
        return out


def meals_detail(actor: Actor, student_id: str | None = None, month: str | None = None) -> list[dict]:
    sid = actor.resolve_student(student_id, "mess")
    with db.tx() as cur:
        return _consumption(cur, sid, month_start(month))


# ── Invoices ────────────────────────────────────────────────

def _priced(cur, user_id: str, ms: datetime.date, mode, hist) -> dict:
    """Month charges for one student under the configured billing basis (shared_cost uses the same formula as the estimate)."""
    rows = _consumption(cur, user_id, ms)
    if mode != "shared_cost":
        return _price(rows, hist, mode)
    counts = {m: sum(1 for r in rows if r["meal_type"] == m) for m in MEALS}
    days = len({r["meal_date"] for r in rows})
    if days == 0:
        return {"days_count": 0, "meals": counts, "lines": [], "amount_pkr": Decimal(0), "missing_rates": []}
    c = _cost_share(cur, ms)
    if c["per_student"] is None:
        return {"days_count": days, "meals": counts, "lines": [], "amount_pkr": None, "missing_rates": ["food_expenditure"]}
    return {"days_count": days, "meals": counts, "amount_pkr": c["per_student"], "missing_rates": [],
            "lines": [{"label": "Share of total food expenditure", "count": c["students_using"], "rate_pkr": float(c["per_student"]), "amount_pkr": float(c["per_student"]),
                       "total_food_expenditure_pkr": float(c["total_expenditure"]), "students_using_mess": c["students_using"]}]}


def _generate_one(cur, actor, user_id: str, ms: datetime.date, mode, hist, due_days: int) -> dict | None:
    priced = _priced(cur, user_id, ms, mode, hist)
    if priced["days_count"] == 0:
        return None                                    # never bill unconsumed meals
    if priced["missing_rates"] == ["food_expenditure"]:
        raise RuleViolation("The food expenditure for this month has not been entered yet; enter it before generating invoices.", code="expenses_missing")
    if priced["missing_rates"]:
        raise RuleViolation(f"Mess rates are not configured for: {', '.join(priced['missing_rates'])}. Configure them before generating invoices.", code="rates_missing")
    cur.execute("""INSERT INTO mess_invoices (invoice_no, user_id, billing_month, days_count, meals_breakfast, meals_lunch, meals_dinner, billing_mode,
                   lines, amount_pkr, generated_by, due_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, billing_month) DO NOTHING RETURNING invoice_id""",
                (f"INV-{ms:%Y%m}-{user_id}", user_id, ms, priced["days_count"], priced["meals"]["breakfast"], priced["meals"]["lunch"], priced["meals"]["dinner"],
                 mode, json.dumps(priced["lines"]), priced["amount_pkr"], getattr(actor, "user_id", "SYSTEM"), clock.today() + datetime.timedelta(days=due_days)))
    row = cur.fetchone()
    if not row:
        return None                                    # already invoiced - duplicate generation is a no-op
    inv = _invoice(cur, row["invoice_id"])
    audit.log(cur, actor, "mess.invoice_generate", "invoice", inv["invoice_no"], detail={"amount_pkr": inv["amount_pkr"], "days": inv["days_count"]})
    events.emit(cur, BILL_INVOICE_GENERATED, {"invoice_no": inv["invoice_no"], "user_id": user_id, "month": ms.strftime("%Y-%m"), "amount_pkr": inv["amount_pkr"], "due_date": str(inv["due_date"])})
    return inv


@op("mess.invoices_generate", "invoice", "month")
def generate_monthly_invoices(actor: Actor | None, month: str | None = None) -> dict:
    """Generate invoices for a COMPLETED month for every student who consumed meals. Idempotent."""
    if actor is not None:
        actor.require_scope("mess")
    ms = month_start(month) if month else (clock.today().replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    if _month_end(ms) >= clock.today():
        raise RuleViolation(f"{ms:%Y-%m} has not finished yet; invoices are generated after month end (use the estimate until then).", code="month_open")
    sysactor = actor or Actor("SYSTEM", "admin", frozenset({"super"}), "system", "System")
    created, skipped = [], 0
    with db.tx() as cur:
        mode, hist, due = settings.get(cur, "mess_billing_mode"), _rate_history(cur), settings.get(cur, "mess_invoice_due_days")
        cur.execute("SELECT DISTINCT user_id FROM mess_ledger WHERE status='consumed' AND meal_date BETWEEN %s AND %s ORDER BY 1", (ms, _month_end(ms)))
        for r in cur.fetchall():
            inv = _generate_one(cur, sysactor, r["user_id"], ms, mode, hist, due)
            if inv:
                created.append(inv["invoice_no"])
            else:
                skipped += 1
    return {"month": ms.strftime("%Y-%m"), "created": len(created), "already_invoiced_or_no_meals": skipped, "invoices": created}


def my_invoices(actor: Actor, student_id: str | None = None) -> list[dict]:
    sid = actor.resolve_student(student_id, "mess")
    with db.tx() as cur:
        cur.execute("SELECT invoice_id FROM mess_invoices WHERE user_id=%s ORDER BY billing_month DESC", (sid,))
        return [_invoice(cur, r["invoice_id"]) for r in cur.fetchall()]


def get_invoice(actor: Actor, invoice_no: str) -> dict:
    with db.tx() as cur:
        cur.execute("SELECT invoice_id, user_id FROM mess_invoices WHERE invoice_no=%s", ((invoice_no or "").upper().strip(),))
        r = cur.fetchone()
        if not r or (r["user_id"] != actor.user_id and not actor.has_scope("mess")):
            raise NotFound("Invoice not found.")      # same answer for other students' invoices
        inv = _invoice(cur, r["invoice_id"])
        cur.execute("SELECT * FROM mess_payments WHERE invoice_id=%s ORDER BY submitted_at", (r["invoice_id"],))
        inv["payments"] = [dict(x) for x in cur.fetchall()]
        cur.execute("SELECT * FROM mess_invoice_adjustments WHERE invoice_id=%s ORDER BY at", (r["invoice_id"],))
        inv["adjustment_history"] = [dict(x) for x in cur.fetchall()]
        return inv


def list_invoices(actor: Actor, month: str | None = None, status: str | None = None, student_id: str | None = None) -> list[dict]:
    actor.require_scope("mess")
    where, p = [], []
    if month:
        where.append("billing_month=%s"); p.append(month_start(month))
    if status:
        where.append("status=%s"); p.append(status)
    if student_id:
        where.append("user_id=%s"); p.append(student_id)
    with db.tx() as cur:
        cur.execute(f"SELECT invoice_id FROM mess_invoices {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY billing_month DESC, user_id LIMIT 500", p)
        return [_invoice(cur, r["invoice_id"]) for r in cur.fetchall()]


@op("mess.invoice_adjust", "invoice", "invoice_no")
def adjust_invoice(actor: Actor, invoice_no: str, amount_pkr: float, reason: str) -> dict:
    """The ONLY way to correct a finalized invoice: a signed, reasoned, audited adjustment by the Mess Office."""
    actor.require_scope("mess")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationFailed("A reason (at least 5 characters) is required for an invoice adjustment.")
    amt = _d(amount_pkr)
    if amt == 0 or abs(amt) > 1000000:
        raise ValidationFailed("Adjustment must be a non-zero amount (positive = extra charge, negative = credit).")
    with db.tx() as cur:
        cur.execute("SELECT invoice_id FROM mess_invoices WHERE invoice_no=%s FOR UPDATE", ((invoice_no or "").upper().strip(),))
        r = cur.fetchone()
        if not r:
            raise NotFound("Invoice not found.")
        inv = _invoice(cur, r["invoice_id"])
        if _d(inv["total_pkr"]) + amt < _d(inv["paid_pkr"]):
            raise RuleViolation("The adjustment would reduce the invoice below the amount already verified as paid.")
        cur.execute("INSERT INTO mess_invoice_adjustments (invoice_id, amount_pkr, reason, adjusted_by) VALUES (%s,%s,%s,%s)", (r["invoice_id"], amt, reason, actor.user_id))
        _refresh_status(cur, r["invoice_id"])
        audit.log(cur, actor, "mess.invoice_adjust", "invoice", inv["invoice_no"], detail={"amount_pkr": str(amt), "reason": reason})
        events.emit(cur, BILL_INVOICE_ADJUSTED, {"invoice_no": inv["invoice_no"], "user_id": inv["user_id"], "amount_pkr": float(amt), "reason": reason})
        return _invoice(cur, r["invoice_id"])


def _refresh_status(cur, invoice_id: int) -> None:
    inv = _invoice(cur, invoice_id)
    status = "paid" if inv["balance_pkr"] <= 0 else "partial" if inv["paid_pkr"] > 0 else "unpaid"
    cur.execute("UPDATE mess_invoices SET status=%s WHERE invoice_id=%s", (status, invoice_id))


# ── Challans & payments ─────────────────────────────────────

@op("mess.challan", "invoice", "invoice_no")
def generate_challan(actor: Actor, invoice_no: str) -> dict:
    with db.tx() as cur:
        cur.execute("SELECT invoice_id, user_id FROM mess_invoices WHERE invoice_no=%s FOR UPDATE", ((invoice_no or "").upper().strip(),))
        r = cur.fetchone()
        if not r or (r["user_id"] != actor.user_id and not actor.has_scope("mess")):
            raise NotFound("Invoice not found.")
        inv = _invoice(cur, r["invoice_id"])
        if inv["balance_pkr"] <= 0:
            raise RuleViolation("This invoice has nothing outstanding.")
        cur.execute("SELECT * FROM mess_challans WHERE invoice_id=%s AND amount_pkr=%s ORDER BY issued_at DESC LIMIT 1", (r["invoice_id"], inv["balance_pkr"]))
        ch = cur.fetchone()
        if not ch:
            cur.execute("SELECT COUNT(*) AS n FROM mess_challans WHERE invoice_id=%s", (r["invoice_id"],))
            n = cur.fetchone()["n"] + 1
            cur.execute("INSERT INTO mess_challans (challan_no, invoice_id, user_id, amount_pkr, due_date) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                        (f"CH-{inv['invoice_no']}-{n}", r["invoice_id"], r["user_id"], inv["balance_pkr"], inv["due_date"]))
            ch = cur.fetchone()
            audit.log(cur, actor, "mess.challan", "invoice", inv["invoice_no"], detail={"challan_no": ch["challan_no"], "amount_pkr": inv["balance_pkr"]})
        return {**dict(ch), "student": inv["full_name"], "student_id": inv["user_id"], "invoice_no": inv["invoice_no"], "month": inv["billing_month_label"],
                "instructions": "Pay this amount at an authorised bank branch quoting the challan number, then submit the bank's transaction reference in the portal. "
                                "G-Cortex does not process card or bank payments."}


@op("mess.payment_submit", "invoice", "invoice_no")
def submit_payment(actor: Actor, invoice_no: str, amount_pkr: float, reference: str, method: str = "bank_challan", note: str = "", challan_no: str | None = None) -> dict:
    reference = (reference or "").strip()
    if len(reference) < 4 or len(reference) > 80:
        raise ValidationFailed("A transaction/receipt reference (4-80 characters) is required.")
    if method not in ("bank_challan", "cash_office"):
        raise ValidationFailed("Method must be bank_challan or cash_office.")
    amt = _d(amount_pkr)
    if amt <= 0:
        raise ValidationFailed("Amount must be positive.")
    with db.tx() as cur:
        cur.execute("SELECT invoice_id, user_id FROM mess_invoices WHERE invoice_no=%s FOR UPDATE", ((invoice_no or "").upper().strip(),))
        r = cur.fetchone()
        if not r or r["user_id"] != actor.user_id:
            raise NotFound("Invoice not found.")
        inv = _invoice(cur, r["invoice_id"])
        room = _d(inv["balance_pkr"]) - _d(inv["pending_pkr"])
        if amt > room:
            raise RuleViolation(f"Amount exceeds what is still payable on {inv['invoice_no']} (PKR {room:.2f}, after receipts already awaiting verification).", code="overpayment")
        if challan_no:
            cur.execute("SELECT 1 FROM mess_challans WHERE challan_no=%s AND invoice_id=%s", (challan_no, r["invoice_id"]))
            if not cur.fetchone():
                raise ValidationFailed("Unknown challan number for this invoice.")
        cur.execute("SELECT 1 FROM mess_payments WHERE reference=%s AND status <> 'rejected'", (reference,))
        if cur.fetchone():
            raise Conflict("That receipt reference was already submitted.")
        cur.execute("""INSERT INTO mess_payments (invoice_id, user_id, amount_pkr, method, reference, challan_no, note, channel) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING payment_id""",
                    (r["invoice_id"], actor.user_id, amt, method, reference, challan_no, (note or "")[:255], actor.channel))
        pid = cur.fetchone()["payment_id"]
        audit.log(cur, actor, "mess.payment_submit", "invoice", inv["invoice_no"], detail={"payment_id": pid, "amount_pkr": str(amt), "reference": reference})
        events.emit(cur, BILL_PAYMENT_SUBMITTED, {"payment_id": pid, "invoice_no": inv["invoice_no"], "user_id": actor.user_id, "amount_pkr": float(amt)})
    return {"ok": True, "payment_id": pid, "status": "submitted", "message": f"Receipt {reference} for PKR {amt:.2f} submitted; the Mess Office will verify it."}


def list_payments(actor: Actor, status: str | None = "submitted", student_id: str | None = None) -> list[dict]:
    where, p = [], []
    if actor.has_scope("mess"):
        if student_id:
            where.append("p.user_id=%s"); p.append(student_id)
    else:
        where.append("p.user_id=%s"); p.append(actor.user_id)
    if status:
        where.append("p.status=%s"); p.append(status)
    return db.query(f"""SELECT p.*, i.invoice_no, i.billing_month, u.full_name FROM mess_payments p JOIN mess_invoices i USING (invoice_id) JOIN users u ON u.user_id=p.user_id
                        {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY p.submitted_at DESC LIMIT 300""", p)


@op("mess.payment_verify", "payment", "payment_id")
def verify_payment(actor: Actor, payment_id: int, approve: bool, note: str = "") -> dict:
    actor.require_scope("mess")
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_payments WHERE payment_id=%s FOR UPDATE", (payment_id,))
        p = cur.fetchone()
        if not p:
            raise NotFound("Payment not found.")
        if p["status"] != "submitted":
            raise RuleViolation(f"Payment is already {p['status']}.")
        cur.execute("SELECT invoice_id FROM mess_invoices WHERE invoice_id=%s FOR UPDATE", (p["invoice_id"],))
        if approve:
            inv = _invoice(cur, p["invoice_id"])
            if _d(p["amount_pkr"]) > _d(inv["balance_pkr"]):
                raise RuleViolation("This payment now exceeds the invoice balance (an adjustment may have reduced it).")
        status = "verified" if approve else "rejected"
        cur.execute("UPDATE mess_payments SET status=%s, verified_by=%s, verified_at=now(), verify_note=%s WHERE payment_id=%s", (status, actor.user_id, (note or "")[:255] or None, payment_id))
        _refresh_status(cur, p["invoice_id"])
        inv = _invoice(cur, p["invoice_id"])
        audit.log(cur, actor, "mess.payment_verify", "payment", payment_id, detail={"decision": status, "invoice": inv["invoice_no"], "note": note})
        events.emit(cur, BILL_PAYMENT_VERIFIED if approve else BILL_PAYMENT_REJECTED,
                    {"payment_id": payment_id, "invoice_no": inv["invoice_no"], "user_id": p["user_id"], "amount_pkr": float(p["amount_pkr"]), "note": note, "invoice_status": inv["status"]})
    return {"ok": True, "payment_id": payment_id, "status": status, "invoice_status": inv["status"]}


def outstanding_dues(actor: Actor) -> list[dict]:
    """Per-student unpaid balances across finalized invoices (admin)."""
    actor.require_scope("mess")
    rows = db.query("SELECT invoice_id, user_id FROM mess_invoices WHERE status <> 'paid'")
    out: dict[str, dict] = {}
    with db.tx() as cur:
        for r in rows:
            inv = _invoice(cur, r["invoice_id"])
            if inv["balance_pkr"] > 0:
                s = out.setdefault(inv["user_id"], {"user_id": inv["user_id"], "full_name": inv["full_name"], "outstanding_pkr": 0.0, "invoices": 0, "overdue_pkr": 0.0})
                s["outstanding_pkr"] = round(s["outstanding_pkr"] + inv["balance_pkr"], 2); s["invoices"] += 1
                if inv["overdue"]:
                    s["overdue_pkr"] = round(s["overdue_pkr"] + inv["balance_pkr"], 2)
    return sorted(out.values(), key=lambda x: -x["outstanding_pkr"])


def billing_summary(actor: Actor) -> dict:
    actor.require_scope("mess")
    with db.tx() as cur:
        cur.execute("SELECT invoice_id FROM mess_invoices")
        invs = [_invoice(cur, r["invoice_id"]) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS n FROM mess_payments WHERE status='submitted'")
        pend = cur.fetchone()["n"]
        cur.execute("SELECT COALESCE(SUM(amount_pkr),0) AS s FROM mess_deposit_transactions WHERE kind='deposit_payment' AND status='verified'")
        dep = float(cur.fetchone()["s"])
    by_month: dict[str, dict] = {}
    for i in invs:
        m = by_month.setdefault(i["billing_month_label"], {"month": i["billing_month_label"], "invoiced": 0.0, "collected": 0.0, "outstanding": 0.0, "invoices": 0})
        m["invoiced"] += i["total_pkr"]; m["collected"] += i["paid_pkr"]; m["outstanding"] += max(0.0, i["balance_pkr"]); m["invoices"] += 1
    return {"invoiced_pkr": round(sum(i["total_pkr"] for i in invs), 2), "collected_pkr": round(sum(i["paid_pkr"] for i in invs), 2),
            "outstanding_pkr": round(sum(max(0.0, i["balance_pkr"]) for i in invs), 2), "invoices": len(invs), "payments_awaiting_verification": pend,
            "deposits_held_pkr": dep, "by_month": sorted(by_month.values(), key=lambda x: x["month"])[-12:]}


# ── Security deposit ────────────────────────────────────────

def _deposit_sums(cur, user_id: str) -> dict:
    cur.execute("""SELECT
        COALESCE(SUM(amount_pkr) FILTER (WHERE kind='deposit_payment' AND status='verified'),0) AS paid,
        COALESCE(SUM(amount_pkr) FILTER (WHERE kind='deposit_payment' AND status='pending'),0) AS pending,
        COALESCE(SUM(amount_pkr) FILTER (WHERE kind='settlement_deduction' AND status='verified'),0) AS deducted,
        COALESCE(SUM(amount_pkr) FILTER (WHERE kind='refund' AND status IN ('pending','verified')),0) AS refunded
        FROM mess_deposit_transactions WHERE user_id=%s""", (user_id,))
    s = cur.fetchone()
    return {k: _d(v) for k, v in s.items()}


def deposit_summary(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    with db.tx() as cur:
        cur.execute("SELECT required_amount_pkr FROM mess_deposit_accounts WHERE user_id=%s", (sid,))
        acc = cur.fetchone()
        configured = _d(settings.get(cur, "mess_deposit_amount"))
        required = _d(acc["required_amount_pkr"]) if acc else configured
        s = _deposit_sums(cur, sid)
        balance = s["paid"] - s["deducted"] - s["refunded"]
        status = ("not_configured" if required <= 0 else "refunded" if s["refunded"] > 0 and balance <= 0 and s["paid"] > 0 else
                  "paid" if s["paid"] >= required else "partial" if s["paid"] > 0 else "unpaid")
        cur.execute("""SELECT t.*, v.full_name AS verified_by_name FROM mess_deposit_transactions t LEFT JOIN users v ON v.user_id=t.verified_by
                       WHERE t.user_id=%s ORDER BY t.recorded_at DESC, t.txn_id DESC""", (sid,))
        txns = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM mess_clearances WHERE user_id=%s ORDER BY clearance_id DESC LIMIT 1", (sid,))
        clr = cur.fetchone()
        return {"student_id": sid, "original_deposit_pkr": float(required), "configured_amount_pkr": float(configured), "status": status,
                "verified_paid_pkr": float(s["paid"]), "pending_verification_pkr": float(s["pending"]), "deductions_pkr": float(s["deducted"]), "refunded_or_reserved_pkr": float(s["refunded"]),
                "refundable_balance_pkr": float(max(Decimal(0), balance)), "outstanding_bills_pkr": _outstanding_total(cur, sid), "transactions": txns,
                "clearance": dict(clr) if clr else None,
                "note": "The security deposit is refundable at graduation after clearance, less any unpaid mess dues. It is not prepaid credit and is never deducted for monthly bills."}


@op("mess.deposit_record", "user", "student_id")
def record_deposit_payment(actor: Actor, student_id: str, amount_pkr: float, reference: str, verified: bool = False, note: str = "") -> dict:
    actor.require_scope("mess")
    amt = _d(amount_pkr)
    if amt <= 0:
        raise ValidationFailed("Amount must be positive.")
    if len((reference or "").strip()) < 4:
        raise ValidationFailed("A payment reference is required.")
    with db.tx() as cur:
        cur.execute("SELECT role FROM users WHERE user_id=%s", (student_id,))
        u = cur.fetchone()
        if not u or u["role"] != "student":
            raise NotFound("Student not found.")
        cur.execute("SELECT required_amount_pkr FROM mess_deposit_accounts WHERE user_id=%s FOR UPDATE", (student_id,))
        acc = cur.fetchone()
        if not acc:
            cfg = _d(settings.get(cur, "mess_deposit_amount"))
            if cfg <= 0:
                raise RuleViolation("The security deposit amount has not been configured yet (Rules & Settings).", code="deposit_unconfigured")
            cur.execute("INSERT INTO mess_deposit_accounts (user_id, required_amount_pkr) VALUES (%s,%s) ON CONFLICT DO NOTHING", (student_id, cfg))
            required = cfg
        else:
            required = _d(acc["required_amount_pkr"])
        s = _deposit_sums(cur, student_id)
        if s["paid"] + s["pending"] + amt > required:
            raise RuleViolation(f"Recording PKR {amt:.2f} would exceed the required deposit of PKR {required:.2f}.", code="deposit_exceeded")
        cur.execute("SELECT 1 FROM mess_deposit_transactions WHERE reference=%s AND kind='deposit_payment' AND status<>'rejected'", (reference.strip(),))
        if cur.fetchone():
            raise Conflict("That payment reference is already recorded.")
        cur.execute("""INSERT INTO mess_deposit_transactions (user_id, kind, amount_pkr, reference, status, note, recorded_by, verified_by, verified_at)
                       VALUES (%s,'deposit_payment',%s,%s,%s,%s,%s,%s,%s) RETURNING txn_id""",
                    (student_id, amt, reference.strip(), "verified" if verified else "pending", (note or "")[:255], actor.user_id,
                     actor.user_id if verified else None, clock.now() if verified else None))
        tid = cur.fetchone()["txn_id"]
        audit.log(cur, actor, "mess.deposit_record", "user", student_id, detail={"txn_id": tid, "amount_pkr": str(amt), "verified": verified, "reference": reference})
        events.emit(cur, DEPOSIT_RECORDED, {"txn_id": tid, "user_id": student_id, "amount_pkr": float(amt), "status": "verified" if verified else "pending"})
    return deposit_summary(actor, student_id)


@op("mess.deposit_verify", "deposit_txn", "txn_id")
def verify_deposit_txn(actor: Actor, txn_id: int, approve: bool, note: str = "") -> dict:
    actor.require_scope("mess")
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_deposit_transactions WHERE txn_id=%s FOR UPDATE", (txn_id,))
        t = cur.fetchone()
        if not t or t["kind"] != "deposit_payment":
            raise NotFound("Deposit payment not found.")
        if t["status"] != "pending":
            raise RuleViolation(f"Payment is already {t['status']}.")
        cur.execute("UPDATE mess_deposit_transactions SET status=%s, verified_by=%s, verified_at=now(), note=COALESCE(NULLIF(%s,''), note) WHERE txn_id=%s",
                    ("verified" if approve else "rejected", actor.user_id, note, txn_id))
        audit.log(cur, actor, "mess.deposit_verify", "deposit_txn", txn_id, detail={"decision": "verified" if approve else "rejected", "user_id": t["user_id"]})
        events.emit(cur, DEPOSIT_RECORDED, {"txn_id": txn_id, "user_id": t["user_id"], "amount_pkr": float(t["amount_pkr"]), "status": "verified" if approve else "rejected"})
    return deposit_summary(actor, t["user_id"])


def deposits_overview(actor: Actor, status: str | None = None) -> list[dict]:
    actor.require_scope("mess")
    rows = db.query("SELECT user_id, full_name FROM users WHERE role='student' AND is_active ORDER BY user_id")
    out = []
    for r in rows:
        s = deposit_summary(actor, r["user_id"])
        if status and s["status"] != status:
            continue
        out.append({"user_id": r["user_id"], "full_name": r["full_name"], "status": s["status"], "original_deposit_pkr": s["original_deposit_pkr"], "verified_paid_pkr": s["verified_paid_pkr"],
                    "pending_verification_pkr": s["pending_verification_pkr"], "refundable_balance_pkr": s["refundable_balance_pkr"], "outstanding_bills_pkr": s["outstanding_bills_pkr"]})
    return out


def pending_deposit_txns(actor: Actor) -> list[dict]:
    actor.require_scope("mess")
    return db.query("""SELECT t.*, u.full_name FROM mess_deposit_transactions t JOIN users u USING (user_id) WHERE t.kind='deposit_payment' AND t.status='pending' ORDER BY t.recorded_at""")


# ── Final clearance & settlement ────────────────────────────

def _settlement_calc(cur, user_id: str) -> dict:
    s = _deposit_sums(cur, user_id)
    balance = s["paid"] - s["deducted"] - s["refunded"]
    mode, hist = settings.get(cur, "mess_billing_mode"), _rate_history(cur)
    outstanding = _d(_outstanding_total(cur, user_id))
    cur.execute("SELECT DISTINCT date_trunc('month', meal_date)::date AS m FROM mess_ledger WHERE user_id=%s AND status='consumed' ORDER BY 1", (user_id,))
    uninvoiced, missing, items = _d(0), [], []
    for r in cur.fetchall():
        if _invoice(cur, user_id=user_id, ms=r["m"]):
            continue
        priced = _priced(cur, user_id, r["m"], mode, hist)
        if priced["missing_rates"]:
            missing += priced["missing_rates"]
        else:
            uninvoiced += priced["amount_pkr"]; items.append({"month": r["m"].strftime("%Y-%m"), "amount_pkr": float(priced["amount_pkr"])})
    dues = outstanding + uninvoiced
    return {"deposit_balance_pkr": balance, "outstanding_invoices_pkr": outstanding, "uninvoiced_consumption_pkr": uninvoiced, "uninvoiced_months": items,
            "total_dues_pkr": dues, "refundable_pkr": max(Decimal(0), balance - dues), "additional_due_pkr": max(Decimal(0), dues - balance), "rates_missing": sorted(set(missing))}


def clearance_preview(actor: Actor, student_id: str | None = None) -> dict:
    sid = actor.resolve_student(student_id, "mess")
    with db.tx() as cur:
        c = _settlement_calc(cur, sid)
        return {"student_id": sid, **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in c.items()}}


@op("mess.clearance_request", "user", "student_id")
def request_clearance(actor: Actor, student_id: str | None = None, note: str = "") -> dict:
    sid = actor.resolve_student(student_id, "mess")
    with db.tx() as cur:
        cur.execute("SELECT role FROM users WHERE user_id=%s", (sid,))
        u = cur.fetchone()
        if not u or u["role"] != "student":
            raise NotFound("Student not found.")
        cur.execute("SELECT 1 FROM mess_clearances WHERE user_id=%s AND status IN ('requested','approved','settled')", (sid,))
        if cur.fetchone():
            raise Conflict("A final clearance is already in progress for this student.")
        c = _settlement_calc(cur, sid)
        cur.execute("""INSERT INTO mess_clearances (user_id, requested_by, deposit_balance_pkr, outstanding_pkr, refundable_pkr, additional_due_pkr, note)
                       VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING clearance_id""",
                    (sid, actor.user_id, c["deposit_balance_pkr"], c["total_dues_pkr"], c["refundable_pkr"], c["additional_due_pkr"], (note or "")[:255]))
        cid = cur.fetchone()["clearance_id"]
        audit.log(cur, actor, "mess.clearance_request", "user", sid, detail={"clearance_id": cid, "refundable_pkr": str(c["refundable_pkr"])})
        events.emit(cur, CLEARANCE_UPDATED, {"clearance_id": cid, "user_id": sid, "status": "requested"})
    return {"ok": True, "clearance_id": cid, "status": "requested", **clearance_preview(actor, sid),
            "message": "Final mess clearance requested. The Mess Office will review it; no refund is issued automatically."}


def list_clearances(actor: Actor, status: str | None = None) -> list[dict]:
    actor.require_scope("mess")
    return db.query(f"SELECT c.*, u.full_name FROM mess_clearances c JOIN users u USING (user_id) {'WHERE c.status=%s' if status else ''} ORDER BY c.requested_at DESC", (status,) if status else ())


@op("mess.clearance_review", "clearance", "clearance_id")
def review_clearance(actor: Actor, clearance_id: int, approve: bool, note: str = "") -> dict:
    actor.require_scope("mess")
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_clearances WHERE clearance_id=%s FOR UPDATE", (clearance_id,))
        c = cur.fetchone()
        if not c:
            raise NotFound("Clearance not found.")
        if c["status"] != "requested":
            raise RuleViolation(f"Clearance is already {c['status']}.")
        calc = _settlement_calc(cur, c["user_id"])
        if approve and calc["rates_missing"]:
            raise RuleViolation("Mess rates are missing for unbilled consumption; configure rates before approving.", code="rates_missing")
        cur.execute("""UPDATE mess_clearances SET status=%s, reviewed_by=%s, reviewed_at=now(), note=COALESCE(NULLIF(%s,''), note),
                       deposit_balance_pkr=%s, outstanding_pkr=%s, refundable_pkr=%s, additional_due_pkr=%s WHERE clearance_id=%s""",
                    ("approved" if approve else "rejected", actor.user_id, note, calc["deposit_balance_pkr"], calc["total_dues_pkr"], calc["refundable_pkr"], calc["additional_due_pkr"], clearance_id))
        audit.log(cur, actor, "mess.clearance_review", "clearance", clearance_id, detail={"approved": approve, "note": note})
        events.emit(cur, CLEARANCE_UPDATED, {"clearance_id": clearance_id, "user_id": c["user_id"], "status": "approved" if approve else "rejected", "note": note})
    return {"ok": True, "clearance_id": clearance_id, "status": "approved" if approve else "rejected"}


@op("mess.clearance_settle", "clearance", "clearance_id")
def settle_clearance(actor: Actor, clearance_id: int) -> dict:
    """Authorised settlement: finalize unbilled months, net unpaid dues against the deposit (recorded as deductions), and
    reserve the remaining refundable balance. The cash refund itself is confirmed separately (mark_refund_returned)."""
    actor.require_scope("mess")
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_clearances WHERE clearance_id=%s FOR UPDATE", (clearance_id,))
        c = cur.fetchone()
        if not c:
            raise NotFound("Clearance not found.")
        if c["status"] != "approved":
            raise RuleViolation("Only an approved clearance can be settled.")
        uid = c["user_id"]
        cur.execute("SELECT 1 FROM users WHERE user_id=%s FOR UPDATE", (uid,))
        cur.execute("""SELECT 1 FROM mess_deposit_transactions WHERE user_id=%s AND kind='deposit_payment' AND status='pending'""", (uid,))
        if cur.fetchone():
            raise RuleViolation("There are deposit payments awaiting verification; verify or reject them first.", code="deposit_unverified")
        cur.execute("SELECT 1 FROM mess_payments WHERE user_id=%s AND status='submitted'", (uid,))
        if cur.fetchone():
            raise RuleViolation("There are bill payment receipts awaiting verification; verify or reject them first.", code="payments_unverified")
        mode, hist, due = settings.get(cur, "mess_billing_mode"), _rate_history(cur), settings.get(cur, "mess_invoice_due_days")
        cur.execute("SELECT DISTINCT date_trunc('month', meal_date)::date AS m FROM mess_ledger WHERE user_id=%s AND status='consumed' ORDER BY 1", (uid,))
        for r in cur.fetchall():
            if not _invoice(cur, user_id=uid, ms=r["m"]):
                _generate_one(cur, actor, uid, r["m"], mode, hist, due)      # raises if rates missing -> whole settlement rolls back
        s = _deposit_sums(cur, uid)
        available = s["paid"] - s["deducted"] - s["refunded"]
        deducted = Decimal(0)
        cur.execute("SELECT invoice_id FROM mess_invoices WHERE user_id=%s AND status <> 'paid' ORDER BY billing_month FOR UPDATE", (uid,))
        for r in cur.fetchall():
            inv = _invoice(cur, r["invoice_id"])
            pay = min(_d(inv["balance_pkr"]), available)
            if pay <= 0:
                continue
            ref = f"SETTLE-{clearance_id}-{inv['invoice_no']}"
            cur.execute("""INSERT INTO mess_payments (invoice_id, user_id, amount_pkr, method, reference, note, status, verified_by, verified_at, channel)
                           VALUES (%s,%s,%s,'deposit_settlement',%s,'Netted from security deposit at final clearance','verified',%s,now(),'web')""",
                        (r["invoice_id"], uid, pay, ref, actor.user_id))
            cur.execute("""INSERT INTO mess_deposit_transactions (user_id, kind, amount_pkr, reference, status, note, clearance_id, recorded_by, verified_by, verified_at)
                           VALUES (%s,'settlement_deduction',%s,%s,'verified',%s,%s,%s,%s,now())""",
                        (uid, pay, ref, f"Deduction for {inv['invoice_no']}", clearance_id, actor.user_id, actor.user_id))
            _refresh_status(cur, r["invoice_id"])
            available -= pay; deducted += pay
        remaining_due = _d(_outstanding_total(cur, uid))
        refundable = max(Decimal(0), available)
        if refundable > 0:
            cur.execute("""INSERT INTO mess_deposit_transactions (user_id, kind, amount_pkr, reference, status, note, clearance_id, recorded_by)
                           VALUES (%s,'refund',%s,%s,'pending','Refund awaiting return to student',%s,%s)""", (uid, refundable, f"REFUND-{clearance_id}", clearance_id, actor.user_id))
        cur.execute("""UPDATE mess_clearances SET status='settled', settled_by=%s, settled_at=now(), deductions_pkr=%s, refundable_pkr=%s, additional_due_pkr=%s,
                       outstanding_pkr=%s WHERE clearance_id=%s""", (actor.user_id, deducted, refundable, remaining_due, deducted + remaining_due, clearance_id))
        audit.log(cur, actor, "mess.clearance_settle", "clearance", clearance_id, detail={"deducted": str(deducted), "refundable": str(refundable), "additional_due": str(remaining_due)})
        events.emit(cur, CLEARANCE_UPDATED, {"clearance_id": clearance_id, "user_id": uid, "status": "settled", "refundable_pkr": float(refundable), "additional_due_pkr": float(remaining_due)})
    return {"ok": True, "clearance_id": clearance_id, "status": "settled", "deductions_pkr": float(deducted), "refundable_pkr": float(refundable), "additional_due_pkr": float(remaining_due)}


@op("mess.clearance_return", "clearance", "clearance_id")
def mark_refund_returned(actor: Actor, clearance_id: int, return_reference: str) -> dict:
    actor.require_scope("mess")
    if len((return_reference or "").strip()) < 4:
        raise ValidationFailed("A refund reference (cheque/transfer number) is required.")
    with db.tx() as cur:
        cur.execute("SELECT * FROM mess_clearances WHERE clearance_id=%s FOR UPDATE", (clearance_id,))
        c = cur.fetchone()
        if not c:
            raise NotFound("Clearance not found.")
        if c["status"] != "settled":
            raise RuleViolation("Only a settled clearance can be marked returned.")
        cur.execute("UPDATE mess_deposit_transactions SET status='verified', verified_by=%s, verified_at=now(), note=%s WHERE clearance_id=%s AND kind='refund' AND status='pending'",
                    (actor.user_id, f"Returned: {return_reference.strip()}", clearance_id))
        cur.execute("UPDATE mess_clearances SET status='returned', return_reference=%s, returned_at=now() WHERE clearance_id=%s", (return_reference.strip(), clearance_id))
        audit.log(cur, actor, "mess.clearance_return", "clearance", clearance_id, detail={"reference": return_reference})
        events.emit(cur, CLEARANCE_UPDATED, {"clearance_id": clearance_id, "user_id": c["user_id"], "status": "returned"})
    return {"ok": True, "clearance_id": clearance_id, "status": "returned"}


def init_student_deposit(cur, user_id: str, paid: bool = False, recorded_by: str | None = None, amount=None) -> bool:
    """Create the student's security-deposit account (idempotent). paid=True also records the admission payment as verified."""
    amt = _d(amount if amount is not None else settings.get(cur, "mess_deposit_amount"))
    if amt <= 0:
        return False
    cur.execute("INSERT INTO mess_deposit_accounts (user_id, required_amount_pkr) VALUES (%s,%s) ON CONFLICT (user_id) DO NOTHING", (user_id, amt))
    created = cur.rowcount > 0
    if paid:
        cur.execute("""INSERT INTO mess_deposit_transactions (user_id, kind, amount_pkr, reference, status, note, recorded_by, verified_by, verified_at)
                       SELECT %s,'deposit_payment',%s,%s,'verified','Paid at admission',%s,%s,now()
                       WHERE NOT EXISTS (SELECT 1 FROM mess_deposit_transactions WHERE user_id=%s AND kind='deposit_payment' AND status <> 'rejected')""",
                    (user_id, amt, f"ADMISSION-{user_id}", recorded_by, recorded_by, user_id))
    return created


def ensure_all_student_deposits(paid: bool = True) -> int:
    """Idempotent initialisation for existing students (also run by the migration). Never duplicates or overwrites a record."""
    n = 0
    with db.tx() as cur:
        cur.execute("SELECT user_id FROM users WHERE role='student' AND is_active")
        for r in cur.fetchall():
            n += int(init_student_deposit(cur, r["user_id"], paid=paid))
    return n


# ── Food expenditure ledger (numerator of the shared-cost formula) ──

@op("mess.expense_add", "expense", "month")
def add_expense(actor: Actor, month: str, amount_pkr: float, description: str = "", category: str = "food") -> dict:
    actor.require_scope("mess")
    ms = month_start(month)
    try:
        amt = _d(amount_pkr)
    except Exception:
        raise ValidationFailed("Please enter the amount as a number.")
    if amt <= 0 or amt > 10_000_000_000:
        raise ValidationFailed("Please enter an expenditure amount greater than zero.")
    with db.tx() as cur:
        cur.execute("SELECT 1 FROM mess_invoices WHERE billing_month=%s LIMIT 1", (ms,))
        if cur.fetchone():
            raise RuleViolation(f"Invoices for {ms:%Y-%m} are already finalized, so its food expenditure can no longer be changed.", code="period_closed")
        cur.execute("INSERT INTO mess_expenses (period, category, description, amount_pkr, recorded_by) VALUES (%s,%s,%s,%s,%s) RETURNING expense_id",
                    (ms, (category or "food")[:60], (description or "")[:200] or None, amt, actor.user_id))
        eid = cur.fetchone()["expense_id"]
        audit.log(cur, actor, "mess.expense_add", "expense", eid, detail={"month": str(ms), "amount_pkr": str(amt), "description": description})
    return month_cost_summary(actor, ms.strftime("%Y-%m"))


def list_expenses(actor: Actor, month: str | None = None) -> list[dict]:
    actor.require_scope("mess")
    where, p = ("WHERE e.period=%s", (month_start(month),)) if month else ("", ())
    return db.query(f"SELECT e.*, u.full_name AS recorded_by_name FROM mess_expenses e LEFT JOIN users u ON u.user_id=e.recorded_by {where} ORDER BY e.period DESC, e.expense_id DESC LIMIT 200", p)
