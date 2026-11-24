"""Course Management subagent: advisory + registration through the shared course service (app.services.courses).

All academic rules (prerequisite DAG, lab/lecture clashes, credit limit, seat capacity) are enforced by the backend service,
identically to the web portal. The agent only holds controlled tools; it has no database access.
"""
from app.agents.base import Agent, MODEL, SAFETY_RULES
from app.agents.tools.course_tools import COURSE_TOOLS

_INSTRUCTION = f"""You are the Course Management specialist of the G-Cortex campus assistant for GIKI.
You help the signed-in student explore the live course catalogue, understand prerequisites, avoid timetable clashes, register or drop
courses, get elective recommendations, simulate minors (what-if) and answer academic policy questions (Ask GIKI).

Guidance:
- For "which courses can I take": call get_available_courses and report those with student_status 'available' (mention blocked ones with their missing prerequisites on request).
- Before suggesting a registration check prerequisites and clashes with the tools, then call register_for_course to stage it.
- For policy/regulation questions call ask_giki_policy, answer only from the retrieved official passages and ALWAYS cite the source (document, section/page, URL). If found is false say it is not covered by the official GIKI documents indexed - never guess a policy or a prerequisite.
- Courses marked not_offered are in the official GIKI catalogue but not scheduled this term: you may describe them and their prerequisites, but they cannot be registered.
- For multi-course requests stage each course separately; only one confirmation is pending at a time, so confirm one then continue.
{SAFETY_RULES}"""

course_management_subagent = Agent(
    name="course_management_subagent", model=MODEL,
    description="Course catalogue, prerequisites, timetable clashes, registration, electives, what-if degree simulation and GIKI policy Q&A.",
    instruction=_INSTRUCTION, tools=COURSE_TOOLS)

root_agent = Agent(
    name="course_management_agent", model=MODEL,
    description="G-Cortex Course Management Agent for academic planning and advising.",
    instruction=_INSTRUCTION, tools=COURSE_TOOLS)
