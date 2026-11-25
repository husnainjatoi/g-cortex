"""Browser tests (desktop + mobile, real backend and database): admin societies, food expenditure, student mess page, degree plan / what-if, society authority."""
import datetime
import json
import os
import subprocess

import pytest

from app import db
from app.services import billing, clock, curriculum, societies
from tests.conftest import PASSWORD
from tests.data import prospectus2025_excerpts as X
from tests.test_curriculum import FCSE, URL, drop_2025_source
from tests.test_frontend import CHROME, PUPPETEER, ROOT, pytestmark  # noqa: F401
from tests.test_frontend_features import server  # noqa: F401  (module-scoped uvicorn on the test database)

MODES = ["desktop", "mobile"]


def run(server, email, mode, scenario, ctx=None):
    r = subprocess.run(["node", str(ROOT / "tests/frontend/round3b_check.mjs"), server, email, PASSWORD, mode, scenario, json.dumps(ctx or {})], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PUPPETEER": PUPPETEER, "CHROME": CHROME})
    assert r.returncode == 0, r.stderr[-1800:]
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["errors"] == [], o["errors"]
    bad = {k: v for k, v in o["checks"].items() if v is not True}
    assert not bad, bad
    assert o["checks"], "scenario ran no checks"                      # guards against a scenario that silently does nothing
    assert o["overflow"] is False
    return o


def eat(uid, day, meal="lunch"):
    with db.tx() as cur:
        cur.execute("INSERT INTO mess_ledger (user_id, meal_date, meal_type, status, source) VALUES (%s,%s,%s,'consumed','turnstile') ON CONFLICT DO NOTHING", (uid, day, meal))


@pytest.fixture
def two_societies(actors):
    a = societies.create_society(actors["req"], "Test Society A", "Has recorded members", "technical")
    b = societies.create_society(actors["req"], "Test Society B", "Has no membership records", "cultural")
    societies.set_membership(actors["req"], a["society_id"], "2024405", "office_bearer")
    yield {"with_members": a["society_id"], "no_members": b["society_id"]}
    with db.tx() as cur:
        cur.execute("DELETE FROM society_requisitions"); cur.execute("DELETE FROM society_memberships WHERE society_id IN (SELECT society_id FROM societies WHERE name ~ 'Test Society')")
        cur.execute("DELETE FROM societies WHERE name ~ 'Test Society'")


@pytest.mark.parametrize("mode", MODES)
def test_admin_society_management(server, mode, two_societies):
    run(server, "dsa2@giki.edu.pk", mode, "admin_societies", {"add_member": "2024668"})
    # the administrator's changes are the same records the student form uses
    assert "Browser Test Society 2" not in [s["name"] for s in societies.list_societies(_actor("2024668"))]   # deactivated -> hidden
    assert db.query_one("SELECT status, manually_edited FROM societies WHERE name='Browser Test Society 2'") == {"status": "inactive", "manually_edited": True}
    assert db.query_one("SELECT COUNT(*) AS n FROM society_memberships WHERE user_id='2024668'")["n"] >= 1
    with db.tx() as cur:
        cur.execute("DELETE FROM societies WHERE name ~ 'Browser Test'")


def _actor(uid):
    from app.services.actor import Actor
    return Actor(uid, "student", frozenset(), "web", "x")


def test_society_management_is_closed_to_other_roles(server, two_societies):
    run(server, "mess@giki.edu.pk", "desktop", "admin_society_denied")


@pytest.mark.parametrize("mode", MODES)
def test_admin_food_expenditure_interface(server, mode, actors):
    today = clock.today()
    for uid in ("2024405", "2024668", "2024662"):
        eat(uid, today.replace(day=1))
    run(server, "mess@giki.edu.pk", mode, "admin_mess", {"month": today.strftime("%Y-%m"), "label": today.strftime("%B %Y"), "users": 3, "per_student": "1,000,000"})
    rows = db.query("SELECT status, amount_pkr, recorded_by, correction_reason FROM mess_expenses ORDER BY expense_id")
    assert [(r["status"], float(r["amount_pkr"])) for r in rows] == [("superseded", 3_000_000.0), ("active", 3_600_000.0)] and rows[1]["recorded_by"] == "ADMMESS"
    assert billing.month_cost_summary(actors["mess"], today.strftime("%Y-%m"))["estimated_per_student_pkr"] == 1_200_000.0


@pytest.mark.parametrize("mode", MODES)
def test_student_mess_page_estimate_invoices_deposit_and_attendance(server, mode, actors):
    with db.tx() as cur:
        cur.execute("UPDATE system_settings SET value='shared_cost' WHERE key='mess_billing_mode'")
    today = clock.today(); prev_last = today.replace(day=1) - datetime.timedelta(days=1)
    for uid in ("2024405", "2024668", "2024662"):
        eat(uid, today.replace(day=1)); eat(uid, prev_last.replace(day=3))
    billing.add_expense(actors["mess"], today.strftime("%Y-%m"), 3000, "test"); billing.add_expense(actors["mess"], prev_last.strftime("%Y-%m"), 3000, "test")
    billing.generate_monthly_invoices(actors["mess"], prev_last.strftime("%Y-%m"))
    for i, m in enumerate(("breakfast", "lunch", "dinner")):
        eat("2024405", today - datetime.timedelta(days=2), m)
    inv = billing.my_invoices(actors["s1"])[0]
    empty = (today.replace(day=1) - datetime.timedelta(days=130)).strftime("%Y-%m")
    run(server, "u2024405@giki.edu.pk", mode, "student_mess", {"disclaimer": billing.ESTIMATE_DISCLAIMER, "estimate": "1,000", "invoice_no": inv["invoice_no"], "empty_month": empty})


@pytest.fixture
def planner_data(actors):
    text = "<<PAGE 47>>\n" + FCSE + X.AI + "\n" + X.MINOR + "\nSchool of Management Sciences\n" + X.MS
    drop_2025_source()
    with db.tx() as cur:
        cur.execute("DELETE FROM program_courses"); cur.execute("DELETE FROM academic_programs")
        cur.execute("INSERT INTO knowledge_sources (url, title, doc_type, status, full_text) VALUES (%s,'Undergraduate Prospectus 2025','pdf','active',%s) ON CONFLICT (url) DO UPDATE SET full_text=EXCLUDED.full_text, status='active'", (URL, text))
    curriculum.import_prospectus_2025(actors["acad"])
    curriculum.init_demo_progress(actors["acad"], ["2024405"], "BSAI")
    yield
    with db.tx() as cur:
        cur.execute("DELETE FROM student_course_progress"); cur.execute("DELETE FROM course_history WHERE term='Demo assumption'")
        cur.execute("UPDATE student_profiles SET program_id=NULL, is_demo=FALSE"); cur.execute("DELETE FROM course_history WHERE course_code IN (SELECT course_code FROM courses WHERE origin='official')")
        cur.execute("DELETE FROM program_courses"); cur.execute("DELETE FROM academic_programs")
        cur.execute("DELETE FROM course_prerequisites WHERE course_code IN (SELECT course_code FROM courses WHERE origin='official')")
        cur.execute("DELETE FROM course_sources"); cur.execute("DELETE FROM course_import_flags"); cur.execute("DELETE FROM courses WHERE origin='official'")
    drop_2025_source()
    with db.tx() as cur:
        cur.execute("DELETE FROM degree_minors WHERE minor_name='Management for Engineers'"); cur.execute("UPDATE knowledge_sources SET status='active' WHERE url ~ 'UG_Prospectus_2021'")


@pytest.mark.parametrize("mode", MODES)
def test_degree_plan_what_if_and_official_minors(server, mode, planner_data):
    run(server, "u2024405@giki.edu.pk", mode, "student_planner", {"own_programme": "BS Artificial Intelligence", "other_code": "BSMS"})


@pytest.mark.parametrize("mode", MODES)
def test_society_reservation_authority_cases(server, mode, two_societies):
    ctx = {"societies": two_societies, "date": (clock.today() + datetime.timedelta(days=10)).isoformat()}
    run(server, "u2024405@giki.edu.pk", mode, "society_reservation", {**ctx, "expect": "member"})               # recorded office-bearer: verified
    assert db.query_one("SELECT authority_verified FROM society_requisitions WHERE society_id=%s", (two_societies["with_members"],))["authority_verified"] is True
    run(server, "u2024668@giki.edu.pk", mode, "society_reservation", {**ctx, "expect": "non_member"})         # not on the society's membership list: refused, even via a direct API call
    assert db.query_one("SELECT COUNT(*) AS n FROM society_requisitions WHERE applicant_id='2024668'")["n"] == 0
    with db.tx() as cur:
        cur.execute("DELETE FROM society_requisitions")
    run(server, "u2024405@giki.edu.pk", mode, "society_reservation", {**ctx, "expect": "unverified"})          # society without membership data: accepted but flagged for an administrator
    assert db.query_one("SELECT authority_verified FROM society_requisitions WHERE society_id=%s", (two_societies["no_members"],))["authority_verified"] is False
    run(server, "dsa2@giki.edu.pk", mode, "admin_reviews_reservations")
