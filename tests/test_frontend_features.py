"""Round-3 frontend features in headless Chrome (desktop + mobile) against the real API and database (the test DB)."""
import json
import os
import socket
import subprocess
import sys
import time

import pytest

from app import db
from app.services import auth, societies
from tests.conftest import PASSWORD, TEST_URL
from tests.test_frontend import CHROME, PUPPETEER, ROOT, pytestmark  # noqa: F401  (skip when Chrome / puppeteer are missing)
from tests.test_reservations import PAGE

NEW_PASSWORD = "Fresh-pass-456"


@pytest.fixture(scope="module")
def server():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT,
                         env={**os.environ, "DATABASE_URL": TEST_URL, "BACKGROUND_JOBS": "false", "LOGIN_IP_LIMIT": "1000"})
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close(); break
        except OSError:
            time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    p.terminate(); p.wait(10)


def run(server, email, mode, scenario, *extra):
    r = subprocess.run(["node", str(ROOT / "tests/frontend/features_check.mjs"), server, email, PASSWORD, mode, scenario, *extra], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PUPPETEER": PUPPETEER, "CHROME": CHROME})
    assert r.returncode == 0, r.stderr[-1500:]
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert "dbg" not in o, o
    assert o["errors"] == [], o["errors"]
    bad = {k: v for k, v in o["checks"].items() if v is not True}
    assert not bad, bad
    assert o["overflow"] is False
    return o


@pytest.fixture
def societies_loaded(actors):
    societies.import_official(actors["req"], html_override=PAGE)
    yield
    with db.tx() as cur:
        cur.execute("DELETE FROM society_requisitions"); cur.execute("DELETE FROM society_memberships WHERE society_id IN (SELECT society_id FROM societies WHERE origin IN ('official','manual'))")
        cur.execute("DELETE FROM societies WHERE origin IN ('official','manual')"); cur.execute("UPDATE societies SET status='active' WHERE origin='legacy'")


@pytest.mark.parametrize("mode", ["desktop", "mobile"])
def test_student_flows(server, mode, societies_loaded):
    run(server, "u2024405@giki.edu.pk", mode, "student")


@pytest.mark.parametrize("mode", ["desktop", "mobile"])
def test_admin_flows(server, mode):
    run(server, "admin@giki.edu.pk" if db.query_one("SELECT 1 FROM users WHERE email='admin@giki.edu.pk'") else db.query_one("SELECT email FROM users WHERE user_id='ADMIN01'")["email"], mode, "admin")


@pytest.mark.parametrize("mode", ["desktop", "mobile"])
def test_first_login_password_change(server, mode):
    uid = "2024668"
    email = db.query_one("SELECT email FROM users WHERE user_id=%s", (uid,))["email"]
    try:
        with db.tx() as cur:
            cur.execute("UPDATE users SET must_change_password=true WHERE user_id=%s", (uid,))
        auth.set_password(None, uid, PASSWORD, must_change=True)
        run(server, email, mode, "forced", NEW_PASSWORD)
        assert db.query_one("SELECT must_change_password FROM users WHERE user_id=%s", (uid,))["must_change_password"] is False
    finally:
        auth.set_password(None, uid, PASSWORD, must_change=False)
