"""Requisition & Resource Management API - thin HTTP layer over requisitions / sports / equipment services."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import current_actor
from app.services import equipment, requisitions, societies as societies_svc, sports
from app.services.actor import Actor

router = APIRouter()


class RequisitionBody(BaseModel):
    reservation_type: str = Field(default="society", max_length=12)
    society_id: int | None = None
    society_name: str | None = Field(default=None, max_length=200)
    venue_id: str = Field(min_length=1, max_length=20)
    event_title: str = Field(min_length=1, max_length=150)
    booking_date: str = Field(min_length=8, max_length=10)
    start_time: str = Field(min_length=3, max_length=8)
    duration_hours: float = Field(gt=0, le=24)
    expected_attendees: int = Field(ge=1, le=10000)
    justification: str = Field(default="", max_length=1000)
    description: str = Field(default="", max_length=2000)


class ReviewBody(BaseModel):
    action: str
    comments: str = Field(default="", max_length=500)


class BookBody(BaseModel):
    facility_id: str = Field(max_length=20)
    date: str
    start_time: str
    duration_hours: float = Field(default=1.0, gt=0, le=8)


class CancelBody(BaseModel):
    reason: str = Field(default="", max_length=200)


class CheckinBody(BaseModel):
    pin: str = Field(max_length=20)


class RecommendBody(BaseModel):
    description: str = Field(min_length=3, max_length=500)
    venue_type: str = Field(default="", max_length=50)
    attendees: int = Field(ge=1, le=10000)
    facilities: str = Field(default="", max_length=200)
    date: str | None = None
    start_time: str | None = None
    duration_hours: float = 2.0


class CheckoutBody(BaseModel):
    purpose: str = Field(min_length=3, max_length=255)
    days: int | None = Field(default=None, ge=1, le=60)


class ReturnBody(BaseModel):
    condition: str = "good"
    note: str = Field(default="", max_length=255)


class VenueBody(BaseModel):
    venue_id: str
    name: str
    capacity: int
    category: str
    facilities: str = ""
    description: str = ""
    requires_approval: bool = True
    exclusive: bool = True


class VenuePatch(BaseModel):
    name: str | None = None
    capacity: int | None = None
    category: str | None = None
    facilities: str | None = None
    description: str | None = None
    requires_approval: bool | None = None
    is_active: bool | None = None
    exclusive: bool | None = None


class SocietyBody(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=50)
    advisor_id: str | None = Field(default=None, max_length=10)
    logo_url: str | None = Field(default=None, max_length=500)


class SocietyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=50)
    status: str | None = None
    advisor_id: str | None = Field(default=None, max_length=10)
    logo_url: str | None = Field(default=None, max_length=500)


class MemberBody(BaseModel):
    user_id: str = Field(min_length=3, max_length=10)
    role: str = "member"


class EquipmentBody(BaseModel):
    barcode: str
    name: str
    lab: str
    category: str = ""
    condition: str = "good"


class EquipmentPatch(BaseModel):
    name: str | None = None
    lab: str | None = None
    category: str | None = None
    condition: str | None = None
    status: str | None = None


# ── Venues & societies ──
@router.get("/societies")
def societies_list(actor: Actor = Depends(current_actor)):
    return {"all": societies_svc.list_societies(actor), "mine": societies_svc.my_societies(actor)}


@router.get("/admin/societies")
def admin_societies(q: str | None = None, actor: Actor = Depends(current_actor)):
    return societies_svc.list_societies(actor, include_inactive=True, q=q)


@router.post("/admin/societies")
def admin_create_society(body: SocietyBody, actor: Actor = Depends(current_actor)):
    return societies_svc.create_society(actor, body.name, body.description or "", body.category or "", body.advisor_id, body.logo_url or "")


@router.put("/admin/societies/{society_id}")
def admin_update_society(society_id: int, body: SocietyPatch, actor: Actor = Depends(current_actor)):
    return societies_svc.update_society(actor, society_id, **body.model_dump(exclude_none=True))


@router.post("/admin/societies/import")
def admin_import_societies(actor: Actor = Depends(current_actor)):
    """Re-import the official list from giki.edu.pk (same domain/robots restrictions as the knowledge crawler)."""
    return societies_svc.import_official(actor)


@router.get("/admin/societies/{society_id}/members")
def society_members(society_id: int, actor: Actor = Depends(current_actor)):
    return societies_svc.members(actor, society_id)


@router.post("/admin/societies/{society_id}/members")
def society_add_member(society_id: int, body: MemberBody, actor: Actor = Depends(current_actor)):
    return societies_svc.set_membership(actor, society_id, body.user_id, body.role)


@router.delete("/admin/societies/{society_id}/members/{user_id}")
def society_remove_member(society_id: int, user_id: str, actor: Actor = Depends(current_actor)):
    return societies_svc.set_membership(actor, society_id, user_id, None)


@router.get("/venues/{venue_id}")
def venue_detail(venue_id: str, actor: Actor = Depends(current_actor)):
    """Trusted lookup used to pre-fill the reservation form from a venue's database ID."""
    return requisitions.get_venue(actor, venue_id)


