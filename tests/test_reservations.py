"""Official societies import + individual/society venue reservations (same service for the website and the assistant)."""
import datetime

import pytest
from fastapi.testclient import TestClient

from app import db
from app.services import clock, requisitions as rq, societies
from app.services.errors import Conflict, Forbidden, NotFound, RuleViolation, ValidationFailed
from tests.conftest import PASSWORD

# Verbatim markup shape of https://giki.edu.pk/campus-life/student-socities/ (three real entries, shortened descriptions)
PAGE = """<html><body><div class="gdlr-core-page-builder-body clearfix">
<div class="x"><h6 class="subheading">GIK WebTeam</h6>
<p><img class="alignleft lazyload" data-src="https://www.giki.edu.pk/images/SocietyLogos/WebTeam.png" alt="" src="data:image/svg+xml;base64,AAA" /></p><p>The GIK Webteam is an in-house team of students that voluntarily design and manage the GIK website.</p></div>
<div class="x"><h6 class="subheading">ACM –&nbsp;(Association For Computing Machinery)</h6>
<p><img data-src="https://www.giki.edu.pk/images/SocietyLogos/ACM.png" /></p><p>The ACM student chapter promotes computing and holds programming contests.</p></div>
<div class="x"><h6 class="subheading">IET – (Insitute of Engineering And Technoloogy) GIK Chapter</h6>
<p>The IET GIK chapter organises seminars, workshops and competitions for engineering students.</p></div>
<div class="kingster-footer-wrapper">footer text</div></div></body></html>"""


@pytest.fixture
def imported(actors):
    societies.import_official(actors["req"], html_override=PAGE)
    yield
    with db.tx() as cur:
        cur.execute("DELETE FROM society_requisitions"); cur.execute("DELETE FROM society_memberships WHERE society_id IN (SELECT society_id FROM societies WHERE origin IN ('official','manual'))")
        cur.execute("DELETE FROM societies WHERE origin IN ('official','manual')"); cur.execute("UPDATE societies SET status='active' WHERE origin='legacy'")


def sid(name_part):
    return db.query_one("SELECT society_id FROM societies WHERE name ILIKE %s AND origin<>'legacy'", (f"%{name_part}%",))["society_id"]


def day(n):
    return (clock.today() + datetime.timedelta(days=n)).isoformat()


def reserve(actor, **kw):
    args = dict(reservation_type="individual", venue_id="SEM_HALL_1", event_title="Gathering", booking_date=day(10), start_time="14:00", duration_hours=2, expected_attendees=30, justification="small meeting")
    args.update(kw)
    return rq.submit_requisition(actor, **args)


def test_parse_every_society_with_logo_and_full_description():
    items = societies.parse_societies(PAGE)
    assert [i["name"] for i in items] == ["GIK WebTeam", "ACM – (Association For Computing Machinery)", "IET – (Insitute of Engineering And Technoloogy) GIK Chapter"]
    assert items[0]["logo_url"] == "https://www.giki.edu.pk/images/SocietyLogos/WebTeam.png" and items[2]["logo_url"] is None
    assert "voluntarily design" in items[0]["description"] and "footer" not in items[2]["description"]


def test_import_is_idempotent_and_respects_manual_edits(actors, imported):
    n = db.query_one("SELECT COUNT(*) AS n FROM societies WHERE origin='official'")["n"]
    assert n == 3
    again = societies.import_official(actors["req"], html_override=PAGE)
    assert again["created"] == 0 and db.query_one("SELECT COUNT(*) AS n FROM societies WHERE origin='official'")["n"] == 3
    acm = sid("ACM")
    societies.update_society(actors["req"], acm, description="Edited by an administrator", category="technical")
    societies.import_official(actors["req"], html_override=PAGE)
    row = societies.get_society(acm)
    assert row["description"] == "Edited by an administrator" and row["manually_edited"] and row["source_url"].startswith("https://giki.edu.pk/")
    with pytest.raises(Forbidden):
        societies.import_official(actors["s1"], html_override=PAGE)
    with pytest.raises(ValidationFailed):
        societies.import_official(actors["req"], html_override="<html>nothing here</html>")        # empty parse never wipes data


def test_societies_are_manageable_by_admins_only(actors, imported):
    with pytest.raises(Forbidden):
        societies.update_society(actors["s1"], sid("ACM"), status="inactive")
    societies.update_society(actors["req"], sid("WebTeam"), status="inactive")
    assert "GIK WebTeam" not in [s["name"] for s in societies.list_societies(actors["s1"])]
    assert "GIK WebTeam" in [s["name"] for s in societies.list_societies(actors["req"], include_inactive=True)]
    c = societies.create_society(actors["req"], "Robotics Club", "Builds robots", "technical")
    assert c["origin"] == "manual"
    with pytest.raises(Conflict):
        societies.create_society(actors["req"], "robotics   club")
    with pytest.raises(ValidationFailed):
        societies.create_society(actors["req"], "ab")


