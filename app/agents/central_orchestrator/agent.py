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

from app.agents import runtime
from app.config import ASSISTANT_MODEL
from app.services import pending_actions
from app.services.actor import Actor
from app.services.errors import ServiceError

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

    def __init__(self, model_name: str = ASSISTANT_MODEL):
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

    async def invoke_subagent(self, subagent_name: str, query: str, session: Session, actor: Actor) -> str:
        """Run a specialised subagent for the authenticated user.

        The user's identity is bound through `runtime.set_context` (server-side); the subagent's tools can only act as that user and
        every state-changing tool merely stages an action for the user to confirm. There is NO simulated fallback: if the model
        service is unavailable the user is told so, rather than being shown invented data.
        """
        logger.info("Invoking %s for %s", subagent_name, actor.user_id)
        runner = self.runners.get(subagent_name)
        if runner is None or self.client is None:
            raise RuntimeError("The AI model service is not configured.")
        token = runtime.set_context(actor, session.session_id)
        try:
            adk_session_id = f"{session.session_id}:{subagent_name}"
            msg = types.Content(role="user", parts=[types.Part.from_text(text=query)])
            chunks: List[str] = []

            async def _stream():
                async for event in runner.run_async(user_id=actor.user_id, session_id=adk_session_id, new_message=msg):
                    content = getattr(event, "content", None)
                    if content and getattr(event, "is_final_response", lambda: True)():
                        for part in getattr(content, "parts", None) or []:
                            if getattr(part, "text", None):
                                chunks.append(part.text)

            await asyncio.wait_for(_stream(), timeout=60.0)
        finally:
            runtime.reset_context(token)
        reply = "".join(chunks).strip()
        if not reply:
            raise RuntimeError("The assistant returned no answer.")
        return reply

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

    async def process_query(self, query: str, session_id: Optional[str] = None, actor: Actor = None) -> Dict[str, Any]:
        """End-to-end pipeline. `actor` is the authenticated user resolved by the API layer from the session cookie."""
        if actor is None:
            raise ServiceError("Authentication required.")
        if not query or not query.strip():
            return {"error": "Message cannot be empty", "reply": "Please enter a valid question or command.", "routed_to": [], "intent": "invalid"}

        session = session_manager.get_or_create_session(session_id, user_id=actor.user_id)
        session.add_message(role="user", content=query)
        q_norm = query.strip().lower().rstrip(".!")

        # ── Explicit confirmation turns are handled deterministically (never by the LLM) ──
        pending = await asyncio.to_thread(pending_actions.latest_pending, actor, session.session_id)
        if pending and q_norm in CONFIRM_WORDS:
            outcome = await asyncio.to_thread(pending_actions.confirm, actor, pending["action_id"])
            reply = ("✅ " if outcome.get("ok") else "⚠️ ") + outcome["message"]
            return self._finish(session, query, reply, ["central_orchestrator"], "action_confirmed" if outcome.get("ok") else "action_failed",
                                "User confirmed a staged action", actor, executed=outcome)
        if pending and q_norm in DECLINE_WORDS:
            await asyncio.to_thread(pending_actions.cancel, actor, pending["action_id"])
            return self._finish(session, query, "No problem - I cancelled that request. Nothing was changed.", ["central_orchestrator"],
                                "action_cancelled", "User declined a staged action", actor)

        routing = await self.classify_and_route(query, session)
        logger.info("Routing decision: intent=%s targets=%s", routing.intent, routing.target_subagents)
        reply, routed_to = "", []

        if routing.intent == "clarification_needed":
            reply, routed_to = routing.clarification_question or "Could you please clarify your request?", ["central_orchestrator"]
        elif routing.intent == "out_of_scope":
            reply = routing.out_of_scope_explanation or "This request is outside the scope of GIKI campus operations."
            routed_to = ["central_orchestrator"]
        elif routing.target_subagents:
            routed_to = list(routing.target_subagents)
            results: Dict[str, str] = {}
            for target in routed_to:  # sequential: at most one staged confirmation, deterministic ordering
                sub_query = routing.subagent_queries.get(target, query)
                try:
                    results[target] = await self.invoke_subagent(target, sub_query, session, actor)
                except Exception as exc:
                    logger.error("Error invoking %s: %s", target, exc)
                    results[target] = (f"⚠️ The {SUBAGENT_LABELS.get(target, target)} could not complete this request right now ({type(exc).__name__}). "
                                       "No changes were made. You can also do this directly from the portal pages.")
            reply = next(iter(results.values())) if len(results) == 1 else await self.synthesize_multi_agent_responses(query, results)
        else:
            reply, routed_to = "I could not identify which part of campus services this relates to. Could you rephrase?", ["central_orchestrator"]

        return self._finish(session, query, reply, routed_to, routing.intent, routing.reasoning, actor)

    def _finish(self, session: Session, query: str, reply: str, routed_to: List[str], intent: str, reasoning: str, actor: Actor,
                executed: Optional[dict] = None) -> Dict[str, Any]:
        staged = pending_actions.pending_for_session(actor, session.session_id)
        if staged:
            lines = "\n".join(f"- {s['summary']}" for s in staged)
            if "confirm" not in reply.lower():
                reply += f"\n\n**Awaiting your confirmation:**\n{lines}\n\nReply **yes** to proceed or **no** to cancel."
        session.add_message(role="assistant", content=reply, routed_to=routed_to, intent=intent, metadata={"reasoning": reasoning})
        return {
            "session_id": session.session_id, "user_id": session.user_id, "query": query, "reply": reply, "routed_to": routed_to,
            "routed_to_labels": [SUBAGENT_LABELS.get(r, r) for r in routed_to], "intent": intent, "reasoning": reasoning,
            "pending_actions": [{"action_id": s["action_id"], "summary": s["summary"], "tool": s["tool"]} for s in staged],
            "executed": executed,
        }


CONFIRM_WORDS = {"yes", "y", "yep", "yeah", "confirm", "confirmed", "ok", "okay", "sure", "go ahead", "do it", "proceed", "please do", "yes please"}
DECLINE_WORDS = {"no", "n", "nope", "cancel", "stop", "dont", "don't", "never mind", "nevermind", "no thanks", "abort"}


# Global singleton instance
central_orchestrator = CentralOrchestrator()
