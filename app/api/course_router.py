"""Course Management API - thin HTTP layer over app.services.courses (the same service the AI assistant uses)."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app import db
from app.api.deps import current_actor
from app.services import curriculum, courses
from app.services.actor import Actor

router = APIRouter()


class StudentRef(BaseModel):
    student_id: str | None = Field(default=None, max_length=10, description="Administrators only: act for this student")


class WhatIfBody(BaseModel):
    minors: list[str] | None = None
    hypothetical_courses: list[str] | None = Field(default=None, max_length=20)


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class InterestBody(BaseModel):
    interests: str = Field(max_length=300)


@router.get("")
def list_courses(q: str | None = None, category: str | None = None, student_id: str | None = None, offered_only: bool = False, actor: Actor = Depends(current_actor)):
    """offered_only=true hides official-catalogue courses that are not scheduled this term."""
    return courses.list_courses(actor, q, category, False, student_id, offered_only)


@router.get("/me/registrations")
def my_registrations(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return courses.my_registrations(actor, student_id)


@router.get("/me/timetable")
def my_timetable(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return courses.timetable(actor, student_id)


@router.get("/me/record")
def academic_record(student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return courses.academic_record(actor, student_id)


@router.get("/me/recommendations")
def recommendations(interests: str | None = Query(default=None, max_length=300), actor: Actor = Depends(current_actor)):
    return courses.recommend_electives(actor, interests)


@router.put("/me/interests")
def set_interests(body: InterestBody, actor: Actor = Depends(current_actor)):
    actor.require_role("student")
    with db.tx() as cur:
        cur.execute("INSERT INTO student_profiles (user_id, interests) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET interests=EXCLUDED.interests",
                    (actor.user_id, body.interests.strip()))
    return {"ok": True, "interests": body.interests.strip()}


@router.post("/me/what-if")
def what_if(body: WhatIfBody, actor: Actor = Depends(current_actor)):
    return courses.what_if(actor, body.minors, body.hypothetical_courses)


@router.get("/minors")
def minors(actor: Actor = Depends(current_actor)):
    """Minors with their verification status: only status='official' ones are officially supported; the rest are team-authored and unverified."""
    return curriculum.official_minors()


@router.get("/programs")
def programs(actor: Actor = Depends(current_actor)):
    return curriculum.list_programs()


@router.get("/programs/{code}")
def program(code: str, track: str | None = None, actor: Actor = Depends(current_actor)):
    return curriculum.get_program(code, track)


@router.get("/me/degree-progress")
def degree_progress(program: str | None = None, track: str | None = None, actor: Actor = Depends(current_actor)):
    return curriculum.degree_progress(actor, None, program, track)


@router.post("/ask")
def ask_giki(body: AskBody, actor: Actor = Depends(current_actor)):
    return courses.ask_giki(body.question)


@router.get("/{code}")
def get_course(code: str, student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return courses.get_course(actor, code, student_id)


@router.get("/{code}/prerequisites")
def prerequisites(code: str, actor: Actor = Depends(current_actor)):
    return courses.prerequisite_chain(code)


@router.get("/{code}/check")
def check(code: str, student_id: str | None = None, actor: Actor = Depends(current_actor)):
    return {"prerequisites": courses.check_prerequisites(actor, code, student_id), "schedule": courses.check_conflicts(actor, code, student_id)}


@router.post("/{code}/register")
def register(code: str, body: StudentRef | None = None, actor: Actor = Depends(current_actor)):
    return courses.register(actor, code, body.student_id if body else None)


@router.post("/{code}/drop")
def drop(code: str, body: StudentRef | None = None, actor: Actor = Depends(current_actor)):
    return courses.drop(actor, code, body.student_id if body else None)
