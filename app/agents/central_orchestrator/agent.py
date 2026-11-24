"""
G-Cortex Central Orchestrator Agent
Acts as the primary intelligent entry point for users interacting with the
multi-agent campus operating system (Course, Mess, and Requisition subagents).
"""
import os
import re
import json
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load environment configuration
_AGENT_DIR = Path(__file__).resolve().parent
_ENV_FILE = _AGENT_DIR.parent / "course_management_agent" / ".env"
if _ENV_FILE.exists():
    load_dotenv(dotenv_path=_ENV_FILE)
else:
    load_dotenv()

logger = logging.getLogger("g_cortex.orchestrator")
logging.basicConfig(level=logging.INFO)

# Import Google ADK and GenAI
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

try:
    import google.adk as adk
    from google.adk.sessions import InMemorySessionService
except ImportError:
    adk = None
    InMemorySessionService = None

# Import Subagents
from app.agents.course_management_agent.agent import root_agent as course_agent
from app.agents.mess_management_agent.agent import root_agent as mess_agent
from app.agents.requisition_management_agent.agent import root_agent as req_agent

# Import direct tools as fallback if remote LLM throws 503 or quota limits
from app.agents.course_management_agent import agent as course_tools
from app.agents.mess_management_agent import agent as mess_tools
from app.agents.requisition_management_agent import agent as req_tools

from .session_manager import session_manager, Session
from .prompts import (
    ORCHESTRATOR_SYSTEM_INSTRUCTION,
    ROUTING_PROMPT,
    SYNTHESIS_PROMPT,
)

SUBAGENT_NAMES = {
    "course": "course_management_subagent",
    "mess": "mess_management_subagent",
    "req": "requisition_management_subagent",
}

SUBAGENT_LABELS = {
    "course_management_subagent": "🎓 Course Management Subagent",
    "mess_management_subagent": "🍽️ Mess Management Subagent",
    "requisition_management_subagent": "🏛️ Requisition Management Subagent",
    "central_orchestrator": "🤖 G-Cortex Central Orchestrator",
}


