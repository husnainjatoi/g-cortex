"""
G-Cortex Course Management Subagent
AI-powered academic advisor for GIKI built on Google ADK.
Directly interfaces with the PostgreSQL database for catalog queries,
prerequisite DAG traversal, enrollment eligibility verification, timetable
conflict checks, degree/minor simulations, and policy retrieval.
"""
import os
import logging
from pathlib import Path

# Resilient imports for dotenv and google-adk
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return True

try:
    from google.adk.agents.llm_agent import Agent
except ImportError:
    try:
        from google.adk.agents import Agent
    except ImportError:
        class Agent:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)

_AGENT_DIR = Path(__file__).resolve().parent
_ENV_FILE = _AGENT_DIR / ".env"
if _ENV_FILE.exists():
    load_dotenv(dotenv_path=_ENV_FILE)
else:
    load_dotenv()

logger = logging.getLogger("g_cortex.course_agent")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost/gcortex_db",
)

# ═══════════════════════════════════════════════════════════════
#  Cross-Subagent Event Bus Integration
# ═══════════════════════════════════════════════════════════════
try:
    from app.core.event_bus import event_bus, COURSE_ENROLLED, COURSE_DROPPED
except ImportError:
    event_bus = None
    COURSE_ENROLLED = "course.enrolled"
    COURSE_DROPPED = "course.dropped"


def _publish_event(event_type: str, data: dict):
    if event_bus:
        try:
            event_bus.publish(event_type, data)
            logger.info("Published %s: %s", event_type, data)
        except Exception as exc:
            logger.warning("Event bus publication failed for %s: %s", event_type, exc)


# ═══════════════════════════════════════════════════════════════
#  Database Connection Helper (Direct Database Access)
# ═══════════════════════════════════════════════════════════════

def _get_db():
    """Return a fresh psycopg2 connection to gcortex_db."""
    import psycopg2
    url = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(url, connect_timeout=5)
    return conn


# ═══════════════════════════════════════════════════════════════
#  Tool 1 — List all courses
# ═══════════════════════════════════════════════════════════════

