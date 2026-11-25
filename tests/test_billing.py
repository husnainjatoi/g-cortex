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


DISCLAIMER = ("This is an estimated mess bill calculated using the total food expenditure divided by the number of students using the mess. "
              "Your actual monthly bill may differ. This estimate is provided only to help you understand and plan your expected expenses.")


def test_estimate_is_total_food_expenditure_divided_by_students_using_the_mess(actors, at):
    adm = actors["mess"]
    billing.add_expense(adm, "2026-11", 3_000_000, "total November food expenditure")
    billing.add_expense(adm, "2026-10", 9_999_999, "previous month - must not leak into November")
    for uid in ("2024405", "2024668", "2024662"):
        eat(uid, NOV.replace(day=2), "lunch")
    eat("2024405", NOV.replace(day=2), "dinner"); eat("2024405", NOV.replace(day=3), "lunch")          # extra meals of the same student: still ONE student
    est = billing.estimate_bill(actors["s1"])
    assert est["kind"] == "estimate" and est["billing_period"]["month"] == "2026-11"
    assert est["total_food_expenditure_pkr"] == 3_000_000 and est["students_using_mess"] == 3
    assert est["estimated_per_student_pkr"] == 1_000_000.0 and est["amount_pkr"] == 1_000_000.0
    assert est["disclaimer"] == DISCLAIMER and "ESTIMATE (not an invoice)" in est["explanation"] and "3,000,000.00 / 3 students" in est["explanation"]
    assert est["invoice"] is None
    assert billing.estimate_bill(actors["s2"])["amount_pkr"] == 1_000_000.0                                # same shared figure for every mess user
    assert billing.month_cost_summary(actors["s1"], "2026-10")["students_using_mess"] == 0              # October has its own denominator


def test_the_spec_example_3_million_over_1000_students():
    from decimal import Decimal
    assert (Decimal("3000000") / 1000).quantize(Decimal("0.01")) == Decimal("3000.00")                    # arithmetic of the requirement's worked example


def test_denominator_counts_distinct_verified_users_not_registered_students(actors, at):
    billing.add_expense(actors["mess"], "2026-11", 900, "test")
    assert billing.estimate_bill(actors["s1"])["students_using_mess"] == 0                                # three registered students, nobody ate
    eat("2024405", NOV.replace(day=2), "lunch"); eat("2024405", NOV.replace(day=2), "lunch")              # duplicate row cannot add a second student or meal
    est = billing.estimate_bill(actors["s1"])
    assert est["students_using_mess"] == 1 and est["meals"]["lunch"] == 1 and est["amount_pkr"] == 900.0
    assert billing.estimate_bill(actors["s3"])["amount_pkr"] == 0.0                                       # a student who never ate this month has nothing to pay


def test_estimate_unavailable_until_expenditure_is_entered(actors, at):
    eat("2024405", NOV.replace(day=2), "lunch")
    est = billing.estimate_bill(actors["s1"])
    assert est["amount_pkr"] is None and "has not entered the food expenditure" in est["explanation"] and est["disclaimer"] == DISCLAIMER
    with pytest.raises(Forbidden):
        billing.add_expense(actors["s1"], "2026-11", 100, "x")
    with pytest.raises(Forbidden):
        billing.add_expense(actors["acad"], "2026-11", 100, "x")
    with pytest.raises(ValidationFailed):
        billing.add_expense(actors["mess"], "2026-11", 0, "x")


