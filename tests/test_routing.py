"""Routing regression tests (incl. the original equipment-routing failure) and the full equipment request lifecycle.

Root cause of the original failure: when the LLM classifier was rate-limited/unavailable the keyword fallback had no
equipment vocabulary and routed 'borrow an FPGA development board' to the Course agent. The tests below pin the fix at
every layer: the generated prompt, the deterministic classifier, the real model, and the end-to-end database effects.
"""
import os
import time

import pytest

from app import db
from app.agents.central_orchestrator import central_orchestrator as orch
from app.agents.central_orchestrator.prompts import ROUTING_PROMPT, build_domain_descriptions
from app.agents.central_orchestrator.session_manager import Session
from tests.test_assistant import Chat, app, session  # noqa: F401  (reuse helpers/fixtures)

REQ = "requisition_management_subagent"
EQUIPMENT_QUERIES = [
    "Show me the available lab equipment.", "I need to borrow an FPGA development board.", "Check who currently has this equipment.",
    "What is the status of my equipment request?", "Return the equipment assigned to me.", "Show me the custody history of this barcode.",
    "Which lab equipment is currently unavailable?", "Please borrow the Basys 3 FPGA board BAR-FPGA-302 for my digital design project.",
]
MESS_QUERIES = ["How much is my mess bill this month?", "How much is my refundable mess security?", "Show me last month's bill", "What is for dinner tonight?"]
COURSE_QUERIES = ["Register me for CS325", "What are the prerequisites for CS412?", "Which courses can I register for next semester?", "What is the add/drop policy?"]


@pytest.mark.parametrize("q", EQUIPMENT_QUERIES)
def test_fallback_classifier_routes_equipment_to_requisition(q):
    r = orch._fallback_classify_and_route(q, Session(user_id="x"))
    assert r.intent == "single_subagent" and r.target_subagents == [REQ], (q, r)


@pytest.mark.parametrize("q", MESS_QUERIES)
def test_fallback_classifier_routes_mess(q):
    assert orch._fallback_classify_and_route(q, Session(user_id="x")).target_subagents == ["mess_management_subagent"]


@pytest.mark.parametrize("q", COURSE_QUERIES)
def test_fallback_classifier_routes_courses(q):
    assert orch._fallback_classify_and_route(q, Session(user_id="x")).target_subagents == ["course_management_subagent"]


def test_fallback_does_not_default_to_course_when_unclear():
    for q in ("hello", "Check my status", "can you help me with something"):
        assert orch._fallback_classify_and_route(q, Session(user_id="x")).intent == "clarification_needed"
    assert orch._fallback_classify_and_route("What's the weather in Tokyo?", Session(user_id="x")).intent == "out_of_scope"


def test_fallback_multi_domain_and_followup_context():
    r = orch._fallback_classify_and_route("What is on the dinner menu and can I book badminton court 1 tomorrow evening?", Session(user_id="x"))
    assert r.intent == "multi_subagent" and set(r.target_subagents) == {"mess_management_subagent", REQ}
    s = Session(user_id="x")
    s.add_message("user", "What are the prerequisites for CS412?"); s.add_message("assistant", "CS330 and MT201", routed_to=["course_management_subagent"])
    f = orch._fallback_classify_and_route("And what about their prerequisites?", s)
    assert f.target_subagents == ["course_management_subagent"]
    # a bare 'yes' with no context must not be silently routed anywhere
    assert orch._fallback_classify_and_route("yes", Session(user_id="x")).intent == "clarification_needed"


def test_routing_prompt_is_generated_from_real_tools_and_has_equipment_rule():
    d = build_domain_descriptions()
    from app.agents.tools.course_tools import COURSE_TOOLS
    from app.agents.tools.mess_tools import MESS_TOOLS
    from app.agents.tools.req_tools import REQ_TOOLS
    for t in (*COURSE_TOOLS, *MESS_TOOLS, *REQ_TOOLS):
        assert t.__name__ in d, f"{t.__name__} missing from routing prompt"
    assert "rebate" not in d.lower()
    assert "equipment" in d.split("requisition_management_subagent")[1][:600].lower()
    assert "ALWAYS goes to" in d and "BAR-" in d
    assert "{domains}" in ROUTING_PROMPT and "Example requests" in d and "Supported operations" in d
    prompt = ROUTING_PROMPT.format(domains=d, history="None", query="x")
    assert "lookup_equipment_by_barcode" in prompt and "get_my_estimated_mess_bill" in prompt


