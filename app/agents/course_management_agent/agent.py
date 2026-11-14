"""
G-Cortex Course Management Agent
AI-powered academic advisor for GIKI built on Google ADK.
Provides tools for course catalog queries, prerequisite DAG traversal,
enrollment eligibility verification, and university policy Q&A.
"""
import os
import psycopg2
from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost/gcortex_db",
)


# ═══════════════════════════════════════════════════════════════
#  Database helper
# ═══════════════════════════════════════════════════════════════

def _get_db():
    """Return a fresh psycopg2 connection to gcortex_db."""
    url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(url)


# ═══════════════════════════════════════════════════════════════
#  Tool 1 — List all courses
# ═══════════════════════════════════════════════════════════════

def list_all_courses() -> dict:
    """Lists every course in the GIKI course catalog with its code, title, and credit hours."""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT course_code, title, credits FROM courses ORDER BY course_code")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    courses = [{"code": r[0], "title": r[1], "credits": r[2]} for r in rows]
    return {"courses": courses, "total_courses": len(courses)}


# ═══════════════════════════════════════════════════════════════
#  Tool 2 — Get course details + direct prerequisites
# ═══════════════════════════════════════════════════════════════

def get_course_details(course_code: str) -> dict:
    """Gets detailed information about a specific course including its direct prerequisites.

    Args:
        course_code: The course code to look up (e.g. 'CS232').
    """
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT course_code, title, credits FROM courses WHERE course_code = %s",
        (course_code.upper(),),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": f"Course '{course_code}' not found in the catalog."}

    cur.execute(
        """
        SELECT c.course_code, c.title
        FROM course_prerequisites cp
        JOIN courses c ON c.course_code = cp.prereq_code
        WHERE cp.course_code = %s
        """,
        (course_code.upper(),),
    )
    prereqs = [{"code": r[0], "title": r[1]} for r in cur.fetchall()]

    cur.execute(
        """
        SELECT c.course_code, c.title
        FROM course_prerequisites cp
        JOIN courses c ON c.course_code = cp.course_code
        WHERE cp.prereq_code = %s
        """,
        (course_code.upper(),),
    )
    unlocks = [{"code": r[0], "title": r[1]} for r in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "course_code": row[0],
        "title": row[1],
        "credits": row[2],
        "direct_prerequisites": prereqs,
        "unlocks_courses": unlocks,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 3 — Full prerequisite DAG chain
# ═══════════════════════════════════════════════════════════════

def get_prerequisite_chain(course_code: str) -> dict:
    """Returns the full prerequisite dependency tree (DAG) for a course,
    showing all transitive prerequisites recursively.

    Args:
        course_code: The course code to trace prerequisites for (e.g. 'CS325').
    """
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT title FROM courses WHERE course_code = %s",
        (course_code.upper(),),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": f"Course '{course_code}' not found in the catalog."}

    def _walk(code: str, visited: set | None = None) -> list[dict]:
        if visited is None:
            visited = set()
        if code in visited:
            return []
        visited.add(code)
        cur.execute(
            """
            SELECT cp.prereq_code, c.title
            FROM course_prerequisites cp
            JOIN courses c ON c.course_code = cp.prereq_code
            WHERE cp.course_code = %s
            """,
            (code,),
        )
        return [
            {
                "course_code": r[0],
                "title": r[1],
                "prerequisites": _walk(r[0], visited),
            }
            for r in cur.fetchall()
        ]

    chain = _walk(course_code.upper())
    cur.close()
    conn.close()

    return {
        "course_code": course_code.upper(),
        "title": row[0],
        "prerequisite_chain": chain,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 4 — Enrollment eligibility check
# ═══════════════════════════════════════════════════════════════

def check_enrollment_eligibility(course_code: str, completed_courses: list[str]) -> dict:
    """Checks whether a student can enroll in a course based on their completed courses.
    Performs a full BFS traversal of the prerequisite DAG to find ALL transitive
    prerequisites and verifies every one has been completed.

    Args:
        course_code: The course the student wants to enroll in (e.g. 'CS325').
        completed_courses: List of course codes the student has already passed (e.g. ['CS101', 'CS232']).
    """
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT title FROM courses WHERE course_code = %s",
        (course_code.upper(),),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": f"Course '{course_code}' not found in the catalog."}

    completed_upper = {c.upper() for c in completed_courses}

    # BFS to collect every prerequisite in the DAG
    all_required: set[str] = set()
    queue = [course_code.upper()]
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        cur.execute(
            "SELECT prereq_code FROM course_prerequisites WHERE course_code = %s",
            (current,),
        )
        for (prereq,) in cur.fetchall():
            all_required.add(prereq)
            queue.append(prereq)

    cur.close()
    conn.close()

    met = sorted(all_required & completed_upper)
    missing = sorted(all_required - completed_upper)
    can_enroll = len(missing) == 0

    return {
        "course_code": course_code.upper(),
        "course_title": row[0],
        "can_enroll": can_enroll,
        "all_required_prerequisites": sorted(all_required),
        "completed_prerequisites": met,
        "missing_prerequisites": missing,
        "message": (
            f"✅ You meet all prerequisites for {course_code.upper()}! You are eligible to enroll."
            if can_enroll
            else f"❌ You cannot enroll in {course_code.upper()} yet. You still need to complete: {', '.join(missing)}"
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 5 — "Ask GIKI" Policy Engine (RAG-lite)
# ═══════════════════════════════════════════════════════════════

_GIKI_POLICIES = {
    "credit_limit": (
        "Students can register for a minimum of 12 and a maximum of 21 credit hours "
        "per semester. Overload requests (up to 24 credits) require a CGPA of 3.5+ "
        "and the Dean's written approval."
    ),
    "prerequisites": (
        "A student must pass all prerequisite courses with a minimum grade of D before "
        "enrolling in the dependent course. Concurrent enrollment in a course and its "
        "prerequisite is NOT permitted."
    ),
    "drop_policy": (
        "Courses can be dropped without academic penalty (W grade) until the 8th week "
        "of the semester. After week 8, a dropped course receives a WF (Withdraw Fail) "
        "grade which counts as an F in CGPA calculation."
    ),
    "repeat_policy": (
        "Students may repeat a failed course. Both attempts appear on the transcript, "
        "but only the higher grade is counted in the CGPA calculation. A course can be "
        "repeated a maximum of two times."
    ),
    "probation": (
        "A student whose semester GPA falls below 2.0 is placed on academic probation. "
        "Two consecutive semesters on probation may result in dismissal from the program."
    ),
    "attendance": (
        "A minimum of 80%% attendance is mandatory for all courses. Students falling "
        "below 75%% attendance are debarred from the final examination and automatically "
        "receive an F grade."
    ),
    "mess_rebate": (
        "Students must apply for mess rebate at least 24 hours before the meal. Rebates "
        "are only applicable for leaves of 3 or more consecutive days. Emergency rebates "
        "require the hostel warden's written approval."
    ),
    "venue_booking": (
        "Campus venues can be booked through the requisition system. Bookings for "
        "auditoriums and labs require faculty advisor approval. Sports facilities are "
        "available on a first-come-first-served basis with a maximum of 2 hours per day "
        "per student."
    ),
    "library": (
        "Library books can be borrowed for 14 days with one renewal allowed. Overdue "
        "books incur a fine of Rs. 10 per day. Reference books cannot be taken outside "
        "the library."
    ),
    "hostel": (
        "All undergraduate students are required to reside in university hostels. Room "
        "changes are permitted only at the start of each semester with hostel admin approval."
    ),
    "grading_scale": (
        "GIKI uses a 4.0 GPA scale: A (4.0), A- (3.67), B+ (3.33), B (3.0), B- (2.67), "
        "C+ (2.33), C (2.0), C- (1.67), D+ (1.33), D (1.0), F (0.0). A minimum CGPA "
        "of 2.0 is required for graduation."
    ),
    "electives": (
        "Students must complete a minimum of 4 free electives and 2 university electives "
        "to fulfil degree requirements. Elective selection opens two weeks before the "
        "start of each semester on a first-come-first-served basis."
    ),
}


def ask_giki_policy(question: str) -> dict:
    """Answers questions about GIKI university policies, academic rules, and regulations
    by searching the policy knowledge base.

    Args:
        question: The student's question about a GIKI policy or rule.
    """
    question_lower = question.lower()
    relevant = []

    for topic, policy_text in _GIKI_POLICIES.items():
        topic_words = set(topic.replace("_", " ").split())
        policy_words_sample = set(policy_text.lower().split()[:15])
        all_keywords = topic_words | policy_words_sample

        if any(kw in question_lower for kw in all_keywords if len(kw) > 3):
            relevant.append(
                {"topic": topic.replace("_", " ").title(), "policy": policy_text}
            )

    if not relevant:
        available = ", ".join(
            t.replace("_", " ").title() for t in _GIKI_POLICIES.keys()
        )
        return {
            "answer": (
                "I couldn't find a specific policy matching your question. "
                f"Available policy topics: {available}"
            ),
            "policies_found": 0,
        }

    return {
        "answer": "Here are the relevant GIKI policies I found:",
        "policies": relevant,
        "policies_found": len(relevant),
    }


# ═══════════════════════════════════════════════════════════════
#  Agent Definition
# ═══════════════════════════════════════════════════════════════

_INSTRUCTION = """\
You are the **G-Cortex Course Management Agent** — an AI-powered academic advisor
for GIKI (Ghulam Ishaq Khan Institute of Engineering Sciences and Technology).

## Your capabilities (use the right tool for each query):

1. **Course Catalog** (`list_all_courses`):
   List every available course with code, title, and credit hours.

2. **Course Details** (`get_course_details`):
   Show full info for one course including direct prerequisites and what it unlocks.

3. **Prerequisite DAG Engine** (`get_prerequisite_chain`):
   Trace the entire dependency tree for a course — shows every transitive prerequisite.

4. **Enrollment Eligibility** (`check_enrollment_eligibility`):
   Given a student's completed courses, determine whether they can enroll in a target
   course by walking the full prerequisite graph. Always ask the student which courses
   they have completed if they haven't told you.

5. **GIKI Policy Engine** (`ask_giki_policy`):
   Answer questions about university academic policies, rules, and regulations.

## Behaviour guidelines:
- When a student asks "Can I take X?", ALWAYS use `check_enrollment_eligibility`.
  Ask them which courses they have completed first if they haven't mentioned any.
- When explaining prerequisites, use `get_prerequisite_chain` for the full picture.
- Present results with clear ✅/❌ indicators.
- If a student is missing prerequisites, proactively suggest what they should take first.
- Be concise, helpful, and friendly. You represent GIKI's academic advisory system.
"""

root_agent = Agent(
    model="gemini-3.5-flash-lite",
    name="course_management_agent",
    description=(
        "AI-powered Course Management Agent for GIKI. Handles course catalog queries, "
        "prerequisite DAG traversal, enrollment eligibility checks, and university policy questions."
    ),
    instruction=_INSTRUCTION,
    tools=[
        list_all_courses,
        get_course_details,
        get_prerequisite_chain,
        check_enrollment_eligibility,
        ask_giki_policy,
    ],
)
