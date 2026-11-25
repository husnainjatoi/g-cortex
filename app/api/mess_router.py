"""Mess Management API - thin HTTP layer over app.services.mess and app.services.billing (monthly billing, deposit)."""
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from app.api.deps import current_actor, gate_key_ok, optional_actor
from app.services import billing, mess
from app.services.actor import Actor

router = APIRouter()


class MessOutBody(BaseModel):
    start_date: str = Field(min_length=8, max_length=10)
    end_date: str = Field(min_length=8, max_length=10)
    reason: str | None = Field(default=None, max_length=255)
    student_id: str | None = Field(default=None, max_length=10)


class AllergyBody(BaseModel):
    allergies: str = Field(max_length=200)


class ScanBody(BaseModel):
    payload: str = Field(min_length=3, max_length=200)
    meal: str | None = None


class GatePassBody(BaseModel):
    user_id: str = Field(max_length=10)
    leave_start: str
    leave_end: str
    destination: str | None = Field(default=None, max_length=100)
    approved: bool = True
    external_ref: str | None = Field(default=None, max_length=60)


class MenuBody(BaseModel):
    items: str
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    allergens: str = "none"


@router.get("/menu")
def menu(actor: Actor = Depends(current_actor)):
    return mess.menu(actor)


@router.get("/me/profile")
def profile(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.profile(actor, student_id)


@router.put("/me/allergies")
def allergies(body: AllergyBody, actor: Actor = Depends(current_actor)):
    return mess.set_allergies(actor, body.allergies)


@router.get("/me/gate-passes")
def gate_passes(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.my_gate_passes(actor, student_id)


@router.get("/me/meals")
def meals(days: int = Query(30, ge=1, le=180), student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.meal_records(actor, student_id, days)


@router.post("/mess-out/preview")
def preview(body: MessOutBody, actor: Actor = Depends(current_actor)):
    return mess.preview_mess_out(actor, body.start_date, body.end_date, body.student_id)


@router.post("/mess-out")
def submit(body: MessOutBody, actor: Actor = Depends(current_actor)):
    return mess.submit_mess_out(actor, body.start_date, body.end_date, body.reason, body.student_id)


@router.get("/mess-out")
def list_mine(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.list_mess_out(actor, student_id)


@router.post("/mess-out/{request_id}/cancel")
def cancel(request_id: int, actor: Actor = Depends(current_actor)):
    return mess.cancel_mess_out(actor, request_id)


@router.get("/qr")
def qr(meal: str | None = None, actor: Actor = Depends(current_actor)):
    """Short-lived TOTP QR for dining entry. Call again to refresh."""
    return mess.generate_qr(actor, meal)


@router.post("/verify-entry")
def verify_entry(body: ScanBody, actor: Actor | None = Depends(optional_actor), gate_ok: bool = Depends(gate_key_ok)):
    """Turnstile scan: Mess Office staff session OR the gate controller's X-Gate-Key."""
    return mess.verify_entry(actor, body.payload, body.meal, gate_ok)


@router.post("/gate-pass-sync")
def gate_pass_sync(body: GatePassBody, actor: Actor | None = Depends(optional_actor), gate_ok: bool = Depends(gate_key_ok)):
    """Integration point for the campus security gate controller (REST push)."""
    if not gate_ok and actor is None:
        from app.services.errors import Unauthenticated
        raise Unauthenticated("Gate controller key or administrator session required.")
    return mess.sync_gate_pass(None if gate_ok else actor, **body.model_dump())


@router.get("/forecast")
def forecast(meal: str, date: str | None = None, day_type: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.forecast(actor, meal, date, day_type)


# ── Mess Office administration ──
@router.get("/admin/attendance")
def attendance(date: str | None = None, meal: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.attendance(actor, date, meal)


@router.put("/admin/menu/{meal}")
def update_menu(meal: str, body: MenuBody, actor: Actor = Depends(current_actor)):
    return mess.update_menu(actor, meal, **body.model_dump())




# ── Monthly billing (student) ──
class PaymentBody(BaseModel):
    amount_pkr: float = Field(gt=0, le=10000000)
    reference: str = Field(min_length=4, max_length=80)
    method: str = "bank_challan"
    note: str = Field(default="", max_length=255)
    challan_no: str | None = Field(default=None, max_length=40)


@router.get("/me/bill")
def my_bill(month: str | None = None, student_id: str | None = None, actor: Actor = Depends(current_actor)):
    """Estimated bill for the (current) month, or the finalized invoice if the month has been invoiced."""
    return billing.estimate_bill(actor, student_id, month)


@router.get("/me/bill/meals")
def my_bill_meals(month: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.meals_detail(actor, None, month)


@router.get("/me/invoices")
def my_invoices(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.my_invoices(actor, student_id)


@router.get("/invoices/{invoice_no}")
def invoice(invoice_no: str, actor: Actor = Depends(current_actor)):
    return billing.get_invoice(actor, invoice_no)


@router.post("/invoices/{invoice_no}/challan")
def challan(invoice_no: str, actor: Actor = Depends(current_actor)):
    return billing.generate_challan(actor, invoice_no)


@router.post("/invoices/{invoice_no}/payments")
def submit_payment(invoice_no: str, body: PaymentBody, actor: Actor = Depends(current_actor)):
    return billing.submit_payment(actor, invoice_no, body.amount_pkr, body.reference, body.method, body.note, body.challan_no)


@router.get("/me/payments")
def my_payments(actor: Actor = Depends(current_actor)):
    return billing.list_payments(actor, None)


@router.get("/me/deposit")
def my_deposit(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.deposit_summary(actor, student_id)


@router.get("/me/clearance/preview")
def clearance_preview(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.clearance_preview(actor, student_id)


@router.post("/me/clearance")
def request_clearance(actor: Actor = Depends(current_actor)):
    return billing.request_clearance(actor)


# ── Mess Office: rates, invoicing, payments, deposits, clearance ──
class RateBody(BaseModel):
    meal_type: str
    rate_pkr: float = Field(ge=0, le=100000)
    effective_from: str | None = None


class GenerateBody(BaseModel):
    month: str | None = None


class AdjustBody(BaseModel):
    amount_pkr: float
    reason: str = Field(min_length=5, max_length=255)


class VerifyBody(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=255)


class DepositPaymentBody(BaseModel):
    amount_pkr: float = Field(gt=0, le=10000000)
    reference: str = Field(min_length=4, max_length=80)
    verified: bool = False
    note: str = Field(default="", max_length=255)


class ReturnBody(BaseModel):
    return_reference: str = Field(min_length=4, max_length=80)


@router.get("/admin/rates")
def get_rates(actor: Actor = Depends(current_actor)):
    actor.require_role("admin")
    return billing.current_rates(actor)


@router.put("/admin/rates")
def set_rate(body: RateBody, actor: Actor = Depends(current_actor)):
    return billing.set_rate(actor, body.meal_type, body.rate_pkr, body.effective_from)


@router.post("/admin/invoices/generate")
def generate_invoices(body: GenerateBody, actor: Actor = Depends(current_actor)):
    return billing.generate_monthly_invoices(actor, body.month)


@router.get("/admin/invoices")
def admin_invoices(month: str | None = None, status: str | None = None, student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.list_invoices(actor, month, status, student_id)


@router.post("/admin/invoices/{invoice_no}/adjust")
def adjust(invoice_no: str, body: AdjustBody, actor: Actor = Depends(current_actor)):
    return billing.adjust_invoice(actor, invoice_no, body.amount_pkr, body.reason)


@router.get("/admin/payments")
def admin_payments(status: str | None = "submitted", student_id: str | None = None, actor: Actor = Depends(current_actor)):
    actor.require_scope("mess")
    return billing.list_payments(actor, status or None, student_id)


@router.post("/admin/payments/{payment_id}/verify")
def verify_payment(payment_id: int, body: VerifyBody, actor: Actor = Depends(current_actor)):
    return billing.verify_payment(actor, payment_id, body.approve, body.note)


@router.get("/admin/dues")
def dues(actor: Actor = Depends(current_actor)):
    return billing.outstanding_dues(actor)


@router.get("/admin/summary")
def summary(actor: Actor = Depends(current_actor)):
    return billing.billing_summary(actor)


@router.get("/admin/deposits")
def deposits(status: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.deposits_overview(actor, status)


@router.get("/admin/deposits/pending")
def pending_deposits(actor: Actor = Depends(current_actor)):
    return billing.pending_deposit_txns(actor)


@router.get("/admin/deposits/{student_id}")
def deposit_of(student_id: str, actor: Actor = Depends(current_actor)):
    actor.require_scope("mess")
    return billing.deposit_summary(actor, student_id)


@router.post("/admin/deposits/{student_id}/payments")
def record_deposit(student_id: str, body: DepositPaymentBody, actor: Actor = Depends(current_actor)):
    return billing.record_deposit_payment(actor, student_id, body.amount_pkr, body.reference, body.verified, body.note)


@router.post("/admin/deposit-txns/{txn_id}/verify")
def verify_deposit(txn_id: int, body: VerifyBody, actor: Actor = Depends(current_actor)):
    return billing.verify_deposit_txn(actor, txn_id, body.approve, body.note)


@router.get("/admin/clearances")
def clearances(status: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.list_clearances(actor, status)


@router.get("/admin/clearances/preview/{student_id}")
def admin_preview(student_id: str, actor: Actor = Depends(current_actor)):
    actor.require_scope("mess")
    return billing.clearance_preview(actor, student_id)


@router.post("/admin/clearances/{clearance_id}/review")
def review_clearance(clearance_id: int, body: VerifyBody, actor: Actor = Depends(current_actor)):
    return billing.review_clearance(actor, clearance_id, body.approve, body.note)


@router.post("/admin/clearances/{clearance_id}/settle")
def settle_clearance(clearance_id: int, actor: Actor = Depends(current_actor)):
    return billing.settle_clearance(actor, clearance_id)


@router.post("/admin/clearances/{clearance_id}/return")
def return_refund(clearance_id: int, body: ReturnBody, actor: Actor = Depends(current_actor)):
    return billing.mark_refund_returned(actor, clearance_id, body.return_reference)


class ExpenseBody(BaseModel):
    month: str = Field(min_length=7, max_length=10)
    amount_pkr: float = Field(gt=0, le=10000000000)
    description: str = Field(default="", max_length=200)


@router.get("/admin/expenses")
def expenses(month: str | None = None, actor: Actor = Depends(current_actor)):
    return billing.list_expenses(actor, month)


@router.post("/admin/expenses")
def add_expense(body: ExpenseBody, actor: Actor = Depends(current_actor)):
    return billing.add_expense(actor, body.month, body.amount_pkr, body.description)


@router.get("/admin/cost-summary")
def cost_summary(month: str | None = None, actor: Actor = Depends(current_actor)):
    actor.require_scope("mess")
    return billing.month_cost_summary(actor, month)


class ExpenseCorrection(BaseModel):
    amount_pkr: float = Field(gt=0, le=10000000000)
    reason: str = Field(min_length=5, max_length=300)


@router.put("/admin/expenses/{month}")
def correct_expense(month: str, body: ExpenseCorrection, actor: Actor = Depends(current_actor)):
    return billing.correct_expense(actor, month, body.amount_pkr, body.reason)


@router.get("/admin/expenses-overview")
def expenses_overview(actor: Actor = Depends(current_actor)):
    return billing.monthly_overview(actor)