# ── Real model + real database ──────────────────────────────

llm = [pytest.mark.llm, pytest.mark.skipif(not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")), reason="no LLM key configured")]


def _classify_with_llm(q):
    import asyncio
    for attempt in range(4):
        r = asyncio.run(orch.classify_and_route(q, Session(user_id="x")))
        if not r.reasoning.startswith("Deterministic routing"):
            return r
        time.sleep(20 * (attempt + 1))
    pytest.skip("LLM classifier unavailable (rate limit)")


@pytest.mark.llm
@pytest.mark.skipif(not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")), reason="no LLM key configured")
@pytest.mark.parametrize("q", EQUIPMENT_QUERIES)
def test_llm_classifier_routes_equipment_to_requisition(q):
    r = _classify_with_llm(q)
    assert r.target_subagents == [REQ], (q, r)
    time.sleep(3)


def _eq_state(code):
    return db.query_one("SELECT status, custodian_id FROM lab_equipment WHERE barcode=%s", (code,))


@pytest.mark.llm
@pytest.mark.skipif(not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")), reason="no LLM key configured")
def test_equipment_request_lifecycle_through_assistant(app):
    web = session(app, "u2024405@giki.edu.pk")
    chat = Chat(web)
    # 1. availability search -> routed to requisition, answer comes from real inventory
    out = chat.say("Show me the available lab equipment.")
    assert out["routed_to"] == [REQ] and "BAR-" in out["reply"]
    # 2. unavailable items (seed has none in custody after reset) -> still routed correctly
    assert chat.say("Which lab equipment is currently unavailable?")["routed_to"] == [REQ]
    # 3. borrow by description -> staged, nothing written yet
    out = chat.say("I need to borrow an FPGA development board for 5 days for my digital design project.")
    assert out["routed_to"] == [REQ]
    if not out["pending_actions"]:                      # the agent may legitimately ask which board / confirm purpose first
        out = chat.say("Yes, the Basys 3 board BAR-FPGA-302 for my digital design project, 5 days.")
    assert out["pending_actions"], out["reply"]
    assert _eq_state("BAR-FPGA-302")["status"] == "available"
    done = chat.say("yes")
    assert done["executed"]["verified"] and _eq_state("BAR-FPGA-302") == {"status": "in_custody", "custodian_id": "2024405"}
    # 4. status of my equipment request / barcode identification / custody history
    mine = chat.say("What is the status of my equipment request?")
    assert mine["routed_to"] == [REQ] and ("BAR-FPGA-302" in mine["reply"] or "Basys" in mine["reply"])
    hist = chat.say("Show me the custody history of barcode BAR-FPGA-302.")
    assert hist["routed_to"] == [REQ] and ("checkout" in hist["reply"].lower() or "borrow" in hist["reply"].lower() or "checked out" in hist["reply"].lower())
    # 5. another student asking who has it: routed to requisition, custodian identity NOT disclosed
    other = Chat(session(app, "u2024668@giki.edu.pk"))
    who = other.say("Check who currently has the equipment with barcode BAR-FPGA-302.")
    assert who["routed_to"] == [REQ] and "2024405" not in who["reply"] and "Husnain" not in who["reply"]
    # 6. return -> staged -> confirmed -> database updated
    out = chat.say("Return the equipment assigned to me.")
    assert out["routed_to"] == [REQ] and out["pending_actions"], out["reply"]
    assert chat.say("yes")["executed"]["verified"]
    assert _eq_state("BAR-FPGA-302") == {"status": "available", "custodian_id": None}
    assert [r["action"] for r in db.query("SELECT action FROM equipment_custody_log WHERE barcode='BAR-FPGA-302' ORDER BY log_id")] == ["checkout", "return"]
