"""HTTP-level tests: authentication, backend-enforced RBAC, and the manual portal flows over the real API + database."""
import pytest
from fastapi.testclient import TestClient

from tests.conftest import PASSWORD


@pytest.fixture(scope="module")
def app():
    import main
    return main.app


def client(app):
    return TestClient(app, base_url="http://testserver")


def login(app, email):
    c = client(app)
    r = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c, r.json()


def test_unauthenticated_requests_are_rejected(app):
    c = client(app)
    for path in ("/api/courses", "/api/mess/menu", "/api/req/venues", "/api/portal/dashboard", "/api/admin/users", "/api/auth/me"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/orchestrator/chat", json={"message": "hi"}).status_code == 401
    assert c.get("/health").status_code == 200


def test_student_and_admin_login_roles_come_from_database(app):
    c, me = login(app, "u2024405@giki.edu.pk")
    assert me["role"] == "student" and me["scopes"] == []
    assert "set-cookie" in c.post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD}).headers
    cookie = c.post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD}).headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    a, adm = login(app, "dsa@giki.edu.pk")
    assert adm["role"] == "admin" and "super" in adm["scopes"]
    # client-supplied role fields are ignored
    r = client(app).post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD, "role": "admin"})
    assert r.json()["role"] == "student"


def test_wrong_credentials(app):
    c = client(app)
    assert c.post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": "nope-nope-1"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "ghost@giki.edu.pk", "password": "nope-nope-1"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "", "password": ""}).status_code == 422