def list_all_courses() -> dict:
    """Lists every course in the GIKI course catalog directly from the database with its code, title, and credit hours."""
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
    """Gets detailed information about a specific course including its direct prerequisites from the database.

    Args:
        course_code: The course code to look up (e.g. 'CS232').
    """
    code_up = course_code.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT course_code, title, credits FROM courses WHERE course_code = %s",
        (code_up,),
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
        (code_up,),
    )
    prereqs = [{"code": r[0], "title": r[1]} for r in cur.fetchall()]

    cur.execute(
        """
        SELECT c.course_code, c.title
        FROM course_prerequisites cp
        JOIN courses c ON c.course_code = cp.course_code
        WHERE cp.prereq_code = %s
        """,
        (code_up,),
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
    """Returns the full prerequisite dependency tree (DAG) for a course from the database,
    showing all transitive prerequisites recursively.

    Args:
        course_code: The course code to trace prerequisites for (e.g. 'CS325').
    """
    code_up = course_code.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT title FROM courses WHERE course_code = %s",
        (code_up,),
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

    chain = _walk(code_up)
    cur.close()
    conn.close()

    return {
        "course_code": code_up,
        "title": row[0],
        "prerequisite_chain": chain,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 4 — Enrollment eligibility check
# ═══════════════════════════════════════════════════════════════

def check_enrollment_eligibility(course_code: str, completed_courses: list[str]) -> dict:
    """Checks whether a student can enroll in a course by querying the prerequisite DAG from the database.
    Performs a full BFS traversal to find ALL transitive prerequisites and verifies every one has been passed.

    Args:
        course_code: The course the student wants to enroll in (e.g. 'CS325').
        completed_courses: List of course codes the student has already passed (e.g. ['CS101', 'CS232']).
    """
    code_up = course_code.upper().strip()
    completed_upper = {c.upper().strip() for c in completed_courses}

    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT title FROM courses WHERE course_code = %s",
        (code_up,),
    )
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": f"Course '{course_code}' not found in the catalog."}

    course_title = row[0]
    all_required: set[str] = set()
    queue = [code_up]
    visited: set[str] = set()

    while queue:
        curr = queue.pop(0)
        if curr in visited:
            continue
        visited.add(curr)
        cur.execute(
            "SELECT prereq_code FROM course_prerequisites WHERE course_code = %s",
            (curr,),
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
        "course_code": code_up,
        "course_title": course_title,
        "can_enroll": can_enroll,
        "all_required_prerequisites": sorted(all_required),
        "completed_prerequisites": met,
        "missing_prerequisites": missing,
        "message": (
            f"✅ You meet all prerequisites for {code_up}! You are eligible to enroll."
            if can_enroll
            else f"❌ You cannot enroll in {code_up} yet. You still need to complete: {', '.join(missing)}"
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 5 — Schedule Conflict Matrix
# ═══════════════════════════════════════════════════════════════

def detect_schedule_conflicts(enrolled_courses: list[str], target_course_code: str) -> dict:
    """Evaluates the timetable schedule matrix from the database to identify any lecture or lab clashes
    between a student's currently enrolled courses and a proposed target course.

    Args:
        enrolled_courses: List of course codes the student is already registered for (e.g. ['CS325', 'MT201']).
        target_course_code: The course the student intends to add (e.g. 'CS330').
    """
    target_up = target_course_code.upper().strip()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT title, slot FROM courses WHERE course_code = %s",
        (target_up,),
    )
    target_row = cur.fetchone()
    if not target_row:
        cur.close()
        conn.close()
        return {"status": "error", "message": f"Course '{target_course_code}' not found in catalog."}

    target_title, target_slot = target_row[0], target_row[1]
    conflicts = []

    enrolled_upper = [c.upper().strip() for c in enrolled_courses if c.upper().strip() != target_up]
    if enrolled_upper and target_slot:
        cur.execute(
            """
            SELECT course_code, title, slot
            FROM courses
            WHERE course_code = ANY(%s) AND slot = %s
            """,
            (enrolled_upper, target_slot),
        )
        for r in cur.fetchall():
            conflicts.append(
                {
                    "conflicting_course": r[0],
                    "conflicting_title": r[1],
                    "shared_slot": r[2],
                }
            )

    cur.close()
    conn.close()

    has_clash = len(conflicts) > 0
    return {
        "status": "conflict_detected" if has_clash else "clear",
        "target_course": target_up,
        "target_title": target_title,
        "target_slot": target_slot,
        "has_schedule_clash": has_clash,
        "conflicts_count": len(conflicts),
        "clashes": conflicts,
        "message": (
            f"⚠️ Schedule Conflict Detected: {target_up} ({target_slot}) collides with {', '.join(c['conflicting_course'] for c in conflicts)}."
            if has_clash
            else f"✅ No schedule conflict: {target_up} ({target_slot}) fits into your timetable."
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 6 — What-If Degree & Minor Simulator
# ═══════════════════════════════════════════════════════════════

def simulate_what_if_degree(completed_courses: list[str], proposed_minor: str = "Artificial Intelligence") -> dict:
    """Simulates graduation requirements, total earned credits, and minor track progress by querying
    curriculum credit requirements and degree minors directly from the database.

    Args:
        completed_courses: List of course codes already passed (e.g. ['CS101', 'CS102', 'CS201', 'CS330', 'MT201']).
        proposed_minor: Proposed minor track ('Artificial Intelligence', 'Software Systems', or 'Embedded & Hardware Systems').
    """
    TOTAL_GRADUATION_CREDITS = 136
    completed_upper = [c.upper().strip() for c in completed_courses]

    conn = _get_db()
    cur = conn.cursor()

    # Calculate earned credits from database
    earned_credits = 0
    if completed_upper:
        cur.execute(
            "SELECT COALESCE(SUM(credits), 0) FROM courses WHERE course_code = ANY(%s)",
            (completed_upper,),
        )
        sum_row = cur.fetchone()
        earned_credits = int(sum_row[0]) if sum_row else 0

    # Query proposed minor requirements from degree_minors table
    cur.execute(
        "SELECT minor_name, required_courses, required_credits, description FROM degree_minors WHERE minor_name = %s",
        (proposed_minor,),
    )
    minor_row = cur.fetchone()
    if not minor_row:
        cur.execute("SELECT minor_name FROM degree_minors ORDER BY minor_name")
        valid_minors = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return {
            "status": "error",
            "message": f"Minor '{proposed_minor}' not found in database. Available tracks: {', '.join(valid_minors)}",
        }

    minor_name = minor_row[0]
    required_courses = [c.strip() for c in minor_row[1].split(",") if c.strip()]
    description = minor_row[3]

    cur.close()
    conn.close()

    completed_set = set(completed_upper)
    met_minor = [c for c in required_courses if c in completed_set]
    missing_minor = [c for c in required_courses if c not in completed_set]
    minor_progress_pct = round((len(met_minor) / len(required_courses)) * 100, 1) if required_courses else 100.0

    return {
        "status": "success",
        "total_graduation_credits_required": TOTAL_GRADUATION_CREDITS,
        "earned_credits": earned_credits,
        "remaining_credits_to_graduate": max(0, TOTAL_GRADUATION_CREDITS - earned_credits),
        "overall_degree_completion_pct": round((earned_credits / TOTAL_GRADUATION_CREDITS) * 100, 1),
        "proposed_minor": minor_name,
        "minor_description": description,
        "minor_progress_pct": f"{minor_progress_pct}%",
        "minor_completed_courses": met_minor,
        "minor_remaining_courses": missing_minor,
        "minor_eligibility": (
            "✅ Minor Requirements Fully Satisfied!"
            if not missing_minor
            else f"⏳ In Progress: You need to complete {', '.join(missing_minor)} to graduate with a minor in {minor_name}."
        ),
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 7 — Interest-Based Elective Recommender
# ═══════════════════════════════════════════════════════════════

def recommend_electives_by_interest(interests_query: str, completed_courses: list[str] = None) -> dict:
    """Queries the database for university and domain electives matching a student's research and career interests,
    evaluating prerequisite satisfaction directly from course records.

    Args:
        interests_query: Keywords describing academic interests (e.g. 'machine learning and deep learning' or 'operating systems and low level').
        completed_courses: Optional list of completed courses to check if student is currently eligible to enroll.
    """
    query_lower = interests_query.lower()
    completed_set = {c.upper().strip() for c in (completed_courses or [])}

    conn = _get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT c.course_code, c.title, c.credits, c.slot, c.domains,
               COALESCE(array_agg(cp.prereq_code) FILTER (WHERE cp.prereq_code IS NOT NULL), '{}') AS prereqs
        FROM courses c
        LEFT JOIN course_prerequisites cp ON cp.course_code = c.course_code
        WHERE c.category = 'elective'
        GROUP BY c.course_code, c.title, c.credits, c.slot, c.domains
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    recommendations = []
    for r in rows:
        code, title, credits, slot, domains_str, prereqs = r[0], r[1], r[2], r[3], r[4] or "", r[5]

        score = 0
        domains = [d.strip() for d in domains_str.split(",") if d.strip()]
        for d in domains:
            if d.lower() in query_lower:
                score += 35
        for word in query_lower.split():
            if len(word) > 3 and word in title.lower():
                score += 25

        if score > 0 or "all" in query_lower:
            missing_prereqs = [p for p in prereqs if p not in completed_set]
            can_take = len(missing_prereqs) == 0

            recommendations.append(
                {
                    "course_code": code,
                    "title": title,
                    "credits": credits,
                    "schedule_slot": slot,
                    "domains": domains,
                    "relevance_score": min(score, 100),
                    "prerequisites_required": prereqs,
                    "currently_eligible": can_take,
                    "missing_prerequisites": missing_prereqs,
                }
            )

    recommendations.sort(key=lambda x: x["relevance_score"], reverse=True)

    return {
        "status": "success",
        "interests_queried": interests_query,
        "recommendations_count": len(recommendations),
        "recommended_electives": recommendations,
    }


# ═══════════════════════════════════════════════════════════════
#  Tool 8 — "Ask GIKI" Policy Engine (Database Search)
# ═══════════════════════════════════════════════════════════════

def ask_giki_policy(question: str) -> dict:
    """Answers questions about GIKI university policies, academic rules, and regulations
    by querying the university_policies database knowledge base.

    Args:
        question: The student's question about a GIKI policy or rule.
    """
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT topic, title, policy_text FROM university_policies")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    question_lower = question.lower()
    relevant = []

    for r in rows:
        topic, title, policy_text = r[0], r[1], r[2]
        topic_words = set(topic.replace("_", " ").split())
        policy_words = set(policy_text.lower().split()[:20])
        keywords = topic_words | policy_words

        if any(kw in question_lower for kw in keywords if len(kw) > 3):
            relevant.append(
                {"topic": title, "policy": policy_text}
            )

    if not relevant:
        available = ", ".join(r[1] for r in rows)
        return {
            "answer": (
                "I couldn't find a specific policy matching your question. "
                f"Available policy topics: {available}"
            ),
            "policies_found": 0,
        }

    return {
        "answer": "Here are the relevant GIKI policies found in the database:",
        "policies": relevant,
        "policies_found": len(relevant),
    }


# ═══════════════════════════════════════════════════════════════
#  Agent Definition
# ═══════════════════════════════════════════════════════════════

_INSTRUCTION = """\
You are the **G-Cortex Course Management Subagent** — the autonomous academic advisor
for GIKI. You query institutional data directly from the G-Cortex PostgreSQL database.

## Your capabilities:
1. **Course Catalog** (`list_all_courses`): Query every course from the database.
2. **Course Details** (`get_course_details`): Query course specifics, direct prerequisites, and unlocked courses.
3. **Prerequisite DAG Engine** (`get_prerequisite_chain`): Recursively trace prerequisite dependencies via database records.
4. **Enrollment Eligibility** (`check_enrollment_eligibility`): Walk the prerequisite graph to verify whether a student can enroll.
5. **Schedule Conflict Matrix** (`detect_schedule_conflicts`): Detect timetable overlaps between enrolled and proposed courses.
6. **What-If Degree Simulator** (`simulate_what_if_degree`): Compute earned credits and minor track progress against database records.
7. **Interest-Based Electives** (`recommend_electives_by_interest`): Query and rank electives matching career/research domains.
8. **GIKI Policy Engine** (`ask_giki_policy`): Search university policies from the policy knowledge base table.

## Behaviour guidelines:
- Always use `check_enrollment_eligibility` when students ask "Can I take X?".
- Validate timetable clashes with `detect_schedule_conflicts` when proposing registration plans.
- Present results clearly with ✅/❌ indicators and explain missing prerequisites constructively.
"""

ALL_COURSE_TOOLS = [
    list_all_courses,
    get_course_details,
    get_prerequisite_chain,
    check_enrollment_eligibility,
    detect_schedule_conflicts,
    simulate_what_if_degree,
    recommend_electives_by_interest,
    ask_giki_policy,
]

course_management_subagent = Agent(
    name="course_management_subagent",
    model="gemini-3.5-flash-lite",
    description=(
        "AI-powered Course Management Subagent for GIKI. Directly interfaces with the database for "
        "catalog queries, prerequisite DAG traversal, enrollment eligibility checks, schedule conflict detection, "
        "interest-based elective recommendations, What-If degree/minor simulations, and university policy Q&A."
    ),
    instruction=_INSTRUCTION,
    tools=ALL_COURSE_TOOLS,
)

root_agent = Agent(
    name="course_management_agent",
    model="gemini-3.5-flash-lite",
    description="G-Cortex Course Management Agent for academic planning and advising.",
    instruction=_INSTRUCTION,
    tools=ALL_COURSE_TOOLS,
)
