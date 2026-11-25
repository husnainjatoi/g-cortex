"""Course Management tools - thin adapters over app.services.courses (identical rules to the web portal)."""
from app.agents.runtime import context, propose, tool
from app.services import courses, curriculum


@tool
def get_available_courses(search: str = "", category: str = "", include_unscheduled: bool = False) -> dict:
    """List courses with credits, timetable slot, prerequisites, seats left and YOUR status for each
    (available / registered / completed / blocked_prerequisites / full / not_offered). By default only courses scheduled this
    term are returned; set include_unscheduled=true to also see the wider official GIKI catalogue (those cannot be registered yet).

    Args:
        search: Optional text to match against code, title or topics (e.g. 'machine learning').
        category: Optional category filter: core, elective, math, humanities, science.
        include_unscheduled: Include official-catalogue courses that are not offered this term.
    """
    rows = courses.list_courses(context().actor, search or None, category or None, offered_only=not include_unscheduled)
    return {"ok": True, "count": len(rows), "courses": [
        {k: r[k] for k in ("course_code", "title", "credits", "slot", "lab_slot", "category", "prerequisites", "seats_left", "instructor", "student_status", "missing_prerequisites", "department") if k in r}
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
        minors: Minor names, e.g. ['Artificial Intelligence']. Omit to evaluate the officially supported minors (unverified minors are only evaluated when named, and are labelled unverified).
        hypothetical_courses: Course codes to assume completed, e.g. ['MT201'].
    """
    return {"ok": True, **courses.what_if(context().actor, minors or None, hypothetical_courses or None)}


@tool
def list_degree_programmes() -> dict:
    """List GIKI's undergraduate degree programmes (from the official Undergraduate Prospectus 2025), their tracks, and the officially supported minors.
    Unverified minors are labelled and must not be presented as official offerings."""
    return {"ok": True, "programmes": curriculum.list_programs(), "minors": curriculum.official_minors()}


@tool
def get_degree_plan(programme: str, track: str = "") -> dict:
    """Semester-wise curriculum of one degree programme exactly as printed in the official prospectus (courses, credit hours, prerequisites).

    Args:
        programme: Programme code or name, e.g. 'BSCE' or 'BS Computer Engineering'.
        track: Optional track/stream for programmes whose semesters 5-8 differ per track.
    """
    return {"ok": True, **curriculum.get_program(programme, track or None)}


@tool
def get_my_degree_progress(programme: str = "", track: str = "") -> dict:
    """The student's progress through a programme's official curriculum: completed / in-progress / eligible / blocked / remaining courses and credit hours.
    Leave programme empty for the student's own programme; pass another programme to see a what-if for switching.

    Args:
        programme: Optional programme code or name.
        track: Optional track.
    """
    return {"ok": True, **curriculum.degree_progress(context().actor, None, programme or None, track or None)}


@tool
def ask_giki_policy(question: str) -> dict:
    """Answer academic/regulatory questions from OFFICIAL GIKI documents (giki.edu.pk: Student Handbook, prospectus, calendar...) via retrieval.
    Base the answer ONLY on the returned passages, cite the source title/section/page and URL given, and if found=false say plainly that the
    official documents indexed do not answer it. Never state a policy that was not retrieved.

    Args:
        question: The policy question in natural language.
    """
    return {"ok": True, **courses.ask_giki(question)}


COURSE_TOOLS = [get_available_courses, get_course_details, check_prerequisites, check_schedule_conflicts, get_my_registrations,
                get_my_timetable, get_my_academic_record, register_for_course, drop_course, recommend_electives,
                simulate_what_if_degree, list_degree_programmes, get_degree_plan, get_my_degree_progress, ask_giki_policy]
