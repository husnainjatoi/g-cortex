"""Monthly mess billing, payments and security deposit - real database, no mocks."""
import datetime

import pytest

from app import db
from app.services import billing, clock, mess, settings
from app.services.errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed

NOV = datetime.date(2026, 11, 1)


def eat(uid, day, meal):
    """Record a verified meal exactly as the turnstile does (through the unique ledger)."""
    with db.tx() as cur:
        cur.execute("INSERT INTO mess_ledger (user_id, meal_date, meal_type, status, source) VALUES (%s,%s,%s,'consumed','turnstile') ON CONFLICT DO NOTHING", (uid, day, meal))


def rates(actors, b=50, l=120, d=100, eff="2026-01-01"):
    for m, r in (("breakfast", b), ("lunch", l), ("dinner", d)):
        billing.set_rate(actors["mess"], m, r, eff)


def test_no_rebate_functionality_remains(actors):
    assert not hasattr(mess, "rebate_ledger") and not hasattr(mess, "decide_mess_out") and not hasattr(mess, "rebate_summary")
    from app.agents.tools.mess_tools import MESS_TOOLS
    assert not [t.__name__ for t in MESS_TOOLS if "rebate" in t.__name__]
    from main import app
    assert not [r.path for r in app.routes if "rebate" in getattr(r, "path", "")]
    for k in ("mess_daily_rate", "mess_min_days", "mess_notice_days"):
        with pytest.raises(NotFound):
            settings.get_quick(k)


def test_mess_out_creates_no_financial_records(actors, at):
    r = mess.submit_mess_out(actors["s1"], "2026-11-28", "2026-11-30", "home")
    assert r["status"] == "recorded"
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_rebates")["n"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_ledger WHERE status='rebated'")["n"] == 0
    assert db.query_one("SELECT est_rebate_pkr FROM mess_out_requests")["est_rebate_pkr"] is None


def test_estimate_per_meal_matches_configured_rates(actors, at):
    s1 = actors["s1"]
    est = billing.estimate_bill(s1)
    assert est["kind"] == "estimate" and est["amount_pkr"] is None or est["days_consumed"] == 0   # nothing eaten / no rates
    rates(actors)
    for day in (2, 3, 4):
        eat("2024405", NOV.replace(day=day), "lunch")
    eat("2024405", NOV.replace(day=2), "dinner"); eat("2024405", NOV.replace(day=2), "breakfast")
    est = billing.estimate_bill(s1)
    assert est["kind"] == "estimate" and est["month"] == "2026-11"
    assert est["days_consumed"] == 3 and est["meals"] == {"breakfast": 1, "lunch": 3, "dinner": 1}
    assert est["amount_pkr"] == 1 * 50 + 3 * 120 + 1 * 100 == 510
    assert "ESTIMATE" in est["explanation"] and "not an invoice" in est["explanation"]


def test_estimate_without_configured_rates_is_not_invented(actors, at):
    eat("2024405", NOV.replace(day=2), "lunch")
    est = billing.estimate_bill(actors["s1"])
    assert est["amount_pkr"] is None and "not been configured" in est["explanation"] and est["meals"]["lunch"] == 1


def test_per_day_mode_charges_each_day_once(actors, at):
    settings.update(actors["mess"], "mess_billing_mode", "per_day")
    billing.set_rate(actors["mess"], "daily", 300, "2026-01-01")
    for meal in ("breakfast", "lunch", "dinner"):
        eat("2024405", NOV.replace(day=2), meal)
    eat("2024405", NOV.replace(day=3), "lunch")
    est = billing.estimate_bill(actors["s1"])
    assert est["days_consumed"] == 2 and est["amount_pkr"] == 600.0


def test_duplicate_and_invalid_scans_are_never_billed(actors, at):
    rates(actors)
    qr = mess.generate_qr(actors["s1"], "lunch")
    mess.verify_entry(actors["mess"], qr["payload"], "lunch")
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], qr["payload"], "lunch")              # replay
    at(2026, 11, 25, 10, 1)
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], mess.generate_qr(actors["s1"], "lunch")["payload"], "lunch")   # anti-passback
    with pytest.raises(RuleViolation):
        mess.verify_entry(actors["mess"], "GCQR:2024405:lunch:00000000", "lunch")                         # forged
    est = billing.estimate_bill(actors["s1"])
    assert est["meals"]["lunch"] == 1 and est["amount_pkr"] == 120.0


