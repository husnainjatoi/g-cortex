"""
Course Management API Router
Provides REST endpoints for course catalog, prerequisite DAG traversal,
and enrollment eligibility checking.
"""
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import SessionLocal
from pydantic import BaseModel


router = APIRouter()


def get_db():
    """FastAPI dependency that provides a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schemas ───────────────────────────────────────────────────

class CourseOut(BaseModel):
    course_code: str
    title: str
    credits: int

    class Config:
        from_attributes = True


class PrereqNode(BaseModel):
    course_code: str
    title: str
    prerequisites: list["PrereqNode"] = []


class DAGResponse(BaseModel):
    course_code: str
    title: str
    prerequisite_chain: list[PrereqNode]


class EnrollmentCheck(BaseModel):
    completed_courses: list[str] = ["CS101", "CS102", "CS201", "CS232"]


class EnrollmentResult(BaseModel):
    course_code: str
    course_title: str
    can_enroll: bool
    completed_prerequisites: list[str]
    missing_prerequisites: list[str]
    message: str


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT course_code, title, credits FROM courses ORDER BY course_code")).fetchall()
    return [{"course_code": r[0], "title": r[1], "credits": r[2]} for r in rows]


@router.get("/{course_code}", response_model=CourseOut)
def get_course(course_code: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT course_code, title, credits FROM courses WHERE course_code = :code"),
        {"code": course_code.upper()}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Course '{course_code}' not found in the catalog")
    return {"course_code": row[0], "title": row[1], "credits": row[2]}


@router.get("/{course_code}/prerequisites", response_model=DAGResponse)
def get_prerequisite_chain(course_code: str, db: Session = Depends(get_db)):
    course = db.execute(
        text("SELECT course_code, title FROM courses WHERE course_code = :code"),
        {"code": course_code.upper()}
    ).fetchone()
    if not course:
        raise HTTPException(status_code=404, detail=f"Course '{course_code}' not found in the catalog")

    def _build_chain(code: str, visited: set | None = None) -> list[dict]:
        if visited is None:
            visited = set()
        if code in visited:
            return []
        visited.add(code)

        prereqs = db.execute(
            text("""
                SELECT c.course_code, c.title
                FROM course_prerequisites cp
                JOIN courses c ON c.course_code = cp.prereq_code
                WHERE cp.course_code = :code
            """),
            {"code": code}
        ).fetchall()

        return [
            {
                "course_code": p[0],
                "title": p[1],
                "prerequisites": _build_chain(p[0], visited),
            }
            for p in prereqs
        ]

    return {
        "course_code": course[0],
        "title": course[1],
        "prerequisite_chain": _build_chain(course_code.upper()),
    }


@router.post("/{course_code}/can-enroll", response_model=EnrollmentResult)
def check_enrollment(course_code: str, body: EnrollmentCheck, db: Session = Depends(get_db)):
    course = db.execute(
        text("SELECT course_code, title FROM courses WHERE course_code = :code"),
        {"code": course_code.upper()}
    ).fetchone()
    if not course:
        raise HTTPException(status_code=404, detail=f"Course '{course_code}' not found in the catalog")

    completed_upper = {c.upper().strip() for c in body.completed_courses}

    # BFS to collect all required prerequisites
    all_required: set[str] = set()
    queue = [course_code.upper()]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        rows = db.execute(
            text("SELECT prereq_code FROM course_prerequisites WHERE course_code = :code"),
            {"code": current}
        ).fetchall()
        for (prereq,) in rows:
            all_required.add(prereq)
            queue.append(prereq)

    met = sorted(all_required & completed_upper)
    missing = sorted(all_required - completed_upper)
    can_enroll = len(missing) == 0

    return EnrollmentResult(
        course_code=course[0],
        course_title=course[1],
        can_enroll=can_enroll,
        completed_prerequisites=met,
        missing_prerequisites=missing,
        message=(
            f"✅ You meet all prerequisites for {course[0]}! You are eligible to enroll."
            if can_enroll
            else f"❌ Cannot enroll in {course[0]}. Missing prerequisites: {', '.join(missing)}"
        ),
    )
