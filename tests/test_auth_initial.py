"""Initial-credential policy: DOB / admin bootstrap passwords, mandatory first-login change (enforced server-side), rate limiting, friendly errors."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.services import auth
from app.services.errors import ValidationFailed
from tests.conftest import PASSWORD


@pytest.fixture(scope="module")
def app():
    import main
    return main.app


def client(app, raise_exc=True):
    return TestClient(app, base_url="http://testserver", raise_server_exceptions=raise_exc)


@pytest.fixture
def new_accounts(actors):
    """A student (DOB 2000-01-23), a faculty member (DOB 1980-07-04) and an administrator, all on initial credentials."""
    auth.create_user(actors["super"], user_id="INI001", full_name="Initial Student", email="ini1@giki.edu.pk", role="student", date_of_birth="2000-01-23", use_initial_password=True)
    auth.create_user(actors["super"], user_id="INIF01", full_name="Initial Faculty", email="inif@giki.edu.pk", role="faculty", date_of_birth="1980-07-04", use_initial_password=True)
    auth.create_user(actors["super"], user_id="INIA01", full_name="Initial Admin", email="inia@giki.edu.pk", role="admin", scopes=["super"], use_initial_password=True)
    yield
    with db.tx() as cur:
        for u in ("INI001", "INIF01", "INIA01"):
            cur.execute("DELETE FROM auth_sessions WHERE user_id=%s", (u,)); cur.execute("DELETE FROM notifications WHERE user_id=%s", (u,))
            cur.execute("DELETE FROM mess_deposit_accounts WHERE user_id=%s", (u,)); cur.execute("DELETE FROM student_profiles WHERE user_id=%s", (u,))
            cur.execute("DELETE FROM admin_scopes WHERE user_id=%s", (u,)); cur.execute("DELETE FROM users WHERE user_id=%s", (u,))


def test_initial_password_rules():
    assert auth.initial_password_for("student", "2000-01-23") == "20000123"
    assert auth.initial_password_for("faculty", "1975-12-05") == "19751205"
    assert auth.initial_password_for("admin", None) == "admin123"
    assert auth.initial_password_for("student", None) is None and auth.initial_password_for("student", "not-a-date") is None   # no universal fallback


@pytest.mark.parametrize("email,initial", [("ini1@giki.edu.pk", "20000123"), ("inif@giki.edu.pk", "19800704"), ("inia@giki.edu.pk", "admin123")])
def test_first_login_with_initial_credentials_forces_change(app, new_accounts, email, initial):
    c = client(app)
    me = c.post("/api/auth/login", json={"email": email, "password": initial}).json()
    assert me["must_change_password"] is True
    # EVERY API except logout / me / change-password is refused server-side while the initial password is in use
    for method, path, body in [("get", "/api/portal/dashboard", None), ("get", "/api/courses", None), ("get", "/api/mess/me/bill", None), ("get", "/api/req/venues", None),
                               ("get", "/api/admin/users", None), ("get", "/api/portal/notifications", None), ("post", "/api/orchestrator/chat", {"message": "hello"}),
                               ("post", "/api/courses/CS271/register", {}), ("get", "/api/req/equipment", None), ("get", "/api/admin/reports/overview", None)]:
        r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["code"] == "password_change_required", (path, r.status_code, r.text)
    assert c.get("/api/auth/me").status_code == 200
    assert db.query_one("SELECT COUNT(*) AS n FROM enrollments")["n"] == 0                        # the direct API call changed nothing


def test_password_change_validation_and_success(app, new_accounts):
    c = client(app)
    c.post("/api/auth/login", json={"email": "ini1@giki.edu.pk", "password": "20000123"})
    def attempt(cur, new, conf=None):
        return c.post("/api/auth/change-password", json={"current_password": cur, "new_password": new, "confirm_password": conf if conf is not None else new})
    assert attempt("wrong-one-1", "Fresh-pass-77").status_code == 403
    r = attempt("20000123", "Fresh-pass-77", "Different-pass-1"); assert r.status_code == 422 and "do not match" in r.json()["detail"]
    r = attempt("20000123", "short1"); assert r.status_code == 422 and "at least 8" in r.json()["detail"]
    r = attempt("20000123", "onlyletters"); assert r.status_code == 422
    r = attempt("20000123", "20000123"); assert r.status_code == 422
    r = attempt("20000123", "Fresh-pass-77")
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert c.get("/api/portal/dashboard").status_code == 200                                      # normal access now allowed with the rotated session
    # the old default credential is rejected, the new one works and is no longer flagged
    assert client(app).post("/api/auth/login", json={"email": "ini1@giki.edu.pk", "password": "20000123"}).status_code == 401
    ok = client(app).post("/api/auth/login", json={"email": "ini1@giki.edu.pk", "password": "Fresh-pass-77"})
    assert ok.status_code == 200 and ok.json()["must_change_password"] is False
    row = db.query_one("SELECT password_hash, must_change_password FROM users WHERE user_id='INI001'")
    assert row["password_hash"].startswith("scrypt$") and "Fresh-pass-77" not in row["password_hash"] and row["must_change_password"] is False
    # initial default password is not reusable even when it is the *current* credential of an admin
    a = client(app); a.post("/api/auth/login", json={"email": "inia@giki.edu.pk", "password": "admin123"})
    r = a.post("/api/auth/change-password", json={"current_password": "admin123", "new_password": "admin123", "confirm_password": "admin123"}); assert r.status_code == 422
    assert a.post("/api/auth/change-password", json={"current_password": "admin123", "new_password": "Admin-new-pass-5", "confirm_password": "Admin-new-pass-5"}).status_code == 200
    assert a.get("/api/admin/users").status_code == 200


def test_old_sessions_are_revoked_on_password_change(app, new_accounts):
    first, second = client(app), client(app)
    first.post("/api/auth/login", json={"email": "inif@giki.edu.pk", "password": "19800704"}); second.post("/api/auth/login", json={"email": "inif@giki.edu.pk", "password": "19800704"})
    assert first.post("/api/auth/change-password", json={"current_password": "19800704", "new_password": "Faculty-new-9", "confirm_password": "Faculty-new-9"}).status_code == 200
    assert second.get("/api/auth/me").status_code == 401                                           # other session rotated out


def test_no_password_material_in_audit_or_responses(app, new_accounts):
    c = client(app)
    r = c.post("/api/auth/login", json={"email": "ini1@giki.edu.pk", "password": "20000123"})
    c.post("/api/auth/change-password", json={"current_password": "20000123", "new_password": "Fresh-pass-77", "confirm_password": "Fresh-pass-77"})
    blob = str(r.json()) + str(db.query("SELECT detail, entity_id FROM audit_log WHERE actor_id='INI001' OR entity_id='INI001'"))
    for secret in ("20000123", "Fresh-pass-77", "admin123"):
        assert secret not in blob


def test_missing_or_invalid_dob_never_gets_a_fallback_password(actors):
    auth.create_user(actors["super"], user_id="NODOB1", full_name="No Dob", email="nodob@giki.edu.pk", role="student", use_initial_password=True)
    try:
        assert db.query_one("SELECT password_hash FROM users WHERE user_id='NODOB1'")["password_hash"] is None
        with pytest.raises(ValidationFailed):
            auth.provision_initial_password(actors["super"], "NODOB1")
        with pytest.raises(ValidationFailed):
            auth.set_date_of_birth(actors["super"], "NODOB1", "2999-01-01")
        auth.set_date_of_birth(actors["super"], "NODOB1", "2001-02-03")
        auth.provision_initial_password(actors["super"], "NODOB1")
        assert auth.login("nodob@giki.edu.pk", "20010203")[2] is True                               # must change
        assert "NODOB1" not in auth.provision_all_initial(actors["super"], only_missing=True)["needs_date_of_birth"]
    finally:
        with db.tx() as cur:
            for t in ("auth_sessions", "mess_deposit_accounts", "student_profiles"):
                cur.execute(f"DELETE FROM {t} WHERE user_id='NODOB1'")
            cur.execute("DELETE FROM users WHERE user_id='NODOB1'")


def test_only_super_admin_can_reset_initial_credentials(actors, new_accounts):
    from app.services.errors import Forbidden
    with pytest.raises(Forbidden):
        auth.provision_initial_password(actors["mess"], "INI001")
    with pytest.raises(Forbidden):
        auth.set_date_of_birth(actors["s1"], "INI001", "2000-01-23")
    auth.provision_initial_password(actors["super"], "INI001")


def test_login_rate_limiting(app, monkeypatch):
    from app import config
    from app.api import auth_router
    monkeypatch.setattr(config, "LOGIN_IP_LIMIT", 4); auth_router._attempts.clear()
    c = client(app)
    codes = [c.post("/api/auth/login", json={"email": "ghost@giki.edu.pk", "password": "bad-pass-1"}).status_code for _ in range(6)]
    assert codes[:4] == [401] * 4 and codes[4:] == [429, 429]
    assert client(app).post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD}).status_code == 429     # blocked IPs stay blocked for the window
    assert "wait" in c.post("/api/auth/login", json={"email": "x@y.pk", "password": "p"}).json()["detail"].lower()
    auth_router._attempts.clear()


# ── friendly errors ─────────────────────────────────────────

def test_validation_errors_are_plain_language(app, actors):
    c = client(app); c.post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD})
    r = c.post("/api/req/venues/recommend", json={"description": "a", "attendees": 10})
    assert r.status_code == 422 and r.json()["detail"] == "Please enter a description containing at least 3 characters."
    assert r.json()["fields"] == {"description": "Please enter a description containing at least 3 characters."}
    r = c.post("/api/req/venues/recommend", json={})
    assert r.status_code == 422 and r.json()["detail"].startswith("Some of the information you entered is missing or invalid")
    assert "Please fill in the description." in r.json()["messages"] and "Please fill in the number of participants." in r.json()["messages"]
    r = c.post("/api/req/venues/recommend", json={"description": "valid text here", "attendees": "many"})
    assert r.json()["fields"]["attendees"] == "The number of participants must be a valid number."
    r = c.post("/api/auth/login", json={"email": "", "password": ""})
    assert "email address" in r.json()["detail"] or "email address" in " ".join(r.json()["messages"])
    for bad in (r.text,):
        for jargon in ("String should", "Field required", "Unprocessable", "loc", "type_error", "pydantic", "ctx"):
            assert jargon not in bad


def test_server_errors_hide_internals_but_are_logged(app, actors, monkeypatch, caplog):
    c = client(app, raise_exc=False); c.post("/api/auth/login", json={"email": "u2024405@giki.edu.pk", "password": PASSWORD})
    from app.services import courses
    monkeypatch.setattr(courses, "list_courses", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("psycopg2 OperationalError: password=hunter2 host=db")))
    with caplog.at_level("ERROR"):
        r = c.get("/api/courses")
    assert r.status_code == 500 and r.json()["detail"] == "Something went wrong while processing your request. Please try again later."
    assert "hunter2" not in r.text and "psycopg2" not in r.text and "Traceback" not in r.text
    assert any("hunter2" in rec.getMessage() or (rec.exc_info and "hunter2" in str(rec.exc_info[1])) for rec in caplog.records)      # detail preserved server-side
    assert c.get("/api/nope").json()["detail"] == "We couldn't find what you were looking for."
    assert c.delete("/api/auth/me").json()["detail"] == "That action isn't supported."
