import pytest

from app import db
from app.services import audit, auth, courses, events, notifications, reports, settings
from app.services.errors import Forbidden, Unauthenticated, ValidationFailed
from tests.conftest import PASSWORD


def test_login_returns_role_from_database_not_client(actors):
    token, actor, must = auth.login("U2024405@giki.edu.pk", PASSWORD)     # email is case-insensitive
    assert (actor.user_id, actor.role, actor.scopes) == ("2024405", "student", frozenset())
    token2, adm, _ = auth.login("registrar@giki.edu.pk", PASSWORD)
    assert adm.role == "admin" and adm.has_scope("academic") and not adm.has_scope("mess")
    assert auth.authenticate(token).user_id == "2024405"


def test_bad_credentials_unknown_user_and_lockout(actors):
    with pytest.raises(Unauthenticated) as a:
        auth.login("u2024405@giki.edu.pk", "wrong-password1")
    with pytest.raises(Unauthenticated) as b:
        auth.login("nobody@giki.edu.pk", "whatever123")
    assert str(a.value) == str(b.value)                                   # no user enumeration
    for _ in range(5):
        with pytest.raises(Unauthenticated):
            auth.login("u2024668@giki.edu.pk", "bad-password-1")
    with pytest.raises(Unauthenticated) as locked:
        auth.login("u2024668@giki.edu.pk", PASSWORD)                      # correct password still refused while locked
    assert "locked" in str(locked.value)


def test_user_without_password_cannot_login(actors):
    auth.create_user(actors["super"], user_id="NEWST1", full_name="New", email="new1@giki.edu.pk", role="student")
    with pytest.raises(Unauthenticated):
        auth.login("new1@giki.edu.pk", "anything123")
    with db.tx() as cur:
        cur.execute("DELETE FROM mess_deposit_accounts WHERE user_id='NEWST1'"); cur.execute("DELETE FROM student_profiles WHERE user_id='NEWST1'"); cur.execute("DELETE FROM users WHERE user_id='NEWST1'")


def test_logout_and_deactivation_revoke_sessions(actors):
    token, actor, _ = auth.login("u2024662@giki.edu.pk", PASSWORD)
    auth.logout(token, actor)
    with pytest.raises(Unauthenticated):
        auth.authenticate(token)
    t2, _, _ = auth.login("u2024662@giki.edu.pk", PASSWORD)
    auth.update_user(actors["super"], "2024662", is_active=False)
    with pytest.raises(Unauthenticated):
        auth.authenticate(t2)
    with pytest.raises(Unauthenticated):
        auth.login("u2024662@giki.edu.pk", PASSWORD)


def test_password_policy_and_hash_storage(actors):
    with pytest.raises(ValidationFailed):
        auth.validate_password_strength("short1")
    with pytest.raises(ValidationFailed):
        auth.validate_password_strength("alllettersnodigits")
    h = db.query_one("SELECT password_hash FROM users WHERE user_id='2024405'")["password_hash"]
    assert h.startswith("scrypt$") and PASSWORD not in h


def test_change_password_revokes_other_sessions(actors):
    token, actor, _ = auth.login("u2024405@giki.edu.pk", PASSWORD)
    with pytest.raises(Forbidden):
        auth.change_password(actor, "wrong-current1", "Brand-new-pass-9", "Brand-new-pass-9")
    new_token = auth.change_password(actor, PASSWORD, "Brand-new-pass-9", "Brand-new-pass-9")
    with pytest.raises(Unauthenticated):
        auth.authenticate(token)
    assert auth.authenticate(new_token).user_id == "2024405"                          # fresh session issued (rotation)
    auth.login("u2024405@giki.edu.pk", "Brand-new-pass-9")
    auth.set_password(None, "2024405", PASSWORD, must_change=False)