def test_shared_cost_invoice_matches_the_formula_and_is_final(actors, at):
    settings.update(actors["mess"], "mess_billing_mode", "shared_cost")
    billing.add_expense(actors["mess"], "2026-11", 600, "food")
    eat("2024405", NOV.replace(day=2), "lunch"); eat("2024668", NOV.replace(day=2), "lunch")                # 2 users -> 300 each; s3 never ate
    at(2026, 12, 2)
    live = billing.estimate_bill(actors["s1"], month="2026-11")["amount_pkr"]
    assert live == 300.0
    r = billing.generate_monthly_invoices(actors["mess"], "2026-11")
    assert r["created"] == 2 and billing.my_invoices(actors["s3"]) == []
    inv = billing.my_invoices(actors["s1"])[0]
    assert inv["total_pkr"] == 300.0 and inv["lines"][0]["students_using_mess"] == 2 and inv["lines"][0]["total_food_expenditure_pkr"] == 600.0
    est = billing.estimate_bill(actors["s1"], month="2026-11")
    assert est["kind"] == "finalized" and est["amount_pkr"] == 300.0 and est["invoice"]["invoice_no"] == inv["invoice_no"]       # estimate and invoice are separate things
    with pytest.raises(RuleViolation) as e:
        billing.add_expense(actors["mess"], "2026-11", 50, "late entry")
    assert e.value.code == "period_closed"


def test_shared_cost_invoices_require_recorded_expenditure(actors, at):
    settings.update(actors["mess"], "mess_billing_mode", "shared_cost")
    eat("2024405", NOV.replace(day=2), "lunch")
    at(2026, 12, 2)
    with pytest.raises(RuleViolation) as e:
        billing.generate_monthly_invoices(actors["mess"], "2026-11")
    assert e.value.code == "expenses_missing" and db.query_one("SELECT COUNT(*) AS n FROM mess_invoices")["n"] == 0



def test_per_day_mode_invoices_charge_each_day_once(actors, at):
    settings.update(actors["mess"], "mess_billing_mode", "per_day")
    billing.set_rate(actors["mess"], "daily", 300, "2026-01-01")
    for meal in ("breakfast", "lunch", "dinner"):
        eat("2024405", NOV.replace(day=2), meal)
    eat("2024405", NOV.replace(day=3), "lunch")
    at(2026, 12, 2)
    billing.generate_monthly_invoices(actors["mess"], "2026-11")
    inv = billing.my_invoices(actors["s1"])[0]
    assert inv["days_count"] == 2 and inv["total_pkr"] == 600.0

def test_duplicate_and_invalid_scans_are_never_billed(actors, at):
    billing.add_expense(actors["mess"], "2026-11", 1000, "food")
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
    assert est["meals"]["lunch"] == 1 and est["students_using_mess"] == 1 and est["amount_pkr"] == 1000.0

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
    at(2026, 12, 2)
    billing.generate_monthly_invoices(actors["mess"], "2026-11")
    assert billing.my_invoices(actors["s1"])[0]["total_pkr"] == 120 + 200

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


def test_every_student_has_the_15000_deposit_paid_at_admission(actors):
    for k in ("s1", "s2", "s3"):
        d = billing.deposit_summary(actors[k])
        assert (d["original_deposit_pkr"], d["status"], d["verified_paid_pkr"], d["refundable_balance_pkr"]) == (15000.0, "paid", 15000.0, 15000.0)
        assert [t["kind"] for t in d["transactions"]] == ["deposit_payment"] and d["transactions"][0]["reference"].startswith("ADMISSION-")
    n = db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_accounts")["n"]
    assert billing.ensure_all_student_deposits(paid=True) == 0                        # idempotent: no duplicates
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_accounts")["n"] == n
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_transactions WHERE kind='deposit_payment'")["n"] == n


