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
    build_domain_descriptions,
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


class ModelBusy(Exception):
    """The model service is rate limited or temporarily unavailable (HTTP 429/503)."""


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
                    domains=build_domain_descriptions(),
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

    # Weighted evidence per domain for the deterministic classifier (used when the LLM classifier is unavailable).
    _DOMAIN_PATTERNS = {
        "requisition_management_subagent": [
            (4, r"\bBAR-[A-Z0-9-]+\b"), (4, r"\b(equipment|equipments|apparatus|instrument|instruments|oscilloscope|multimeter|logic analy[sz]er|signal generator|function generator|"
                                         r"fpga|basys|arduino|raspberry pi|microcontroller|dev(elopment)? board|lab kit|lab (item|gear|device)|barcode|custody|custodian)\b"),
            (3, r"\b(borrow|borrowed|lend|lent|check ?out|checked out|issue me|issued to|hand ?over)\b.*\b(board|kit|device|item|equipment|scope|meter|analy[sz]er|generator|pi)\b"),
            (2, r"\b(requisition|requisitions|society|societies|venue|venues|auditorium|seminar hall|conference room|booking|bookings|reservation|reserve|sports?|gym|"
                r"badminton|squash|tennis|futsal|table tennis|court|facility|facilities|no-?show|check-?in)\b"),
            (2, r"\b(return|returned|returning)\b.*\b(assigned|borrowed|equipment|board|kit|item|it)\b"), (2, r"\bwho (has|is holding|holds|borrowed|took)\b"),
            (1, r"\b(unavailable|under repair|in custody|inventory)\b")],
        "mess_management_subagent": [
            (4, r"\b(mess|dining|canteen|cafeteria)\b"), (4, r"\b(bill|bills|billing|invoice|invoices|challan|dues|outstanding|security deposit|mess security|refundable|clearance)\b"),
            (3, r"\b(meal|meals|menu|breakfast|lunch|dinner|calories|protein|carbs|macros?|nutrition(al)?|allerg(en|ens|y|ies)|food|kitchen|turnstile|qr)\b"),
            (3, r"\bmess[- ]?out\b"), (2, r"\b(attendance|ate|eaten|consumption)\b.*\b(meal|mess|day|month)\b"), (1, r"\b(gate[- ]?pass|headcount|forecast)\b")],
        "course_management_subagent": [
            (4, r"(?-i:\b[A-Z]{2,4} ?\d{3}\b)|\b[a-z]{2,4}\d{3}\b"), (4, r"\b(course|courses|prereq(uisite)?s?|enrol(l|ment)|register(ed|ing)? for|registration|drop(ping)? (a |the )?course|add/drop|credit hours?|credits)\b"),
            (3, r"\b(timetable|time table|class(es)? clash|schedule|lecture|elective|electives|minor|degree|graduation|what-?if|cgpa|gpa|probation|semester|syllabus|curriculum|academic)\b"),
            (2, r"\b(policy|policies|regulation|handbook|rules?)\b.*\b(academic|exam|attendance|grading|probation|degree|registration)\b")],
    }

    def _fallback_classify_and_route(self, query: str, session: Session) -> IntentResult:
        """Deterministic, evidence-weighted classifier (no LLM). It routes by domain vocabulary and, when evidence is missing or tied, asks the
        user to clarify instead of silently defaulting to one subagent."""
        q = query.strip()
        q_lower = q.lower()
        history = session.get_recent_history(max_turns=6)

        if re.search(r"\b(weather|flight|airline|hotel booking|uber|crypto|bitcoin|stock market|world cup|recipe for pizza)\b", q_lower):
            return IntentResult(intent="out_of_scope", out_of_scope_explanation=(
                "I am the **G-Cortex assistant** for GIKI. I can help with **courses and academics**, the **mess** (bills, security deposit, meals) and **campus resources** "
                "(sports bookings, venue requisitions, lab equipment). I cannot help with external services such as flights, weather or markets."), reasoning="Outside campus operations")

        scores = {name: sum(w for w, pat in pats if re.search(pat, q, re.I)) for name, pats in self._DOMAIN_PATTERNS.items()}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top_name, top = ranked[0]

        if top == 0:
            # follow-ups ("and what about their prerequisites?", "yes") inherit the previous subagent
            last_agent = next((m.routed_to[0] for m in reversed(history) if m.routed_to and m.routed_to[0] in scores), None)
            if last_agent and (history and len(q.split()) <= 12):
                resolved = q
                codes = re.findall(r"\b([A-Z]{2,4}\d{3})\b", " ".join(m.content for m in history[-3:]), re.I)
                if last_agent == "course_management_subagent" and codes and re.search(r"\b(their|its|it|them)\b", q_lower):
                    resolved = f"{q} (regarding {codes[-1].upper()})"
                return IntentResult(intent="single_subagent", target_subagents=[last_agent], subagent_queries={last_agent: resolved}, reasoning="Follow-up resolved from conversation context")
            return IntentResult(intent="clarification_needed", reasoning="No domain evidence in the request", clarification_question=(
                "I can help with:\n1. **Courses & academics** (registration, prerequisites, timetable, policies)\n2. **Mess** (bills, security deposit, meals, menu)\n"
                "3. **Campus resources** (sports bookings, venue requisitions, lab equipment)\n\nWhich of these is your request about?"))

        strong = [n for n, sc in ranked if sc >= 2 and sc >= top - 1]
        targets = strong if len(strong) > 1 else [top_name]
        queries = {t: q for t in targets}
        return IntentResult(intent="multi_subagent" if len(targets) > 1 else "single_subagent", target_subagents=targets, subagent_queries=queries,
                            reasoning="Deterministic routing by evidence: " + ", ".join(f"{n.split('_')[0]}={sc}" for n, sc in ranked))

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

            try:
                await asyncio.wait_for(_stream(), timeout=60.0)
            except Exception as exc:
                text = f"{type(exc).__name__} {exc}"
                if any(k in text for k in ("RESOURCE_EXHAUSTED", "429", "UNAVAILABLE", "503", "overloaded")):
                    raise ModelBusy(text[:200]) from exc
                raise
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
        reply, routed_to, busy = "", [], False

        if routing.intent == "clarification_needed":
            reply, routed_to = routing.clarification_question or "Could you please clarify your request?", ["central_orchestrator"]
        elif routing.intent == "out_of_scope":
            reply = routing.out_of_scope_explanation or "This request is outside the scope of GIKI campus operations."
            routed_to = ["central_orchestrator"]
        elif routing.target_subagents:
            routed_to = list(routing.target_subagents)
            results: Dict[str, str] = {}
            busy = False
            for target in routed_to:  # sequential: at most one staged confirmation, deterministic ordering
                sub_query = routing.subagent_queries.get(target, query)
                try:
                    results[target] = await self.invoke_subagent(target, sub_query, session, actor)
                except ModelBusy:
                    busy = True
                    results[target] = ("⏳ The AI assistant is receiving too many requests right now. Please try again in about a minute. "
                                       "No changes were made.")
                except Exception as exc:
                    logger.error("Error invoking %s: %s", target, exc)
                    results[target] = (f"⚠️ The {SUBAGENT_LABELS.get(target, target)} could not complete this request right now ({type(exc).__name__}). "
                                       "No changes were made. You can also do this directly from the portal pages.")
            reply = next(iter(results.values())) if len(results) == 1 else await self.synthesize_multi_agent_responses(query, results)
        else:
            reply, routed_to = "I could not identify which part of campus services this relates to. Could you rephrase?", ["central_orchestrator"]

        return self._finish(session, query, reply, routed_to, "model_busy" if (routing.target_subagents and routing.intent not in ("clarification_needed", "out_of_scope") and busy) else routing.intent, routing.reasoning, actor)

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
