"""Course Management tools - thin adapters over app.services.courses (identical rules to the web portal)."""
from app.agents.runtime import context, propose, tool
from app.services import courses


@tool
def get_available_courses(search: str = "", category: str = "") -> dict:
    """List active courses with credits, timetable slot, prerequisites, seats left and YOUR status for each
    (available / registered / completed / blocked_prerequisites / full).

    Args:
        search: Optional text to match against code, title or topics (e.g. 'machine learning').
        category: Optional category filter: core, elective, math, humanities, science.
    """
    rows = courses.list_courses(context().actor, search or None, category or None)
    return {"ok": True, "count": len(rows), "courses": [
        {k: r[k] for k in ("course_code", "title", "credits", "slot", "lab_slot", "category", "prerequisites", "seats_left", "instructor", "student_status", "missing_prerequisites") if k in r}
        for r in rows]}


@tool
def get_course_details(course_code: str) -> dict:
    """Full details for one course, including its prerequisite chain and your eligibility.

    Args:
        course_code: e.g. 'CS325'.
    """
    return {"ok": True, "course": courses.get_course(context().actor, course_code)}


@tool
def check_prerequisites(course_code: str) -> dict:
    """Check whether YOU satisfy the prerequisites of a course (uses your real completed-course record).

    Args:
        course_code: e.g. 'CS330'.
    """
    return {"ok": True, **courses.check_prerequisites(context().actor, course_code)}


@tool
def check_schedule_conflicts(course_code: str) -> dict:
    """Check whether adding a course would clash (lecture or lab) with YOUR currently registered courses.

    Args:
        course_code: Candidate course, e.g. 'CS412'.
    """
    return {"ok": True, **courses.check_conflicts(context().actor, course_code)}


@tool
def get_my_registrations() -> dict:
    """Your currently registered courses for the active term with total credit hours."""
    return {"ok": True, **courses.my_registrations(context().actor)}


@tool
def get_my_timetable() -> dict:
    """Your weekly timetable (lectures and labs) and any clashes between registered courses."""
    return {"ok": True, **courses.timetable(context().actor)}


@tool
def get_my_academic_record() -> dict:
    """Your completed courses with grades, CGPA and credits earned."""
    return {"ok": True, **courses.academic_record(context().actor)}


@tool
def register_for_course(course_code: str) -> dict:
    """Request registration for a course. This validates ALL registration rules (prerequisites, clashes, credit limit,
    seats) and then asks the user to confirm. It does NOT register until the user confirms.

    Args:
        course_code: e.g. 'CS325'.
    """
    code = (course_code or "").upper().strip()
    res = courses.register(context().actor, code, dry_run=True)
    return propose("register_for_course", {"course_code": code},
                   f"Register you for {code} - {res['title']} ({res['credits']} credits, {res['term']}). Total credits afterwards: {res['total_credits_after']}.")


@tool
def drop_course(course_code: str) -> dict:
    """Request to drop a registered course. Asks the user to confirm; nothing changes until they do.

    Args:
        course_code: e.g. 'CS325'.
    """
    code = (course_code or "").upper().strip()
    regs = {c["course_code"]: c for c in courses.my_registrations(context().actor)["courses"]}
    if code not in regs:
        return {"ok": False, "error": f"You are not registered for {code} this term."}
    return propose("drop_course", {"course_code": code}, f"Drop {code} - {regs[code]['title']} ({regs[code]['credits']} credits).")


@tool
def recommend_electives(interests: str = "") -> dict:
    """Recommend electives that match the student's interests and that they are eligible for.

    Args:
        interests: Free text interests (e.g. 'machine learning and robotics'). Leave empty to use the saved profile interests.
    """
    return {"ok": True, **courses.recommend_electives(context().actor, interests or None)}


@tool
def simulate_what_if_degree(minors: list[str] | None = None, hypothetical_courses: list[str] | None = None) -> dict:
    """What-if simulator: progress towards one or more minors, what remains, prerequisite blockers, and the effect of
    hypothetically completing extra courses. Changes nothing.

    Args:
        minors: Minor names, e.g. ['Artificial Intelligence']. Omit to evaluate all minors.
        hypothetical_courses: Course codes to assume completed, e.g. ['MT201'].
    """
    return {"ok": True, **courses.what_if(context().actor, minors or None, hypothetical_courses or None)}


@tool
def ask_giki_policy(question: str) -> dict:
    """Answer academic/regulatory questions from the official GIKI policy corpus (retrieval). Quote the returned text; do
    not invent regulations that were not retrieved.

    Args:
        question: The policy question in natural language.
    """
    return {"ok": True, **courses.ask_giki(question)}


COURSE_TOOLS = [get_available_courses, get_course_details, check_prerequisites, check_schedule_conflicts, get_my_registrations,
                get_my_timetable, get_my_academic_record, register_for_course, drop_course, recommend_electives,
                simulate_what_if_degree, ask_giki_policy]