def test_individual_reservation_uses_the_students_own_account(actors, imported):
    r = reserve(actors["s1"])
    assert r["reservation_type"] == "individual" and r["status"] == "pending_admin_approval" and "not confirmed" in r["message"]
    row = db.query_one("SELECT applicant_id, reservation_type, society_id, society_name, status FROM society_requisitions WHERE requisition_id=%s", (r["requisition_id"],))
    assert row == {"applicant_id": "2024405", "reservation_type": "individual", "society_id": None, "society_name": None, "status": "pending_admin_approval"}
    assert db.query_one("SELECT COUNT(*) AS n FROM bookings WHERE requisition_id=%s", (r["requisition_id"],))["n"] == 0      # nothing is confirmed by submitting
    view = rq.get_requisition(actors["s1"], r["requisition_id"])
    assert view["reservation_type"] == "individual" and view["stages"][0]["state"] == "current" and view["pending_authority"] == "Directorate of Student Affairs"
    with pytest.raises(Forbidden):
        rq.review_requisition(actors["s1"], r["requisition_id"], "approve")
    with pytest.raises(Forbidden):
        rq.review_requisition(actors["fac"], r["requisition_id"], "approve")                       # no advisor stage for individuals
    assert rq.review_requisition(actors["req"], r["requisition_id"], "approve")["status"] == "approved"
    assert db.query_one("SELECT status FROM bookings WHERE requisition_id=%s", (r["requisition_id"],))["status"] == "approved"
    assert any(x["reservation_type"] == "individual" for x in rq.list_requisitions(actors["req"], "all"))


def test_society_reservation_requires_authority_and_follows_the_approval_chain(actors, imported):
    acm = sid("ACM")
    societies.update_society(actors["req"], acm, advisor_id="FAC001")
    societies.set_membership(actors["req"], acm, "2024662", "office_bearer")
    with pytest.raises(Forbidden) as e:
        reserve(actors["s2"], reservation_type="society", society_id=acm)                          # society has members; s2 is not one
    assert "not recorded as a member" in str(e.value)
    r = reserve(actors["s3"], reservation_type="society", society_id=acm, event_title="Coding contest", expected_attendees=80)
    row = db.query_one("SELECT applicant_id, reservation_type, society_id, authority_verified, status FROM society_requisitions WHERE requisition_id=%s", (r["requisition_id"],))
    assert row == {"applicant_id": "2024662", "reservation_type": "society", "society_id": acm, "authority_verified": True, "status": "pending_advisor_approval"}
    assert [x["requisition_id"] for x in rq.list_requisitions(actors["fac"], "assigned")] == [r["requisition_id"]]
    assert rq.review_requisition(actors["fac"], r["requisition_id"], "approve")["status"] == "pending_admin_approval"
    assert rq.review_requisition(actors["req"], r["requisition_id"], "approve")["status"] == "approved"
    assert rq.get_requisition(actors["s3"], r["requisition_id"])["society_display"].startswith("ACM")


def test_unverifiable_authority_goes_to_administrators_not_auto_confirmed(actors, imported):
    iet = sid("IET")                                           # no members and no advisor recorded
    r = reserve(actors["s2"], reservation_type="society", society_id=iet, expected_attendees=100)
    row = db.query_one("SELECT authority_verified, status, approval_chain FROM society_requisitions WHERE requisition_id=%s", (r["requisition_id"],))
    assert row["authority_verified"] is False and row["status"] == "pending_admin_approval" and "could not be verified" in row["approval_chain"]
    assert db.query_one("SELECT COUNT(*) AS n FROM bookings WHERE requisition_id=%s", (r["requisition_id"],))["n"] == 0
    assert any("awaits clearance" in n["body"] for n in db.query("SELECT body FROM notifications WHERE user_id='ADMREQ'"))


def test_both_types_share_the_same_validation_rules(actors, imported):
    acm = sid("ACM")
    societies.set_membership(actors["req"], acm, "2024662", "member")
    for kw in ({"reservation_type": "individual"}, {"reservation_type": "society", "society_id": acm}):
        with pytest.raises(RuleViolation) as e:
            reserve(actors["s3"], expected_attendees=500, **kw)
        assert e.value.code == "capacity_exceeded"
        with pytest.raises(RuleViolation):
            reserve(actors["s3"], booking_date=day(1), **kw)                                         # notice period
        with pytest.raises(NotFound):
            reserve(actors["s3"], venue_id="NOWHERE", **kw)
    with pytest.raises(ValidationFailed):
        reserve(actors["s3"], reservation_type="group")
    with pytest.raises(ValidationFailed):
        reserve(actors["s3"], reservation_type="society")                                            # society required
    with pytest.raises(NotFound):
        reserve(actors["s3"], reservation_type="society", society_id=999999)
    first = reserve(actors["s3"])
    rq.review_requisition(actors["req"], first["requisition_id"], "approve")
    with pytest.raises(RuleViolation) as e:                                                         # an approved individual booking blocks a society one
        reserve(actors["s3"], reservation_type="society", society_id=acm, start_time="14:30")
    assert e.value.code == "venue_conflict"