def test_new_students_get_a_deposit_account_automatically(actors):
    from app.services import auth
    try:
        auth.create_user(actors["super"], user_id="NEW5001", full_name="New Student", email="new5001@giki.edu.pk", role="student", date_of_birth="2005-03-09", use_initial_password=True)
        d = billing.deposit_summary(actors["super"], "NEW5001")
        assert (d["original_deposit_pkr"], d["status"]) == (15000.0, "unpaid") and d["transactions"] == []        # status tracked: awaiting payment
        billing.record_deposit_payment(actors["mess"], "NEW5001", 15000, "ADMISSION-NEW5001", True)
        assert billing.deposit_summary(actors["super"], "NEW5001")["status"] == "paid"
        auth.create_user(actors["super"], user_id="NEW5002", full_name="Paid Student", email="new5002@giki.edu.pk", role="student", date_of_birth="2005-03-10", use_initial_password=True, deposit_received=True)
        assert billing.deposit_summary(actors["super"], "NEW5002")["refundable_balance_pkr"] == 15000.0
    finally:
        with db.tx() as cur:
            for u in ("NEW5001", "NEW5002"):
                cur.execute("DELETE FROM mess_deposit_transactions WHERE user_id=%s", (u,)); cur.execute("DELETE FROM mess_deposit_accounts WHERE user_id=%s", (u,))
                cur.execute("DELETE FROM student_profiles WHERE user_id=%s", (u,)); cur.execute("DELETE FROM users WHERE user_id=%s", (u,))


def test_deposit_admin_rules_and_verification(actors):
    adm, s1 = actors["mess"], actors["s1"]
    with pytest.raises(Forbidden):
        settings.update(actors["acad"], "mess_deposit_amount", "5000")
    with db.tx() as cur:                                              # fresh account to exercise partial payment + verification
        cur.execute("DELETE FROM mess_deposit_transactions WHERE user_id='2024668'")
    d = billing.deposit_summary(actors["s2"])
    assert d["status"] == "unpaid"
    r = billing.record_deposit_payment(adm, "2024668", 9000, "DEP-0001", False)
    assert r["status"] == "unpaid" and r["pending_verification_pkr"] == 9000
    with pytest.raises(Forbidden):
        billing.verify_deposit_txn(actors["s2"], r["transactions"][0]["txn_id"], True)
    billing.verify_deposit_txn(adm, r["transactions"][0]["txn_id"], True)
    assert billing.deposit_summary(actors["s2"])["status"] == "partial"
    with pytest.raises(RuleViolation):
        billing.record_deposit_payment(adm, "2024668", 7000, "DEP-0002", True)          # would exceed 15,000
    with pytest.raises(Conflict):
        billing.record_deposit_payment(adm, "2024668", 100, "DEP-0001", True)
    billing.record_deposit_payment(adm, "2024668", 6000, "DEP-0003", True)
    assert billing.deposit_summary(actors["s2"])["status"] == "paid"
    settings.update(adm, "mess_deposit_amount", "20000")                                # config change never alters an existing record
    assert billing.deposit_summary(s1)["original_deposit_pkr"] == 15000

def test_deposit_is_not_prepaid_credit(actors, at):
    no = _invoiced(actors, at)
    inv = billing.get_invoice(actors["s1"], no)
    assert inv["status"] == "unpaid" and inv["balance_pkr"] == 360.0             # bill NOT auto-deducted from the 15,000 deposit
    assert billing.deposit_summary(actors["s1"])["refundable_balance_pkr"] == 15000

