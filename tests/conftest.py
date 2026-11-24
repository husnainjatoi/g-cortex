"""Test harness: runs the REAL application against a dedicated PostgreSQL database (gcortex_test).

The database is dropped, re-created and migrated once per test session, then seeded with the reference data
and test accounts. There are no mocks of the service layer: every test exercises real SQL.
"""
import os
import re

import psycopg2
import pytest

BASE = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost/gcortex_db").replace("postgresql+psycopg2://", "postgresql://")
TEST_URL = os.environ.get("TEST_DATABASE_URL") or re.sub(r"/[^/]+$", "/gcortex_test", BASE)
os.environ["DATABASE_URL"] = TEST_URL
os.environ["BACKGROUND_JOBS"] = "false"

PASSWORD = "Test-pass-123"


def _recreate_database():
    admin_url = re.sub(r"/[^/]+$", "/postgres", TEST_URL)
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='gcortex_test' AND pid <> pg_backend_pid()")
    cur.execute("DROP DATABASE IF EXISTS gcortex_test")
    cur.execute("CREATE DATABASE gcortex_test")
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def database():
    _recreate_database()
    from app import migrate, db
    db.reset_pool()
    migrate.run(verbose=False)
    import seed_db  # noqa: F401  (reference catalogue: users, courses, menu, venues, societies, equipment)
    seed_db.DB_URL = TEST_URL
    seed_db.seed()
    migrate.run(verbose=False)
    from app import seed_portal
    seed_portal.seed()
    from app.services import auth
    for uid in ("2024405", "2024668", "2024662", "FAC001", "FAC002", "ADMIN01"):
        auth.set_password(None, uid, PASSWORD, must_change=False)
    # separate responsibility-scoped administrators
    auth.create_user(None, user_id="ADMACAD", full_name="Registrar", email="registrar@giki.edu.pk", role="admin", scopes=["academic"], password=PASSWORD, must_change=False)
    auth.create_user(None, user_id="ADMMESS", full_name="Mess Office", email="mess@giki.edu.pk", role="admin", scopes=["mess"], password=PASSWORD, must_change=False)
    auth.create_user(None, user_id="ADMREQ", full_name="Student Affairs", email="dsa2@giki.edu.pk", role="admin", scopes=["requisition"], password=PASSWORD, must_change=False)
    yield
    db.reset_pool()


@pytest.fixture(autouse=True)
def fresh_clock():
    from app.services import clock
    clock.freeze(None)
    yield
    clock.freeze(None)


@pytest.fixture
def actors():
    from app.services.actor import Actor
    A = frozenset
    return {
        "s1": Actor("2024405", "student", A(), "web", "Husnain"),
        "s2": Actor("2024668", "student", A(), "web", "Zahra"),
        "s3": Actor("2024662", "student", A(), "web", "Warda"),
        "fac": Actor("FAC001", "faculty", A(), "web", "Farah"),
        "super": Actor("ADMIN01", "admin", A({"super"}), "web", "DSA"),
        "acad": Actor("ADMACAD", "admin", A({"academic"}), "web", "Registrar"),
        "mess": Actor("ADMMESS", "admin", A({"mess"}), "web", "Mess"),
        "req": Actor("ADMREQ", "admin", A({"requisition"}), "web", "DSA2"),
    }


NOW = None


@pytest.fixture(autouse=True)
def reset_state():
    """Each test starts from the seeded reference data with no transactional leftovers."""
    from app import db
    with db.tx() as cur:
        cur.execute("""TRUNCATE enrollments, mess_out_requests, mess_qr_redemptions, notifications, domain_events, bookings,
                       society_requisitions, equipment_custody_log, assistant_pending_actions, mess_ledger, event_handler_runs,
                       mess_payments, mess_challans, mess_invoice_adjustments, mess_invoices, mess_deposit_transactions, mess_deposit_accounts, mess_clearances, mess_rates RESTART IDENTITY CASCADE""")
        cur.execute("DELETE FROM mess_rebates")
        cur.execute("DELETE FROM gate_passes")
        cur.execute("UPDATE no_show_records SET strikes=0, suspended=false, suspended_until=NULL, strike_history='[]'")
        cur.execute("UPDATE lab_equipment SET status='available', custodian_id=NULL, checkout_date=NULL, due_date=NULL, purpose=NULL, condition='good'")
        cur.execute("DELETE FROM course_prerequisites WHERE course_code LIKE 'ZZ%%' OR prereq_code LIKE 'ZZ%%'")
        cur.execute("DELETE FROM courses WHERE course_code LIKE 'ZZ%%'")
        cur.execute("UPDATE auth_sessions SET revoked=true")
        cur.execute("UPDATE users SET failed_logins=0, locked_until=NULL, is_active=true")
        cur.execute("UPDATE system_settings SET value='2027-01-15' WHERE key='drop_deadline'")
        cur.execute("UPDATE courses SET is_active=true, capacity=60 WHERE course_code NOT IN ('CS330','CS412')")
        cur.execute("UPDATE student_profiles SET allergies='none'")
        cur.execute("UPDATE system_settings SET value='per_meal' WHERE key='mess_billing_mode'")
        cur.execute("UPDATE system_settings SET value='0' WHERE key='mess_deposit_amount'")
        cur.execute("DELETE FROM mess_totp_secrets")
    yield


@pytest.fixture
def at():
    """Freeze the application clock: at(2026, 11, 25, 10, 0)."""
    import datetime
    from app.services import clock

    def _set(y=2026, m=11, d=25, hh=10, mm=0):
        clock.freeze(datetime.datetime(y, m, d, hh, mm))
    _set()
    return _set