def test_open_reservation_quota_counts_both_types(actors, imported):
    acm = sid("ACM"); societies.set_membership(actors["req"], acm, "2024662", "member")
    reserve(actors["s3"], booking_date=day(11)); reserve(actors["s3"], booking_date=day(12), reservation_type="society", society_id=acm); reserve(actors["s3"], booking_date=day(13))
    with pytest.raises(RuleViolation) as e:
        reserve(actors["s3"], booking_date=day(14), reservation_type="society", society_id=acm)
    assert "awaiting approval" in str(e.value)


def test_legacy_societies_are_retired_but_history_and_membership_carry_over(actors, imported):
    acm = sid("ACM")
    assert db.query_one("SELECT status FROM societies WHERE origin='legacy' AND name='ACM GIKI Chapter'")["status"] == "inactive"
    members = {m["user_id"] for m in societies.members(actors["req"], acm)}
    assert {"2024405", "2024662"} <= members                                                         # demo memberships followed the society


def test_reservation_over_http_with_trusted_venue_lookup(actors, imported):
    import main
    c = TestClient(main.app, base_url="http://testserver")
    c.post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD})
    v = c.get("/api/req/venues/SEM_HALL_1").json()
    assert v["venue_id"] == "SEM_HALL_1" and v["capacity"] > 0 and c.get("/api/req/venues/NOPE").status_code == 404
    soc = c.get("/api/req/societies").json()
    assert {s["name"] for s in soc["all"]} >= {"GIK WebTeam"} and isinstance(soc["mine"], list)
    ok = c.post("/api/req/requisitions", json={"reservation_type": "individual", "venue_id": v["venue_id"], "event_title": "Study group", "booking_date": day(9), "start_time": "10:00",
                                                "duration_hours": 2, "expected_attendees": 20, "justification": "exam prep"})
    assert ok.status_code == 200 and ok.json()["reservation_type"] == "individual"
    bad = c.post("/api/req/requisitions", json={"reservation_type": "individual", "venue_id": "SEM_HALL_1", "event_title": "", "booking_date": day(9), "start_time": "10:00", "duration_hours": 2, "expected_attendees": 0})
    assert bad.status_code == 422 and "String should" not in bad.text and "fields" in bad.json()
    assert c.post("/api/req/admin/societies/import").status_code == 403
    assert c.get("/api/req/admin/societies").status_code == 403


def test_admin_society_changes_reach_the_student_form_the_assistant_and_survive_reimport(actors, imported):
    from app.agents import runtime
    from app.agents.tools import req_tools
    from tests.test_api import client, login
    import main
    stu, _ = login(main.app, "u2024405@giki.edu.pk")
    adm, _ = login(main.app, "dsa2@giki.edu.pk")
    assert stu.get("/api/req/admin/societies").status_code == 403 and stu.post("/api/req/admin/societies", json={"name": "Hackers"}).status_code == 403
    assert stu.post("/api/req/admin/societies/import").status_code == 403
    assert adm.post("/api/req/admin/societies", json={"name": "Quantum Computing Society", "description": "Qubits"}).status_code == 200
    names = lambda r: [s["name"] for s in r.json()["all"]]
    assert "Quantum Computing Society" in names(stu.get("/api/req/societies"))                        # the New Requisition dropdown source
    tok = runtime.set_context(actors["s1"], "t")
    try:
        assert "Quantum Computing Society" in [x["name"] for x in req_tools.list_societies()["societies"]]            # same table feeds the assistant
    finally:
        runtime.reset_context(tok)
    q = sid("Quantum")
    assert adm.put(f"/api/req/admin/societies/{q}", json={"status": "inactive"}).status_code == 200
    assert "Quantum Computing Society" not in names(stu.get("/api/req/societies"))                    # deactivation is immediate
    tok = runtime.set_context(actors["s1"], "t")
    try:
        assert "Quantum Computing Society" not in [x["name"] for x in req_tools.list_societies()["societies"]]
    finally:
        runtime.reset_context(tok)
    r = stu.post("/api/req/requisitions", json={"reservation_type": "society", "society_id": q, "venue_id": "SEM_HALL_1", "event_title": "x", "booking_date": day(10), "start_time": "14:00",
                                                "duration_hours": 2, "expected_attendees": 10})
    assert r.status_code == 409 and "not an active society" in r.json()["detail"]                       # the backend refuses even if the form is bypassed
    societies.update_society(actors["req"], sid("ACM"), description="Edited by an administrator")
    societies.import_official(actors["req"], html_override=PAGE)
    assert societies.get_society(sid("ACM"))["description"] == "Edited by an administrator"