def test_rate_changes_apply_prospectively_and_require_scope(actors, at):
    with pytest.raises(Forbidden):
        billing.set_rate(actors["s1"], "lunch", 1)
    with pytest.raises(Forbidden):
        billing.set_rate(actors["acad"], "lunch", 1)
    with pytest.raises(ValidationFailed):
        billing.set_rate(actors["mess"], "brunch", 10)
    rates(actors)
    billing.set_rate(actors["mess"], "lunch", 200, "2026-11-10")              # new rate from the 10th
    eat("2024405", NOV.replace(day=5), "lunch"); eat("2024405", NOV.replace(day=12), "lunch")
    assert billing.estimate_bill(actors["s1"])["amount_pkr"] == 120 + 200


def test_invoice_generation_after_month_end_is_idempotent_and_immutable(actors, at):
    rates(actors)
    for day in (3, 4):
        eat("2024405", NOV.replace(day=day), "lunch")
    eat("2024668", NOV.replace(day=3), "dinner")
    with pytest.raises(RuleViolation) as e:                                    # November is still open
        billing.generate_monthly_invoices(actors["mess"], "2026-11")
    assert e.value.code == "month_open"
    at(2026, 12, 2)
    with pytest.raises(Forbidden):
        billing.generate_monthly_invoices(actors["s1"], "2026-11")
    r = billing.generate_monthly_invoices(actors["mess"], "2026-11")
    assert r["created"] == 2
    again = billing.generate_monthly_invoices(actors["mess"], "2026-11")       # no duplicates
    assert again["created"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_invoices")["n"] == 2
    inv = billing.my_invoices(actors["s1"])[0]
    assert inv["invoice_no"] == "INV-202611-2024405" and inv["total_pkr"] == 240.0 and inv["status"] == "unpaid" and inv["days_count"] == 2
    # students without meals are not billed; consumption snapshot cannot be edited or deleted
    assert billing.my_invoices(actors["s3"]) == []
    with pytest.raises(Exception) as ex:
        with db.tx() as cur:
            cur.execute("UPDATE mess_invoices SET amount_pkr=1 WHERE invoice_no='INV-202611-2024405'")
    assert "immutable" in str(ex.value)
    with pytest.raises(Exception):
        with db.tx() as cur:
            cur.execute("DELETE FROM mess_invoices")
    # the finalized invoice is what estimate_bill returns for that month, and it no longer moves
    eat("2024405", NOV.replace(day=20), "dinner")
    est = billing.estimate_bill(actors["s1"], month="2026-11")
    assert est["kind"] == "finalized" and est["amount_pkr"] == 240.0
    notes = [n["title"] for n in db.query("SELECT title FROM notifications WHERE user_id='2024405'")]
    assert any("INV-202611-2024405" in t for t in notes)


def test_invoice_generation_requires_configured_rates(actors, at):
    eat("2024405", NOV.replace(day=3), "lunch")
    at(2026, 12, 2)
    with pytest.raises(RuleViolation) as e:
        billing.generate_monthly_invoices(actors["mess"], "2026-11")
    assert e.value.code == "rates_missing" and db.query_one("SELECT COUNT(*) AS n FROM mess_invoices")["n"] == 0


def _invoiced(actors, at):
    rates(actors)
    for day in (3, 4, 5):
        eat("2024405", NOV.replace(day=day), "lunch")
    at(2026, 12, 2)
    billing.generate_monthly_invoices(actors["mess"], "2026-11")
    return "INV-202611-2024405"


def test_payment_flow_challan_receipt_verification(actors, at):
    no = _invoiced(actors, at)                                                 # 3 x 120 = 360
    s1, adm = actors["s1"], actors["mess"]
    ch = billing.generate_challan(s1, no)
    assert ch["amount_pkr"] == 360.0 and ch["challan_no"].startswith("CH-INV-202611-2024405")
    assert billing.generate_challan(s1, no)["challan_no"] == ch["challan_no"]    # idempotent for the same balance
    with pytest.raises(NotFound):
        billing.generate_challan(actors["s2"], no)                              # other student's invoice is invisible
    with pytest.raises(RuleViolation) as e:
        billing.submit_payment(s1, no, 400, "BANK-0001")
    assert e.value.code == "overpayment"
    p = billing.submit_payment(s1, no, 100, "BANK-0001", challan_no=ch["challan_no"])
    assert billing.get_invoice(s1, no)["paid_pkr"] == 0                         # unverified receipts do not reduce the balance
    with pytest.raises(Conflict):
        billing.submit_payment(s1, no, 50, "BANK-0001")                         # duplicate receipt reference
    with pytest.raises(Forbidden):
        billing.verify_payment(s1, p["payment_id"], True)                       # students cannot verify
    billing.verify_payment(adm, p["payment_id"], True)
    inv = billing.get_invoice(s1, no)
    assert inv["status"] == "partial" and inv["balance_pkr"] == 260.0
    p2 = billing.submit_payment(s1, no, 260, "BANK-0002")
    billing.verify_payment(adm, p2["payment_id"], False, "blurry receipt")      # rejected: balance unchanged
    assert billing.get_invoice(s1, no)["balance_pkr"] == 260.0
    p3 = billing.submit_payment(s1, no, 260, "BANK-0003")
    billing.verify_payment(adm, p3["payment_id"], True)
    inv = billing.get_invoice(s1, no)
    assert inv["status"] == "paid" and inv["balance_pkr"] == 0
    assert [x["status"] for x in inv["payments"]] == ["verified", "rejected", "verified"]
    assert billing.estimate_bill(s1, month="2026-11")["outstanding_total_pkr"] == 0
    with pytest.raises(RuleViolation):
        billing.generate_challan(s1, no)                                        # nothing outstanding


def test_adjustment_requires_authority_reason_and_keeps_original(actors, at):
    no = _invoiced(actors, at)
    with pytest.raises(Forbidden):
        billing.adjust_invoice(actors["s1"], no, -60, "waiver requested")
    with pytest.raises(Forbidden):
        billing.adjust_invoice(actors["acad"], no, -60, "wrong office")
    with pytest.raises(ValidationFailed):
        billing.adjust_invoice(actors["mess"], no, -60, "x")
    inv = billing.adjust_invoice(actors["mess"], no, -60, "Duplicate lunch entry on 4 Nov")
    assert inv["amount_pkr"] == 360.0 and inv["adjustments_pkr"] == -60.0 and inv["total_pkr"] == 300.0
    assert db.query_one("SELECT outcome FROM audit_log WHERE action='mess.invoice_adjust' ORDER BY audit_id DESC LIMIT 1")["outcome"] == "success"


def test_students_cannot_see_other_students_billing(actors, at):
    no = _invoiced(actors, at)
    with pytest.raises(Forbidden):
        billing.estimate_bill(actors["s2"], "2024405")
    with pytest.raises(NotFound):
        billing.get_invoice(actors["s2"], no)
    with pytest.raises(Forbidden):
        billing.my_invoices(actors["s2"], "2024405")
    with pytest.raises(Forbidden):
        billing.list_invoices(actors["s2"])


def test_security_deposit_lifecycle(actors, at):
    s1, adm = actors["s1"], actors["mess"]
    assert billing.deposit_summary(s1)["status"] == "not_configured"
    with pytest.raises(RuleViolation) as e:
        billing.record_deposit_payment(adm, "2024405", 1000, "DEP-0001", True)
    assert e.value.code == "deposit_unconfigured"
    with pytest.raises(Forbidden):
        settings.update(actors["acad"], "mess_deposit_amount", "5000")
    settings.update(adm, "mess_deposit_amount", "5000")
    summary = billing.record_deposit_payment(adm, "2024405", 3000, "DEP-0001", False)
    assert summary["original_deposit_pkr"] == 5000 and summary["status"] == "unpaid" and summary["pending_verification_pkr"] == 3000
    with pytest.raises(Forbidden):
        billing.verify_deposit_txn(s1, summary["transactions"][0]["txn_id"], True)
    billing.verify_deposit_txn(adm, summary["transactions"][0]["txn_id"], True)
    assert billing.deposit_summary(s1)["status"] == "partial"
    with pytest.raises(RuleViolation):
        billing.record_deposit_payment(adm, "2024405", 2500, "DEP-0002", True)   # would exceed required amount
    with pytest.raises(Conflict):
        billing.record_deposit_payment(adm, "2024405", 100, "DEP-0001", True)
    billing.record_deposit_payment(adm, "2024405", 2000, "DEP-0003", True)
    d = billing.deposit_summary(s1)
    assert d["status"] == "paid" and d["refundable_balance_pkr"] == 5000 and len(d["transactions"]) == 2
    settings.update(adm, "mess_deposit_amount", "9000")                          # config change does not alter existing record
    assert billing.deposit_summary(s1)["original_deposit_pkr"] == 5000


def test_deposit_is_not_prepaid_credit(actors, at):
    no = _invoiced(actors, at)
    settings.update(actors["mess"], "mess_deposit_amount", "5000")
    billing.record_deposit_payment(actors["mess"], "2024405", 5000, "DEP-9", True)
    inv = billing.get_invoice(actors["s1"], no)
    assert inv["status"] == "unpaid" and inv["balance_pkr"] == 360.0             # bill NOT auto-deducted from the deposit
    assert billing.deposit_summary(actors["s1"])["refundable_balance_pkr"] == 5000


def test_final_clearance_settlement(actors, at):
    no = _invoiced(actors, at)                                                    # Nov bill 360 unpaid
    for day in (3, 4):
        eat("2024405", datetime.date(2026, 12, day), "dinner")                    # December consumption, not yet invoiced
    s1, adm = actors["s1"], actors["mess"]
    settings.update(adm, "mess_deposit_amount", "5000")
    billing.record_deposit_payment(adm, "2024405", 5000, "DEP-C1", True)
    prev = billing.clearance_preview(s1)
    assert prev["deposit_balance_pkr"] == 5000 and prev["outstanding_invoices_pkr"] == 360 and prev["uninvoiced_consumption_pkr"] == 200
    assert prev["refundable_pkr"] == 5000 - 560 and prev["additional_due_pkr"] == 0
    r = billing.request_clearance(s1)
    cid = r["clearance_id"]
    with pytest.raises(Conflict):
        billing.request_clearance(s1)
    with pytest.raises(Forbidden):
        billing.settle_clearance(s1, cid)
    with pytest.raises(RuleViolation):
        billing.settle_clearance(adm, cid)                                        # must be reviewed/approved first
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_transactions WHERE kind IN ('refund','settlement_deduction')")["n"] == 0   # nothing automatic
    billing.review_clearance(adm, cid, True, "documents verified")
    out = billing.settle_clearance(adm, cid)
    assert out["deductions_pkr"] == 560 and out["refundable_pkr"] == 4440 and out["additional_due_pkr"] == 0
    assert billing.get_invoice(s1, no)["status"] == "paid"
    assert len(billing.my_invoices(s1)) == 2                                      # December was finalized during settlement
    d = billing.deposit_summary(s1)
    assert d["deductions_pkr"] == 560 and d["refundable_balance_pkr"] == 0 and d["refunded_or_reserved_pkr"] == 4440
    assert [t["kind"] for t in d["transactions"]].count("settlement_deduction") == 2
    refund = next(t for t in d["transactions"] if t["kind"] == "refund")
    assert refund["status"] == "pending"                                          # refund is not "returned" until an admin records it
    with pytest.raises(RuleViolation):
        billing.mark_refund_returned(adm, cid, "CHQ-1234") if False else billing.settle_clearance(adm, cid)
    billing.mark_refund_returned(adm, cid, "CHQ-1234")
    d = billing.deposit_summary(s1)
    assert d["clearance"]["status"] == "returned" and d["clearance"]["return_reference"] == "CHQ-1234" and d["status"] == "refunded"


def test_clearance_with_dues_exceeding_deposit(actors, at):
    no = _invoiced(actors, at)
    s1, adm = actors["s1"], actors["mess"]
    settings.update(adm, "mess_deposit_amount", "200")
    billing.record_deposit_payment(adm, "2024405", 200, "DEP-L", True)
    cid = billing.request_clearance(s1)["clearance_id"]
    billing.review_clearance(adm, cid, True)
    out = billing.settle_clearance(adm, cid)
    assert out["deductions_pkr"] == 200 and out["refundable_pkr"] == 0 and out["additional_due_pkr"] == 160
    assert billing.get_invoice(s1, no)["balance_pkr"] == 160.0
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_transactions WHERE kind='refund'")["n"] == 0


def test_clearance_blocked_by_unverified_receipts(actors, at):
    no = _invoiced(actors, at)
    adm = actors["mess"]
    settings.update(adm, "mess_deposit_amount", "5000")
    billing.record_deposit_payment(adm, "2024405", 5000, "DEP-B", True)
    billing.submit_payment(actors["s1"], no, 100, "BANK-77")
    cid = billing.request_clearance(actors["s1"])["clearance_id"]
    billing.review_clearance(adm, cid, True)
    with pytest.raises(RuleViolation) as e:
        billing.settle_clearance(adm, cid)
    assert e.value.code == "payments_unverified"


def test_admin_dues_summary_and_reports(actors, at):
    _invoiced(actors, at)
    dues = billing.outstanding_dues(actors["mess"])
    assert dues[0]["user_id"] == "2024405" and dues[0]["outstanding_pkr"] == 360.0
    s = billing.billing_summary(actors["mess"])
    assert s["invoiced_pkr"] == 360.0 and s["outstanding_pkr"] == 360.0 and s["collected_pkr"] == 0
    from app.services import reports
    rep = reports.overview(actors["super"])
    assert "billing" in rep["mess"] and "rebates" not in rep["mess"]
    dash = reports.student_dashboard(actors["s1"])
    assert "rebate_pkr" not in dash["mess"] and dash["mess"]["bill"]["outstanding_total_pkr"] == 360.0