def test_only_super_admin_manages_users_and_cannot_demote_self(actors):
    with pytest.raises(Forbidden):
        auth.list_users(actors["acad"])
    with pytest.raises(Forbidden):
        auth.create_user(actors["s1"], user_id="HACK1", full_name="x", email="h@x.pk", role="admin", scopes=["super"])
    with pytest.raises(Forbidden):
        auth.update_user(actors["super"], "ADMIN01", role="student")
    with pytest.raises(ValidationFailed):
        auth.create_user(actors["super"], user_id="ADM9", full_name="A", email="a9@giki.edu.pk", role="admin", scopes=[])


def test_audit_log_is_append_only(actors):
    courses.register(actors["s1"], "CS271")
    row = db.query_one("SELECT audit_id FROM audit_log ORDER BY audit_id DESC LIMIT 1")
    with pytest.raises(Exception) as e1:
        with db.tx() as cur:
            cur.execute("UPDATE audit_log SET outcome='success' WHERE audit_id=%s", (row["audit_id"],))
    assert "append-only" in str(e1.value)
    with pytest.raises(Exception):
        with db.tx() as cur:
            cur.execute("DELETE FROM audit_log")
    with pytest.raises(Exception):
        with db.tx() as cur:
            cur.execute("TRUNCATE audit_log")


def test_audit_entries_visible_only_to_owner_or_super(actors):
    courses.register(actors["s1"], "CS271")
    courses.register(actors["s3"], "CS330") if False else None
    mine = audit.list_entries(actors["s1"])
    assert mine and all(r["actor_id"] == "2024405" for r in mine)
    assert not any(r["actor_id"] == "2024405" for r in audit.list_entries(actors["s2"]))
    assert any(r["actor_id"] == "2024405" for r in audit.list_entries(actors["super"]))
    assert audit.list_entries(actors["acad"]) == [r for r in audit.list_entries(actors["acad"]) if r["actor_id"] == "ADMACAD"]


def test_notifications_are_private_and_markable(actors):
    courses.register(actors["s1"], "CS271")
    n = notifications.list_for(actors["s1"])
    assert n and notifications.unread_count(actors["s1"]) == len(n)
    assert notifications.list_for(actors["s2"]) == []
    notifications.mark_read(actors["s1"])
    assert notifications.unread_count(actors["s1"]) == 0


def test_failed_handler_is_recorded_and_retry_recovers(actors):
    calls = {"n": 0}

    @events.subscribe("test.flaky")
    def flaky(cur, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
    with db.tx() as cur:
        eid = events.emit(cur, "test.flaky", {"x": 1})
    events.dispatch(eid)
    assert db.query_one("SELECT status FROM event_handler_runs WHERE event_id=%s", (eid,))["status"] == "failed"
    assert events.retry_failed()["recovered"] >= 1
    assert db.query_one("SELECT status FROM event_handler_runs WHERE event_id=%s", (eid,))["status"] == "done"
    events.dispatch(eid)
    assert calls["n"] == 2                                                 # not run a third time
    events._handlers["test.flaky"].remove(flaky)


def test_settings_scoped_updates(actors):
    with pytest.raises(Forbidden):
        settings.update(actors["mess"], "max_credits_per_term", "20")
    with pytest.raises(ValidationFailed):
        settings.update(actors["mess"], "mess_deposit_amount", "abc")
    with pytest.raises(ValidationFailed):
        settings.update(actors["mess"], "mess_billing_mode", "prepaid")
    settings.update(actors["mess"], "mess_invoice_due_days", "14")
    assert settings.get_quick("mess_invoice_due_days") == 14
    settings.update(actors["mess"], "mess_invoice_due_days", "10")


def test_reports_are_scope_filtered(actors, at):
    courses.register(actors["s1"], "CS271")
    full = reports.overview(actors["super"])
    assert {"courses", "mess", "requisitions", "activity"} <= set(full["sections"])
    assert next(c for c in full["courses"]["by_course"] if c["course_code"] == "CS271")["registered"] == 1
    acad = reports.overview(actors["acad"])
    assert acad["sections"] == ["courses"] and "mess" not in acad
    with pytest.raises(Forbidden):
        reports.overview(actors["s1"])
    d = reports.student_dashboard(actors["s1"])
    assert d["courses"]["count"] == 1