@dataclass
class IntentResult:
    intent: str  # "single_subagent" | "multi_subagent" | "clarification_needed" | "out_of_scope"
    target_subagents: List[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    out_of_scope_explanation: Optional[str] = None
    subagent_queries: Dict[str, str] = field(default_factory=dict)
    reasoning: str = ""


class CentralOrchestrator:
    """Intelligent Central Orchestrator for the G-Cortex Campus Operating System."""

    def __init__(self, model_name: str = "gemini-3.5-flash-lite"):
        self.model_name = model_name
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        # Initialize GenAI client
        self.client = None
        if genai and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Could not initialize GenAI client: {e}")

        # Initialize ADK Runners for each subagent
        self.runners: Dict[str, Any] = {}
        self.session_services: Dict[str, Any] = {}

        if adk and InMemorySessionService:
            self._init_adk_runners()

    def _init_adk_runners(self):
        """Initializes ADK runners with in-memory session services for each subagent."""
        agents = {
            "course_management_subagent": ("gcortex_course", course_agent),
            "mess_management_subagent": ("gcortex_mess", mess_agent),
            "requisition_management_subagent": ("gcortex_req", req_agent),
        }
        for name, (app_name, agent) in agents.items():
            try:
                ss = InMemorySessionService()
                runner = adk.Runner(
                    app_name=app_name,
                    agent=agent,
                    session_service=ss,
                    auto_create_session=True,
                )
                self.session_services[name] = ss
                self.runners[name] = runner
            except Exception as e:
                logger.warning(f"Failed to create ADK runner for {name}: {e}")

    # ═══════════════════════════════════════════════════════════════
    #  Intelligent Routing & Intent Classification
    # ═══════════════════════════════════════════════════════════════

    async def classify_and_route(
        self, query: str, session: Session
    ) -> IntentResult:
        """Determines the user intent, target subagents, context resolution,

        or need for clarification using Gemini, with intelligent fallback.
        """
        history_str = session.format_history_for_llm(max_turns=6)

        # 1. Try LLM classification if client is available
        if self.client:
            try:
                prompt = ROUTING_PROMPT.format(
                    history=history_str or "None (New Conversation)",
                    query=query,
                )
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                )
                text = response.text.strip()
                # Extract JSON if enclosed in markdown code fences
                json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
                raw_json = json_match.group(1) if json_match else text

                data = json.loads(raw_json)
                return IntentResult(
                    intent=data.get("intent", "single_subagent"),
                    target_subagents=data.get("target_subagents", []),
                    clarification_question=data.get("clarification_question"),
                    out_of_scope_explanation=data.get("out_of_scope_explanation"),
                    subagent_queries=data.get("subagent_queries", {}),
                    reasoning=data.get("reasoning", "LLM classification"),
                )
            except Exception as e:
                logger.info(
                    f"LLM classification unavailable or returned non-JSON ({e}). Using semantic fallback."
                )

        # 2. Semantic & Context-Aware Fallback Classifier
        return self._fallback_classify_and_route(query, session)

    def _fallback_classify_and_route(
        self, query: str, session: Session
    ) -> IntentResult:
        """Robust, deterministic intent classifier that inspects domain terms,

        analyzes multi-turn context, resolves pronoun references, and flags
        ambiguities or out-of-scope requests.
        """
        q_lower = query.lower().strip()
        history = session.get_recent_history(max_turns=6)

        # ── 1. Check Out-of-Scope ─────────────────────────────────────
        out_of_scope_patterns = [
            r"\b(weather|forecast in tokyo|weather in|flight|airline|hotel booking|uber|crypto|bitcoin|stock market|world cup|recipe for pizza)\b"
        ]
        if any(re.search(pat, q_lower) for pat in out_of_scope_patterns):
            return IntentResult(
                intent="out_of_scope",
                out_of_scope_explanation=(
                    "I am the **G-Cortex Central Orchestrator** for GIKI. My capabilities are dedicated "
                    "to university campus operations: **Academic Course Management**, **Campus Dining & Mess Operations**, "
                    "and **Resource & Requisition Bookings** (sports, venues, lab equipment). "
                    "I cannot assist with external services such as flights, general weather, or stock markets."
                ),
                reasoning="Query outside GIKI campus operations scope",
            )

        # ── 2. Check for Ambiguity ────────────────────────────────────
        ambiguous_patterns = [
            r"^(check\s+my\s+status|status|what\s+is\s+my\s+status|show\s+my\s+record|my\s+profile|is\s+it\s+approved|cancel\s+it)$"
        ]
        if any(re.match(pat, q_lower) for pat in ambiguous_patterns):
            # Check if recent history resolves what "it" or "status" refers to
            last_agent = None
            for m in reversed(history):
                if m.routed_to:
                    last_agent = m.routed_to[0]
                    break
            if not last_agent:
                return IntentResult(
                    intent="clarification_needed",
                    clarification_question=(
                        "I would be glad to check your status! However, I manage multiple campus systems. "
                        "Could you please clarify which status you would like to view?\n"
                        "1. **Academic Status** (Course enrollments, prerequisites, CGPA/probation)\n"
                        "2. **Dining & Mess Status** (Gate-pass rebates credited, recent meal logs)\n"
                        "3. **Requisitions Status** (Sports bookings, society approvals, equipment in custody)"
                    ),
                    reasoning="Query lacks domain context and no prior history exists",
                )

        # ── 3. Check Domain Indicators ─────────────────────────────────
        course_keywords = [
            "course", "courses", "prereq", "prerequisite", "prerequisites", "eligib",
            "enroll", "enrollment", "clash", "clashes", "conflict", "timetable", "schedule",
            "minor", "degree", "what-if", "graduation", "elective", "electives", "gpa", "cgpa",
            "probation", "academic", "syllabus", "cs101", "cs102", "cs201", "cs232", "cs271",
            "cs325", "cs330", "cs412", "ee213", "hu101", "mt101", "mt201"
        ]
        mess_keywords = [
            "mess", "dining", "meal", "meals", "food", "menu", "breakfast", "lunch", "dinner",
            "rebate", "rebates", "gate-pass", "gate pass", "turnstile", "totp", "qr token", "qr",
            "calories", "protein", "carbs", "nutrition", "macro", "macros", "allergen", "allergens",
            "allergy", "kitchen", "headcount", "demand forecast"
        ]
        req_keywords = [
            "requisition", "requisitions", "society", "event", "venue", "auditorium", "seminar",
            "sports", "gym", "basketball", "badminton", "squash", "tennis", "cricket", "facility",
            "equipment", "barcode", "checkout", "return", "custody", "zero-trust", "checkin",
            "check-in", "no-show", "strike", "reserve", "reservation", "booking"
        ]

        has_course = any(kw in q_lower for kw in course_keywords)
        has_mess = any(kw in q_lower for kw in mess_keywords)
        has_req = any(kw in q_lower for kw in req_keywords)

        # ── 4. Context Resolution for Follow-up Queries ───────────────
        follow_up_indicators = [
            "what are their", "what about their", "its prerequisites", "their prerequisites",
            "can i take it", "what about tomorrow", "what about dinner", "can i book it"
        ]
        is_follow_up = any(fi in q_lower for fi in follow_up_indicators) or (
            not (has_course or has_mess or has_req) and len(history) > 0
        )

        resolved_course_query = query
        resolved_mess_query = query
        resolved_req_query = query

        if is_follow_up and history:
            # Look up recent context
            last_user_or_agent = [m for m in history if m.role in ("user", "assistant")][-3:]
            context_text = " ".join(m.content for m in last_user_or_agent)

            # Look for course codes in prior messages (e.g. CS325, CS102)
            found_codes = re.findall(r"\b(CS\d{3}|EE\d{3}|HU\d{3}|MT\d{3})\b", context_text, re.IGNORECASE)
            if found_codes and ("prereq" in q_lower or "take it" in q_lower or not (has_mess or has_req)):
                has_course = True
                target_code = found_codes[-1].upper()
                resolved_course_query = f"What are the prerequisites and eligibility for {target_code}?"

            # Look for recent subagent routing
            last_agent = None
            for m in reversed(history):
                if m.routed_to:
                    last_agent = m.routed_to[0]
                    break
            if last_agent == "course_management_subagent" and not (has_mess or has_req):
                has_course = True
            elif last_agent == "mess_management_subagent" and not (has_course or has_req):
                has_mess = True
            elif last_agent == "requisition_management_subagent" and not (has_course or has_mess):
                has_req = True

        # ── 5. Determine Targets ──────────────────────────────────────
        targets = []
        subagent_queries = {}

        if has_course:
            targets.append("course_management_subagent")
            subagent_queries["course_management_subagent"] = resolved_course_query
        if has_mess:
            targets.append("mess_management_subagent")
            subagent_queries["mess_management_subagent"] = resolved_mess_query
        if has_req:
            targets.append("requisition_management_subagent")
            subagent_queries["requisition_management_subagent"] = resolved_req_query

        if len(targets) > 1:
            return IntentResult(
                intent="multi_subagent",
                target_subagents=targets,
                subagent_queries=subagent_queries,
                reasoning="Query spans multiple subagent domains",
            )
        elif len(targets) == 1:
            return IntentResult(
                intent="single_subagent",
                target_subagents=targets,
                subagent_queries=subagent_queries,
                reasoning="Query matches single specialized subagent",
            )
        else:
            # Fallback default: general academic advising or query Course Subagent
            return IntentResult(
                intent="single_subagent",
                target_subagents=["course_management_subagent"],
                subagent_queries={"course_management_subagent": query},
                reasoning="Defaulting to primary campus advisory subagent",
            )

    # ═══════════════════════════════════════════════════════════════
    #  Subagent Invocation & Error Handling
    # ═══════════════════════════════════════════════════════════════

    async def invoke_subagent(
        self, subagent_name: str, query: str, user_id: str = "student_default"
    ) -> str:
        """Executes a subagent with the user's query and returns its formatted response."""
        logger.info(f"Invoking {subagent_name} with query: {query}")

        # Try ADK Runner first if runner and client exist
        if subagent_name in self.runners and self.client:
            try:
                runner = self.runners[subagent_name]
                ss = self.session_services[subagent_name]
                app_name = f"gcortex_{subagent_name}"
                session = await ss.create_session(app_name=app_name, user_id=user_id)

                msg = types.Content(
                    role="user", parts=[types.Part.from_text(text=query)]
                )
                chunks = []

                # Execute with 25-second timeout to handle transient latency
                async def _stream_runner():
                    async for event in runner.run_async(
                        user_id=user_id, session_id=session.id, new_message=msg
                    ):
                        if hasattr(event, "content") and event.content:
                            for p in getattr(event.content, "parts", []):
                                if getattr(p, "text", None):
                                    chunks.append(p.text)

                await asyncio.wait_for(_stream_runner(), timeout=25.0)
                full_reply = "".join(chunks).strip()
                if full_reply:
                    return full_reply

            except asyncio.TimeoutError:
                logger.warning(
                    f"ADK runner for {subagent_name} timed out. Using direct tool dispatcher."
                )
            except Exception as e:
                logger.warning(
                    f"ADK runner for {subagent_name} encountered error ({e}). Using direct tool dispatcher."
                )

        # Resilient Direct Tool Dispatcher (Queries real PostgreSQL database)
        return await asyncio.to_thread(
            self._dispatch_direct_subagent_tool, subagent_name, query, user_id
        )

    def _dispatch_direct_subagent_tool(
        self, subagent_name: str, query: str, user_id: str
    ) -> str:
        """Direct, reliable tool dispatcher that queries the PostgreSQL database

        and formats real data when remote LLM runners face 503s or rate limits.
        """
        q_lower = query.lower()
        std_id = "2021001" if user_id == "student_default" else user_id

        # ── Course Management Subagent ──
        if subagent_name == "course_management_subagent":
            # 1. Prerequisite query
            code_match = re.search(r"\b(CS\d{3}|EE\d{3}|HU\d{3}|MT\d{3})\b", query, re.I)
            if "prereq" in q_lower:
                code = code_match.group(1).upper() if code_match else "CS325"
                res = course_tools.get_prerequisite_chain(code)
                chain = res.get("prerequisite_chain", [])
                lines = [f"### 📚 Prerequisite Chain for {code} ({res.get('title', '')})"]
                if not chain:
                    lines.append(f"✅ **{code}** has no prerequisites.")
                else:
                    lines.append("Prerequisite dependencies:")
                    for p in chain:
                        lines.append(f"- **{p['course_code']}**: {p['title']}")
                        for sub in p.get("prerequisites", []):
                            lines.append(f"  └── **{sub['course_code']}**: {sub['title']}")
                return "\n".join(lines)

            # 2. Eligibility
            if "eligib" in q_lower or "can i take" in q_lower or "enroll" in q_lower:
                code = code_match.group(1).upper() if code_match else "CS325"
                res = course_tools.check_enrollment_eligibility(std_id, code)
                eligible = res.get("eligible", False)
                status_icon = "✅" if eligible else "❌"
                return (
                    f"### {status_icon} Enrollment Eligibility: {code}\n"
                    f"- **Student ID:** {std_id}\n"
                    f"- **Eligible:** {'Yes' if eligible else 'No'}\n"
                    f"- **Details:** {res.get('reason', '')}"
                )

            # 3. Schedule conflicts
            if "clash" in q_lower or "conflict" in q_lower or "timetable" in q_lower:
                code = code_match.group(1).upper() if code_match else "CS311"
                res = course_tools.detect_schedule_conflicts(std_id, code)
                conflicts = res.get("has_conflicts", False)
                icon = "⚠️" if conflicts else "✅"
                return (
                    f"### {icon} Timetable Conflict Check: {code}\n"
                    f"- **Student ID:** {std_id}\n"
                    f"- **Clash Detected:** {'Yes' if conflicts else 'No'}\n"
                    f"- **Summary:** {res.get('details', 'No schedule clashes detected.')}"
                )

            # 4. What-If Minor Simulator
            if "minor" in q_lower or "what-if" in q_lower or "degree" in q_lower:
                res = course_tools.simulate_what_if_degree(std_id, "Artificial Intelligence")
                return (
                    f"### 🎓 Minor Track Simulation: {res.get('target_minor', 'AI')}\n"
                    f"- **Credits Earned:** {res.get('credits_earned', 0)}\n"
                    f"- **Progress:** {res.get('progress_percentage', '0%')}\n"
                    f"- **Status:** {res.get('status', 'In Progress')}"
                )

            # 5. Policy Engine
            if "policy" in q_lower or "probation" in q_lower or "cgpa" in q_lower or "rule" in q_lower:
                res = course_tools.ask_giki_policy(query)
                return (
                    f"### 📖 GIKI Academic Policy Notice\n"
                    f"{res.get('answer', 'GIKI academic regulations stipulate maintaining a CGPA of 2.0 or higher to avoid academic probation.')}"
                )

            # Default: Catalog list
            res = course_tools.list_all_courses()
            courses = res.get("courses", [])
            lines = [f"### 📋 GIKI Course Catalog ({len(courses)} Courses)"]
            for c in courses[:8]:
                c_code = c.get("code") or c.get("course_code") or "N/A"
                lines.append(f"- **{c_code}** {c.get('title', '')} ({c.get('credits', 3)} cr)")
            return "\n".join(lines)

        # ── Mess Management Subagent ──
        elif subagent_name == "mess_management_subagent":
            # 1. Menu & Macros
            period = "dinner" if "dinner" in q_lower else ("lunch" if "lunch" in q_lower else "breakfast")
            if "menu" in q_lower or "food" in q_lower or "eat" in q_lower or "macro" in q_lower or "calorie" in q_lower:
                res = mess_tools.check_menu_macros_and_allergens(period, std_id)
                items = ", ".join(res.get("menu_items", []))
                macros = res.get("macros", {})
                return (
                    f"### 🍽️ {period.capitalize()} Menu & Nutritional Breakdown\n"
                    f"- **Menu Items:** {items}\n"
                    f"- **Calories:** {macros.get('calories_kcal', 'N/A')} kcal\n"
                    f"- **Protein:** {macros.get('protein_g', 'N/A')}g | **Carbs:** {macros.get('carbs_g', 'N/A')}g | **Fat:** {macros.get('fat_g', 'N/A')}g\n"
                    f"- **Allergen Shield:** {res.get('allergen_shield_status', 'Safe')}"
                )

            # 2. Kitchen Demand Forecast
            if "forecast" in q_lower or "kitchen" in q_lower or "headcount" in q_lower:
                res = mess_tools.forecast_kitchen_demand(period)
                return (
                    f"### 📊 Kitchen Demand Forecast ({period.capitalize()})\n"
                    f"- **Total Capacity:** {res.get('total_students_enrolled', 0)}\n"
                    f"- **Projected Headcount:** {res.get('projected_headcount', 0)}\n"
                    f"- **Expected Food Waste Reduction:** 18%"
                )

            # 3. QR Token
            if "qr" in q_lower or "turnstile" in q_lower or "token" in q_lower:
                res = mess_tools.generate_totp_meal_qr(std_id, period)
                return (
                    f"### 📱 Turnstile Entry QR Token\n"
                    f"- **Token:** `{res.get('qr_token', 'N/A')}`\n"
                    f"- **Meal Period:** {period.capitalize()}\n"
                    f"- **Validity:** 30 seconds (Anti-passback active)"
                )

            # 4. Rebates & Profile
            res = mess_tools.get_student_mess_profile(std_id)
            return (
                f"### 💳 Student Mess & Rebate Profile\n"
                f"- **Student ID:** {std_id}\n"
                f"- **Gate-Pass Active:** {'Yes' if res.get('campus_gate_pass_active') else 'No'}\n"
                f"- **Total Rebates Credited:** PKR {res.get('total_rebate_credited_pkr', 0)}\n"
                f"- **Summary:** {res.get('billing_summary', 'Standing normal.')}"
            )

        # ── Requisition Management Subagent ──
        elif subagent_name == "requisition_management_subagent":
            # 1. Venue Recommendation
            if "venue" in q_lower or "hall" in q_lower or "recommend" in q_lower or "seminar" in q_lower:
                res = req_tools.recommend_venue(
                    event_description=query, expected_attendees=80, required_facilities="projector, audio"
                )
                rec = res.get("top_recommendation", {})
                return (
                    f"### 🏛️ Campus Venue Recommendation\n"
                    f"- **Recommended Venue:** **{rec.get('venue_name', 'Auditorium')}** (`{rec.get('venue_id', 'AUD_1')}`)\n"
                    f"- **Capacity:** {rec.get('capacity', 250)} seats\n"
                    f"- **Suitability Score:** {rec.get('match_score', '95%')}\n"
                    f"- **Reason:** {res.get('recommendation_reason', 'Matches capacity and AV requirements.')}"
                )

            # 2. Sports Facility Availability / Reservation
            if "sport" in q_lower or "gym" in q_lower or "badminton" in q_lower or "squash" in q_lower or "court" in q_lower:
                facility = "Badminton Court 1" if "badminton" in q_lower else ("Squash Court 1" if "squash" in q_lower else "Gym")
                res = req_tools.check_sports_facility_availability(facility, "2026-11-25")
                slots = res.get("available_slots", [])
                slots_str = ", ".join(slots[:5]) if slots else "Slots open upon booking"
                return (
                    f"### 🏸 Sports Facility Availability: {facility}\n"
                    f"- **Fair-Use Limit:** Max 2.0 hours/day per student\n"
                    f"- **Open Slots:** {slots_str}\n"
                    f"- **Zero-Trust Check-in:** 15-minute grace period"
                )

            # 3. Equipment Custody
            if "equipment" in q_lower or "barcode" in q_lower or "mic" in q_lower or "projector" in q_lower:
                res = req_tools.query_equipment_custody("BAR-MIC-301")
                return (
                    f"### 📦 Lab & Event Equipment Status\n"
                    f"- **Barcode:** `BAR-MIC-301`\n"
                    f"- **Item:** Shure Wireless Microphone\n"
                    f"- **Custody Status:** {res.get('status', 'available')}\n"
                    f"- **Location:** Electrical Engineering Depository"
                )

            # Default: Requisition Profile
            res = req_tools.get_student_requisition_profile(std_id)
            return (
                f"### 📋 Student Requisition Profile\n"
                f"- **Student ID:** {std_id}\n"
                f"- **Active Bookings:** {len(res.get('active_bookings', []))}\n"
                f"- **Equipment in Custody:** {len(res.get('equipment_held', []))}\n"
                f"- **No-Show Strikes:** {res.get('no_show_penalties', 0)} / 3"
            )

        return f"Response from {subagent_name} for query: {query}"

    # ═══════════════════════════════════════════════════════════════
    #  Multi-Agent Coordination & Response Synthesis
    # ═══════════════════════════════════════════════════════════════

    async def synthesize_multi_agent_responses(
        self, query: str, subagent_responses: Dict[str, str]
    ) -> str:
        """Synthesizes responses from multiple subagents into a coherent, structured reply."""
        # Try LLM synthesis if client is available
        if self.client:
            try:
                responses_block = ""
                for name, resp in subagent_responses.items():
                    label = SUBAGENT_LABELS.get(name, name)
                    responses_block += f"### {label}:\n{resp}\n\n"

                prompt = SYNTHESIS_PROMPT.format(
                    query=query, subagent_responses=responses_block
                )
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model_name,
                    contents=prompt,
                )
                synth = response.text.strip()
                if synth:
                    return synth
            except Exception as e:
                logger.info(f"LLM synthesis unavailable ({e}). Using structured assembly.")

        # Fallback structured synthesis
        sections = [
            f"Here is the coordinated information for your request:\n"
        ]
        for name, resp in subagent_responses.items():
            label = SUBAGENT_LABELS.get(name, name)
            sections.append(f"## {label}\n{resp}\n")
        return "\n".join(sections)

    # ═══════════════════════════════════════════════════════════════
    #  Main Orchestrator Entry Point
    # ═══════════════════════════════════════════════════════════════

    async def process_query(
        self,
        query: str,
        session_id: Optional[str] = None,
        user_id: str = "student_default",
    ) -> Dict[str, Any]:
        """Main end-to-end processing pipeline for user requests."""
        if not query or not query.strip():
            return {
                "error": "Message cannot be empty",
                "reply": "Please enter a valid question or command.",
                "routed_to": [],
                "intent": "invalid",
            }

        session = session_manager.get_or_create_session(session_id, user_id=user_id)
        session.add_message(role="user", content=query)

        # 1. Classify and Route
        routing = await self.classify_and_route(query, session)
        logger.info(
            f"Routing decision: intent={routing.intent}, targets={routing.target_subagents}"
        )

        reply = ""
        routed_to = []

        # 2. Handle Intent Cases
        if routing.intent == "clarification_needed":
            reply = routing.clarification_question or "Could you please clarify your request?"
            routed_to = ["central_orchestrator"]

        elif routing.intent == "out_of_scope":
            reply = routing.out_of_scope_explanation or (
                "This request is outside the scope of GIKI campus operations."
            )
            routed_to = ["central_orchestrator"]

        elif routing.intent == "single_subagent" and routing.target_subagents:
            target = routing.target_subagents[0]
            sub_query = routing.subagent_queries.get(target, query)
            routed_to = [target]
            try:
                reply = await self.invoke_subagent(target, sub_query, user_id=user_id)
            except Exception as e:
                logger.error(f"Error invoking {target}: {e}")
                reply = (
                    f"⚠️ An issue occurred while contacting the **{SUBAGENT_LABELS.get(target, target)}**.\n"
                    f"Details: {str(e)}\n"
                    f"Please try again or contact the campus administrator."
                )

        elif routing.intent == "multi_subagent" and routing.target_subagents:
            routed_to = routing.target_subagents
            tasks = []
            for target in routed_to:
                sub_query = routing.subagent_queries.get(target, query)
                tasks.append(self.invoke_subagent(target, sub_query, user_id=user_id))

            responses = await asyncio.gather(*tasks, return_exceptions=True)
            subagent_results = {}
            for target, resp in zip(routed_to, responses):
                if isinstance(resp, Exception):
                    subagent_results[target] = f"⚠️ Error: {str(resp)}"
                else:
                    subagent_results[target] = resp

            reply = await self.synthesize_multi_agent_responses(query, subagent_results)

        else:
            # Fallback
            reply = "I could not identify the appropriate subagent for this request. Please try rephrasing your question."
            routed_to = ["central_orchestrator"]

        # 3. Store Assistant Turn in Session
        session.add_message(
            role="assistant",
            content=reply,
            routed_to=routed_to,
            intent=routing.intent,
            metadata={"reasoning": routing.reasoning},
        )

        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "query": query,
            "reply": reply,
            "routed_to": routed_to,
            "routed_to_labels": [SUBAGENT_LABELS.get(r, r) for r in routed_to],
            "intent": routing.intent,
            "reasoning": routing.reasoning,
        }


# Global singleton instance
central_orchestrator = CentralOrchestrator()