def test_student_cannot_reach_admin_apis(app):
    c, _ = login(app, "u2024405@giki.edu.pk")
    for method, path, body in [("get", "/api/admin/users", None), ("get", "/api/admin/reports/overview", None), ("get", "/api/admin/audit", None),
                               ("post", "/api/admin/courses", {"course_code": "ZZ900", "title": "x", "credits": 3}),
                               ("delete", "/api/admin/courses/CS101", None), ("put", "/api/admin/settings/mess_deposit_amount", {"value": "1"}),
                               ("get", "/api/mess/admin/invoices", None), ("get", "/api/mess/admin/attendance", None),
                               ("get", "/api/req/admin/bookings", None), ("post", "/api/req/admin/auto-release", None),
                               ("get", "/api/req/requisitions?scope=all", None)]:
        r = getattr(c, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 403, (path, r.status_code, r.text)


def test_scoped_admins_are_separated(app):
    mess, _ = login(app, "mess@giki.edu.pk")
    acad, _ = login(app, "registrar@giki.edu.pk")
    assert mess.get("/api/mess/admin/invoices").status_code == 200
    assert acad.get("/api/mess/admin/invoices").status_code == 403
    assert acad.post("/api/admin/courses", json={"course_code": "ZZ901", "title": "Scoped", "credits": 2}).status_code == 200
    assert mess.post("/api/admin/courses", json={"course_code": "ZZ902", "title": "Scoped", "credits": 2}).status_code == 403
    assert mess.get("/api/admin/users").status_code == 403


def test_student_cannot_read_another_students_records(app):
    c, _ = login(app, "u2024405@giki.edu.pk")
    for path in ("/api/courses/me/registrations?student_id=2024668", "/api/mess/me/deposit?student_id=2024668",
                 "/api/mess/mess-out?student_id=2024668", "/api/req/sports/bookings?student_id=2024668", "/api/courses/me/record?student_id=2024668"):
        assert c.get(path).status_code == 403, path
    assert c.post("/api/courses/CS271/register", json={"student_id": "2024668"}).status_code == 403
    assert c.post("/api/mess/mess-out", json={"start_date": "2030-01-01", "end_date": "2030-01-05", "student_id": "2024668"}).status_code == 403


def test_course_registration_over_http(app):
    c, _ = login(app, "u2024405@giki.edu.pk")
    r = c.post("/api/courses/CS271/register")
    assert r.status_code == 200 and r.json()["ok"]
    assert [x["course_code"] for x in c.get("/api/courses/me/registrations").json()["courses"]] == ["CS271"]
    dup = c.post("/api/courses/CS271/register")
    assert dup.status_code == 409
    blocked = c.post("/api/courses/CS412/register")                   # CS330 + MT201 prerequisites unmet
    assert blocked.status_code == 409 and blocked.json()["code"] == "prerequisites_unmet"
    assert c.post("/api/courses/NOPE1/register").status_code == 404
    assert c.post("/api/courses/CS271/drop").status_code == 200


def test_admin_adds_course_student_sees_it_and_can_register(app):
    adm, _ = login(app, "dsa@giki.edu.pk")
    r = adm.post("/api/admin/courses", json={"course_code": "ZZ950", "title": "Edge AI", "credits": 3, "prerequisites": ["CS102"], "slot": "Sa 10:00-11:30",
                                             "capacity": 2, "category": "elective", "domains": "edge ai, embedded"})
    assert r.status_code == 200, r.text
    stu, _ = login(app, "u2024668@giki.edu.pk")
    assert any(x["course_code"] == "ZZ950" for x in stu.get("/api/courses").json())
    assert stu.get("/api/courses/ZZ950").json()["prerequisites"] == ["CS102"]
    assert stu.post("/api/courses/ZZ950/register").status_code == 200
    students = adm.get("/api/admin/courses/ZZ950/students").json()
    assert [s["user_id"] for s in students] == ["2024668"]
    assert adm.put("/api/admin/courses/ZZ950", json={"title": "Edge AI Systems", "capacity": 1}).status_code == 200
    assert adm.put("/api/admin/courses/ZZ950", json={"capacity": 0}).status_code == 409          # below current registrations
    assert stu.get("/api/courses/ZZ950").json()["title"] == "Edge AI Systems"
    assert adm.delete("/api/admin/courses/ZZ950").json()["action"] == "deactivated"
    assert stu.get("/api/courses/ZZ950").status_code == 404
    assert adm.post("/api/admin/courses", json={"course_code": "bad", "title": "x", "credits": 3}).status_code == 422


def test_mess_out_declaration_and_no_rebate_endpoints_over_http(app):
    import datetime
    from app.services import clock
    stu, _ = login(app, "u2024405@giki.edu.pk")
    s = (clock.today() + datetime.timedelta(days=3)).isoformat()
    e = (clock.today() + datetime.timedelta(days=5)).isoformat()
    sub = stu.post("/api/mess/mess-out", json={"start_date": s, "end_date": e, "reason": "home"}).json()
    assert sub["status"] == "recorded" and "rebate" not in str(sub).lower()
    assert stu.post("/api/mess/mess-out", json={"start_date": s, "end_date": e}).status_code == 409           # overlap
    for path in ("/api/mess/me/rebates", "/api/mess/admin/summary/rebates"):
        assert stu.get(path).status_code == 404
    adm, _ = login(app, "mess@giki.edu.pk")
    assert adm.post(f"/api/mess/admin/mess-out/{sub['request_id']}/decision", json={"approve": True}).status_code in (404, 405)


def test_billing_flow_over_http(app):
    import datetime
    from app import db
    from app.services import clock
    stu, _ = login(app, "u2024405@giki.edu.pk")
    adm, _ = login(app, "mess@giki.edu.pk")
    for m, r in (("breakfast", 40), ("lunch", 100), ("dinner", 90)):
        assert adm.put("/api/mess/admin/rates", json={"meal_type": m, "rate_pkr": r, "effective_from": "2026-01-01"}).status_code == 200
    assert stu.put("/api/mess/admin/rates", json={"meal_type": "lunch", "rate_pkr": 1}).status_code == 403
    first = clock.today().replace(day=1)
    with db.tx() as cur:
        cur.execute("INSERT INTO mess_ledger (user_id, meal_date, meal_type, status, source) VALUES ('2024405', %s, 'lunch', 'consumed', 'turnstile'), ('2024405', %s, 'dinner', 'consumed', 'turnstile')", (first, first))
    bill = stu.get("/api/mess/me/bill").json()
    assert bill["kind"] == "estimate" and bill["amount_pkr"] == 190 and bill["days_consumed"] == 1
    assert stu.get("/api/mess/me/bill?student_id=2024668").status_code == 403
    assert stu.get("/api/mess/me/invoices").json() == []
    assert stu.post("/api/mess/admin/invoices/generate", json={"month": first.strftime("%Y-%m")}).status_code == 403
    assert adm.post("/api/mess/admin/invoices/generate", json={"month": first.strftime("%Y-%m")}).status_code == 409   # month still open
    assert stu.get("/api/mess/me/deposit").json()["status"] == "not_configured"
    assert stu.get("/api/mess/admin/deposits").status_code == 403
    assert stu.post("/api/mess/me/clearance").json()["status"] == "requested"
    assert stu.get("/api/mess/admin/clearances").status_code == 403
    assert adm.get("/api/mess/admin/clearances").status_code == 200


def test_qr_endpoint_and_turnstile_authorisation(app, monkeypatch):
    stu, _ = login(app, "u2024668@giki.edu.pk")
    qr = stu.get("/api/mess/qr?meal=lunch").json()
    assert "<svg" in qr["svg"]
    anon = client(app)
    assert anon.post("/api/mess/verify-entry", json={"payload": qr["payload"], "meal": "lunch"}).status_code == 403
    assert stu.post("/api/mess/verify-entry", json={"payload": qr["payload"], "meal": "lunch"}).status_code == 403
    from app import config
    monkeypatch.setattr(config, "GATE_API_KEY", "test-gate-key")
    assert anon.post("/api/mess/verify-entry", json={"payload": qr["payload"], "meal": "lunch"}, headers={"X-Gate-Key": "wrong"}).status_code == 403
    ok = anon.post("/api/mess/verify-entry", json={"payload": qr["payload"], "meal": "lunch"}, headers={"X-Gate-Key": "test-gate-key"})
    assert ok.status_code == 200 and ok.json()["status"] == "access_granted"


def test_requisition_and_sports_over_http(app):
    import datetime
    from app.services import clock
    s3, _ = login(app, "u2024662@giki.edu.pk")
    fac, _ = login(app, "farah.saeed@giki.edu.pk")
    dsa, _ = login(app, "dsa@giki.edu.pk")
    day = (clock.today() + datetime.timedelta(days=10)).isoformat()
    r = s3.post("/api/req/requisitions", json={"society_name": "ACM GIKI Chapter", "venue_id": "SEM_HALL_1", "event_title": "Demo", "booking_date": day,
                                                "start_time": "14:00", "duration_hours": 2, "expected_attendees": 60, "justification": "j"})
    assert r.status_code == 200, r.text
    rid = r.json()["requisition_id"]
    assert fac.get("/api/req/requisitions?scope=assigned").json()[0]["requisition_id"] == rid
    assert s3.post(f"/api/req/requisitions/{rid}/review", json={"action": "approve"}).status_code == 403
    assert fac.post(f"/api/req/requisitions/{rid}/review", json={"action": "approve"}).json()["status"] == "pending_admin_approval"
    assert dsa.post(f"/api/req/requisitions/{rid}/review", json={"action": "approve"}).json()["status"] == "approved"
    assert s3.get(f"/api/req/requisitions/{rid}").json()["stages"][1]["state"] == "approved"
    tomorrow = (clock.today() + datetime.timedelta(days=1)).isoformat()
    b = s3.post("/api/req/sports/bookings", json={"facility_id": "SPORTS_C1", "date": tomorrow, "start_time": "17:00", "duration_hours": 1})
    assert b.status_code == 200, b.text
    assert s3.get("/api/req/sports/quota").json()["daily_limit_hours"] == 2
    assert s3.post(f"/api/req/sports/bookings/{b.json()['booking_code']}/cancel").status_code == 200


def test_equipment_over_http(app):
    s1, _ = login(app, "u2024405@giki.edu.pk")
    assert s1.post("/api/req/equipment/BAR-FPGA-302/checkout", json={"purpose": "lab project", "days": 3}).status_code == 200
    assert [e["barcode"] for e in s1.get("/api/req/equipment/mine").json()] == ["BAR-FPGA-302"]
    s2, _ = login(app, "u2024668@giki.edu.pk")
    assert s2.post("/api/req/equipment/BAR-FPGA-302/return", json={}).status_code == 403
    assert s1.post("/api/req/equipment/BAR-FPGA-302/return", json={"condition": "good"}).status_code == 200


def test_csrf_origin_check_and_security_headers(app):
    c, _ = login(app, "u2024405@giki.edu.pk")
    r = c.post("/api/courses/CS271/register", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    ok = c.get("/health")
    assert ok.headers["x-content-type-options"] == "nosniff"


def test_logout_invalidates_session(app):
    c, _ = login(app, "u2024668@giki.edu.pk")
    assert c.get("/api/auth/me").status_code == 200
    token = c.cookies.get("gcortex_session")
    c.post("/api/auth/logout")
    c2 = client(app)
    c2.cookies.set("gcortex_session", token)
    assert c2.get("/api/auth/me").status_code == 401


def test_activity_history_shows_own_failures_only(app):
    c, _ = login(app, "u2024668@giki.edu.pk")
    c.post("/api/courses/CS325/register")                     # rejected: prerequisites
    act = c.get("/api/portal/activity").json()
    assert any(a["action"] == "course.register" and a["outcome"] == "failed" for a in act)
    assert all(True for a in act)
    c1, _ = login(app, "u2024405@giki.edu.pk")
    assert not any(a["entity_id"] == "CS325" for a in c1.get("/api/portal/activity").json())
