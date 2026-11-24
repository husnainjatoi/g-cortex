"""Mess Management API - thin HTTP layer over app.services.mess."""
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from app.api.deps import current_actor, gate_key_ok, optional_actor
from app.services import mess
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


class DecisionBody(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=255)


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


@router.get("/me/rebates")
def rebates(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.rebate_ledger(actor, student_id)


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
@router.get("/admin/mess-out")
def admin_list(status: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.list_mess_out(actor, status=status, all_students=True)


@router.post("/admin/mess-out/{request_id}/decision")
def admin_decide(request_id: int, body: DecisionBody, actor: Actor = Depends(current_actor)):
    return mess.decide_mess_out(actor, request_id, body.approve, body.note)


@router.get("/admin/attendance")
def attendance(date: str | None = None, meal: str | None = None, actor: Actor = Depends(current_actor)):
    return mess.attendance(actor, date, meal)


@router.put("/admin/menu/{meal}")
def update_menu(meal: str, body: MenuBody, actor: Actor = Depends(current_actor)):
    return mess.update_menu(actor, meal, **body.model_dump())


@router.get("/admin/summary")
def summary(actor: Actor = Depends(current_actor)):
    return mess.rebate_summary(actor)