def test_final_clearance_settlement(actors, at):
    no = _invoiced(actors, at)                                                    # Nov bill 360 unpaid
    for day in (3, 4):
        eat("2024405", datetime.date(2026, 12, day), "dinner")                    # December consumption, not yet invoiced (2 x 100)
    s1, adm = actors["s1"], actors["mess"]
    prev = billing.clearance_preview(s1)
    assert prev["deposit_balance_pkr"] == 15000 and prev["outstanding_invoices_pkr"] == 360 and prev["uninvoiced_consumption_pkr"] == 200
    assert prev["refundable_pkr"] == 15000 - 560 and prev["additional_due_pkr"] == 0
    cid = billing.request_clearance(s1)["clearance_id"]
    with pytest.raises(Conflict):
        billing.request_clearance(s1)
    with pytest.raises(Forbidden):
        billing.settle_clearance(s1, cid)
    with pytest.raises(RuleViolation):
        billing.settle_clearance(adm, cid)                                        # must be reviewed/approved first
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_transactions WHERE kind IN ('refund','settlement_deduction')")["n"] == 0   # nothing automatic
    billing.review_clearance(adm, cid, True, "documents verified")
    out = billing.settle_clearance(adm, cid)
    assert out["deductions_pkr"] == 560 and out["refundable_pkr"] == 14440 and out["additional_due_pkr"] == 0
    assert billing.get_invoice(s1, no)["status"] == "paid" and len(billing.my_invoices(s1)) == 2
    d = billing.deposit_summary(s1)
    assert d["deductions_pkr"] == 560 and d["refundable_balance_pkr"] == 0 and d["refunded_or_reserved_pkr"] == 14440
    assert [t["kind"] for t in d["transactions"]].count("settlement_deduction") == 2
    assert next(t for t in d["transactions"] if t["kind"] == "refund")["status"] == "pending"      # not "returned" until an admin records it
    with pytest.raises(RuleViolation):
        billing.settle_clearance(adm, cid)
    billing.mark_refund_returned(adm, cid, "CHQ-1234")
    d = billing.deposit_summary(s1)
    assert d["clearance"]["status"] == "returned" and d["clearance"]["return_reference"] == "CHQ-1234" and d["status"] == "refunded"

def test_clearance_with_dues_exceeding_deposit(actors, at):
    billing.set_rate(actors["mess"], "lunch", 7000, "2026-01-01"); billing.set_rate(actors["mess"], "breakfast", 1, "2026-01-01"); billing.set_rate(actors["mess"], "dinner", 1, "2026-01-01")
    for day in (3, 4, 5):
        eat("2024405", NOV.replace(day=day), "lunch")                              # 3 x 7000 = 21000 > 15000 deposit
    at(2026, 12, 2)
    billing.generate_monthly_invoices(actors["mess"], "2026-11")
    s1, adm = actors["s1"], actors["mess"]
    cid = billing.request_clearance(s1)["clearance_id"]
    billing.review_clearance(adm, cid, True)
    out = billing.settle_clearance(adm, cid)
    assert out["deductions_pkr"] == 15000 and out["refundable_pkr"] == 0 and out["additional_due_pkr"] == 6000
    assert billing.get_invoice(s1, "INV-202611-2024405")["balance_pkr"] == 6000.0
    assert db.query_one("SELECT COUNT(*) AS n FROM mess_deposit_transactions WHERE kind='refund'")["n"] == 0

def test_clearance_blocked_by_unverified_receipts(actors, at):
    no = _invoiced(actors, at)
    adm = actors["mess"]
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


def test_expenditure_is_one_record_per_month_and_corrections_keep_history(actors, at):
    adm = actors["mess"]
    billing.add_expense(adm, "2026-11", 1000, "food")
    with pytest.raises(Conflict) as e:
        billing.add_expense(adm, "2026-11", 2000, "duplicate")
    assert e.value.code == "expense_exists"
    with pytest.raises(ValidationFailed):
        billing.correct_expense(adm, "2026-11", 1500, "x")                                   # a real reason is mandatory
    with pytest.raises(Forbidden):
        billing.correct_expense(actors["s1"], "2026-11", 1500, "student must not do this")
    r = billing.correct_expense(adm, "2026-11", 1500, "Invoice from supplier corrected")
    assert r["total_food_expenditure_pkr"] == 1500
    rows = billing.list_expenses(adm, "2026-11")
    assert sorted(x["status"] for x in rows) == ["active", "superseded"] and {x["amount_pkr"] for x in rows} == {1000, 1500}
    assert db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE action='mess.expense_correct' AND outcome='success' AND detail::text LIKE %s", ("%supplier corrected%",))["n"] >= 1
    ov = {m["month"]: m for m in billing.monthly_overview(adm)}["2026-11"]
    assert ov["status"] == "recorded" and ov["revisions"] == 1 and ov["recorded_by"] == "Mess Office"
