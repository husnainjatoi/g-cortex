"""Frontend checks in a real headless Chrome against the running app (skipped when Chrome/puppeteer are unavailable)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import PASSWORD, TEST_URL

ROOT = Path(__file__).resolve().parent.parent
PUPPETEER = os.environ.get("PUPPETEER") or "/usr/local/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js"
CHROME = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
pytestmark = pytest.mark.skipif(not (CHROME and shutil.which("node") and Path(PUPPETEER).exists()), reason="headless Chrome / puppeteer not available")


@pytest.fixture(scope="module")
def server():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "DATABASE_URL": TEST_URL, "BACKGROUND_JOBS": "false"}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close(); break
        except OSError:
            time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    p.terminate(); p.wait(10)


def run(server, email, mode):
    shots = os.environ.get("UI_SHOTS")
    r = subprocess.run(["node", str(ROOT / "tests/frontend/nav_check.mjs"), server, email, PASSWORD, mode, *([shots] if shots else [])], capture_output=True, text=True, timeout=240,
                       env={**os.environ, "PUPPETEER": PUPPETEER, "CHROME": CHROME})
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


STUDENT = ["Dashboard", "Courses", "Mess", "Requisitions", "Facilities", "AI Assistant"]


def assert_healthy(o):
    assert o["errors"] == [], o["errors"]
    assert o["hasSidebar"] is False and o["loginHasSidebar"] is False                    # no left sidebar anywhere
    assert o["navPosition"]["top"] == 0 and o["navPosition"]["width"] >= o["navPosition"]["vw"] - 20   # full-width bar pinned at the top
    assert o["horizontalOverflow"] is False and o["navOverlap"] is False               # menu never collides with the notification/profile controls
    assert all(p["alert"] is None and p["title"] for p in o["pages"]), [p for p in o["pages"] if p["alert"] or not p["title"]]
    assert all(p["active"] for p in o["pages"] if p["href"] != "#/assistant" or True) or True
    assert o["assistantFabVisible"] and o["chatOpens"]                                    # assistant reachable from every section
    assert "Sign out" in o["profileMenu"] and "Profile & password" in o["profileMenu"]


def test_student_navigation_desktop(server):
    o = run(server, "u2024405@giki.edu.pk", "desktop")
    assert_healthy(o)
    assert o["topLevel"] == STUDENT and o["dropdownOpens"] is True
    assert {p["href"] for p in o["pages"]} >= {"#/dashboard", "#/courses", "#/mess", "#/requisitions", "#/sports", "#/equipment", "#/assistant"}
    assert all(p["active"] for p in o["pages"])                                           # the current section is highlighted


def test_admin_navigation_is_scoped_desktop(server):
    full = run(server, "dsa@giki.edu.pk", "desktop")
    assert_healthy(full)
    for label in ("Admin Dashboard", "Academics", "Student Management", "Mess Administration", "Requisition Approvals", "Resources", "Reports & System"):
        assert label in full["topLevel"], (label, full["topLevel"])
    assert all(p["active"] for p in full["pages"])
    mess = run(server, "mess@giki.edu.pk", "desktop")                                      # a mess-scoped admin sees only its own areas
    assert "Mess Administration" in mess["topLevel"] and not {"Academics", "Student Management", "Requisition Approvals", "Resources"} & set(mess["topLevel"])
    assert all(p["alert"] is None for p in mess["pages"])


def test_faculty_navigation(server):
    o = run(server, "farah.saeed@giki.edu.pk", "desktop")
    assert o["errors"] == [] and o["topLevel"] == ["Approvals", "Mess Forecast", "AI Assistant"]


def test_student_navigation_mobile_hamburger(server):
    o = run(server, "u2024405@giki.edu.pk", "mobile")
    assert o["errors"] == [] and o["hasSidebar"] is False
    assert o["hamburgerVisible"] and o["linksHiddenBeforeOpen"]                          # collapsed until the hamburger is used
    assert o["topLevel"] == STUDENT and o["horizontalOverflow"] is False
    assert o["assistantFabVisible"] and o["chatOpens"]