@router.get("/venues")
def venues(category: str | None = None, q: str | None = None, include_inactive: bool = False, actor: Actor = Depends(current_actor)):
    return requisitions.list_venues(actor, category, q, include_inactive)


@router.get("/venues/{venue_id}/availability")
def venue_availability(venue_id: str, date: str, actor: Actor = Depends(current_actor)):
    return requisitions.venue_availability(actor, venue_id, date)


@router.post("/venues/recommend")
def recommend(body: RecommendBody, actor: Actor = Depends(current_actor)):
    return requisitions.recommend_venue(actor, body.description, body.attendees, body.facilities, body.date, body.start_time, body.duration_hours, body.venue_type)


@router.post("/venues")
def create_venue(body: VenueBody, actor: Actor = Depends(current_actor)):
    return requisitions.create_venue(actor, **body.model_dump())


@router.put("/venues/{venue_id}")
def update_venue(venue_id: str, body: VenuePatch, actor: Actor = Depends(current_actor)):
    return requisitions.update_venue(actor, venue_id, **body.model_dump(exclude_none=True))


# ── Society requisitions ──
@router.post("/requisitions")
def submit(body: RequisitionBody, actor: Actor = Depends(current_actor)):
    return requisitions.submit_requisition(actor, **body.model_dump())


@router.get("/requisitions")
def list_requisitions(scope: str = "mine", status: str | None = None, actor: Actor = Depends(current_actor)):
    return requisitions.list_requisitions(actor, scope, status)


@router.get("/requisitions/{requisition_id}")
def get_requisition(requisition_id: str, actor: Actor = Depends(current_actor)):
    return requisitions.get_requisition(actor, requisition_id)


@router.post("/requisitions/{requisition_id}/review")
def review(requisition_id: str, body: ReviewBody, actor: Actor = Depends(current_actor)):
    return requisitions.review_requisition(actor, requisition_id, body.action, body.comments)


@router.post("/requisitions/{requisition_id}/cancel")
def cancel_requisition(requisition_id: str, actor: Actor = Depends(current_actor)):
    return requisitions.cancel_requisition(actor, requisition_id)


# ── Sports ──
@router.get("/sports/facilities")
def facilities(actor: Actor = Depends(current_actor)):
    return sports.facilities(actor)


@router.get("/sports/availability")
def availability(facility_id: str, date: str, actor: Actor = Depends(current_actor)):
    return sports.availability(actor, facility_id, date)


@router.get("/sports/quota")
def quota(date: str | None = None, student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return sports.quota(actor, student_id, date)


@router.get("/sports/bookings")
def my_bookings(upcoming: bool = False, student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return sports.my_bookings(actor, student_id, upcoming)


@router.post("/sports/bookings")
def book(body: BookBody, actor: Actor = Depends(current_actor)):
    return sports.book(actor, body.facility_id, body.date, body.start_time, body.duration_hours)


@router.post("/sports/bookings/{booking_ref}/cancel")
def cancel(booking_ref: str, body: CancelBody | None = None, actor: Actor = Depends(current_actor)):
    return sports.cancel(actor, booking_ref, body.reason if body else "")


@router.post("/sports/bookings/{booking_ref}/checkin")
def checkin(booking_ref: str, body: CheckinBody, actor: Actor = Depends(current_actor)):
    return sports.check_in(actor, booking_ref, body.pin)


@router.get("/admin/bookings")
def admin_bookings(date: str | None = None, venue_id: str | None = None, status: str | None = None, actor: Actor = Depends(current_actor)):
    return sports.list_bookings(actor, date, venue_id, status)


@router.post("/admin/auto-release")
def auto_release(actor: Actor = Depends(current_actor)):
    return sports.auto_release(actor)


# ── Equipment ──
@router.get("/equipment")
def list_equipment(status: str | None = None, lab: str | None = None, q: str | None = None, actor: Actor = Depends(current_actor)):
    return equipment.list_equipment(actor, status, lab, q)


@router.get("/equipment/mine")
def my_custody(actor: Actor = Depends(current_actor)):
    return equipment.my_custody(actor)


@router.get("/equipment/log")
def custody_log(barcode: str | None = None, actor: Actor = Depends(current_actor)):
    return equipment.custody_log(actor, barcode)


@router.get("/equipment/{barcode}")
def lookup(barcode: str, actor: Actor = Depends(current_actor)):
    return equipment.lookup(actor, barcode)


@router.post("/equipment/{barcode}/checkout")
def checkout(barcode: str, body: CheckoutBody, actor: Actor = Depends(current_actor)):
    return equipment.checkout(actor, barcode, body.purpose, body.days)


@router.post("/equipment/{barcode}/return")
def return_item(barcode: str, body: ReturnBody, actor: Actor = Depends(current_actor)):
    return equipment.return_equipment(actor, barcode, body.condition, body.note)


@router.post("/equipment")
def create_equipment(body: EquipmentBody, actor: Actor = Depends(current_actor)):
    return equipment.create_equipment(actor, **body.model_dump())


@router.put("/equipment/{barcode}")
def update_equipment(barcode: str, body: EquipmentPatch, actor: Actor = Depends(current_actor)):
    return equipment.update_equipment(actor, barcode, **body.model_dump(exclude_none=True))
