"""Administration API. Every endpoint is guarded by the service layer's scope checks (backend-enforced RBAC)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app import db
from app.api.deps import current_actor
from app.services import audit, auth, catalog_import, courses, events, knowledge, reports, settings
from app.services.actor import Actor

router = APIRouter()


class CourseCreate(BaseModel):
    course_code: str
    title: str
    credits: int
    prerequisites: list[str] = []
    slot: str | None = None
    lab_slot: str | None = None
    capacity: int = 60
    category: str = "elective"
    instructor: str | None = None
    description: str | None = None
    domains: str | None = None


class CoursePatch(BaseModel):
    title: str | None = None
    credits: int | None = None
    prerequisites: list[str] | None = None
    slot: str | None = None
    lab_slot: str | None = None
    capacity: int | None = None
    category: str | None = None
    instructor: str | None = None
    description: str | None = None
    domains: str | None = None
    is_active: bool | None = None
    catalog_only: bool | None = None
    department: str | None = None


class GradeBody(BaseModel):
    student_id: str
    course_code: str
    grade: str | None = None
    term: str | None = None


class StudentActionBody(BaseModel):
    student_id: str


class UserCreate(BaseModel):
    user_id: str
    full_name: str
    email: str
    role: str
    scopes: list[str] = []
    password: str | None = Field(default=None, max_length=200)


class UserPatch(BaseModel):
    role: str | None = None
    scopes: list[str] | None = None
    is_active: bool | None = None
    full_name: str | None = None


class PasswordReset(BaseModel):
    password: str = Field(max_length=200)


class SettingBody(BaseModel):
    value: str = Field(max_length=100)


# ── Reporting ──
@router.get("/reports/overview")
def overview(actor: Actor = Depends(current_actor)):
    return reports.overview(actor)


# ── Courses ──
@router.get("/courses")
def list_courses(q: str | None = None, actor: Actor = Depends(current_actor)):
    return courses.list_courses(actor, q, include_inactive=True)


@router.post("/courses")
def create_course(body: CourseCreate, actor: Actor = Depends(current_actor)):
    data = body.model_dump()
    return courses.create_course(actor, data.pop("course_code"), data.pop("title"), data.pop("credits"), data.pop("prerequisites"), **data)


@router.put("/courses/{code}")
def update_course(code: str, body: CoursePatch, actor: Actor = Depends(current_actor)):
    data = body.model_dump(exclude_unset=True)
    prereqs = data.pop("prerequisites", None)
    return courses.update_course(actor, code, prerequisites=prereqs, **data)


@router.delete("/courses/{code}")
def delete_course(code: str, actor: Actor = Depends(current_actor)):
    return courses.delete_course(actor, code)


@router.get("/courses/{code}/students")
def course_students(code: str, actor: Actor = Depends(current_actor)):
    return courses.course_students(actor, code)


@router.post("/courses/{code}/register")
def admin_register(code: str, body: StudentActionBody, actor: Actor = Depends(current_actor)):
    actor.require_scope("academic")
    return courses.register(actor, code, body.student_id)


@router.post("/courses/{code}/drop")
def admin_drop(code: str, body: StudentActionBody, actor: Actor = Depends(current_actor)):
    actor.require_scope("academic")
    return courses.drop(actor, code, body.student_id)


@router.post("/grades")
def record_grade(body: GradeBody, actor: Actor = Depends(current_actor)):
    return courses.set_course_history(actor, body.student_id, body.course_code, body.grade, body.term)


@router.get("/students")
def students(q: str = "", actor: Actor = Depends(current_actor)):
    return courses.search_students(actor, q)


# ── Users & roles ──
@router.get("/users")
def users(role: str | None = None, q: str | None = None, actor: Actor = Depends(current_actor)):
    return auth.list_users(actor, role, q)


@router.post("/users")
def create_user(body: UserCreate, actor: Actor = Depends(current_actor)):
    return auth.create_user(actor, user_id=body.user_id, full_name=body.full_name, email=body.email, role=body.role,
                            password=body.password, scopes=body.scopes, must_change=True)


@router.put("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, actor: Actor = Depends(current_actor)):
    return auth.update_user(actor, user_id, **body.model_dump(exclude_unset=True))


@router.post("/users/{user_id}/password")
def reset_password(user_id: str, body: PasswordReset, actor: Actor = Depends(current_actor)):
    auth.set_password(actor, user_id, body.password, must_change=True)
    return {"ok": True}


# ── Settings, audit, events ──
@router.get("/settings")
def get_settings(actor: Actor = Depends(current_actor)):
    actor.require_role("admin")
    return settings.list_all()


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingBody, actor: Actor = Depends(current_actor)):
    return settings.update(actor, key, body.value)


@router.get("/audit")
def audit_log(limit: int = 100, offset: int = 0, actor_id: str | None = None, action: str | None = None, outcome: str | None = None,
              actor: Actor = Depends(current_actor)):
    actor.require_scope("super")
    return audit.list_entries(actor, limit=limit, offset=offset, actor_filter=actor_id, action=action, outcome=outcome)


@router.get("/events")
def recent_events(limit: int = 50, actor: Actor = Depends(current_actor)):
    actor.require_scope("super")
    return events.recent(limit)


@router.post("/events/retry")
def retry_events(actor: Actor = Depends(current_actor)):
    actor.require_scope("super")
    return events.retry_failed()


# ── Official GIKI data (knowledge base + course catalogue import) ──
@router.get("/knowledge/status")
def knowledge_status(actor: Actor = Depends(current_actor)):
    return knowledge.status(actor)


@router.get("/knowledge/sources")
def knowledge_sources(actor: Actor = Depends(current_actor)):
    actor.require_scope("academic")
    return knowledge.list_sources(actor)


@router.post("/knowledge/refresh")
def knowledge_refresh(force: bool = False, actor: Actor = Depends(current_actor)):
    """Re-crawl the official giki.edu.pk sources (same domain/robots restrictions as the CLI) in the background."""
    return knowledge.start_refresh_async(actor, force)


@router.get("/knowledge/flags")
def knowledge_flags(status: str | None = "open", actor: Actor = Depends(current_actor)):
    return catalog_import.list_flags(actor, status)


@router.post("/knowledge/flags/{flag_id}/{action}")
def resolve_flag(flag_id: int, action: str, actor: Actor = Depends(current_actor)):
    return catalog_import.resolve_flag(actor, flag_id, action)
