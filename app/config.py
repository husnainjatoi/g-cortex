"""Central configuration. All secrets come from the environment (never from source code)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Root .env wins; the legacy per-agent .env files are read as a fallback so existing setups keep working.
load_dotenv(ROOT / ".env")
for _legacy in ("course_management_agent", "mess_management_agent", "requisition_management_agent"):
    load_dotenv(ROOT / "app" / "agents" / _legacy / ".env")


def database_url() -> str:
    """Plain libpq URL (psycopg2 form)."""
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost/gcortex_db")
    return url.replace("postgresql+psycopg2://", "postgresql://")


def sqlalchemy_url() -> str:
    url = database_url()
    return url.replace("postgresql://", "postgresql+psycopg2://", 1)


SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_NAME = "gcortex_session"
# Shared secret for the external gate controller / turnstile REST integration (optional).
GATE_API_KEY = os.getenv("GATE_API_KEY", "")
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
BACKGROUND_JOBS = os.getenv("BACKGROUND_JOBS", "true").lower() == "true"
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "gemini-3.5-flash-lite")

# Initial (temporary) administrator credential. Stored only as a hash after provisioning, never logged or returned by the API.
# Override in the environment for any real deployment; users are forced to change it at first sign-in.
ADMIN_INITIAL_PASSWORD = os.getenv("ADMIN_INITIAL_PASSWORD", "admin123")
LOGIN_IP_LIMIT = int(os.getenv("LOGIN_IP_LIMIT", "30"))          # attempts per IP per window
LOGIN_IP_WINDOW_SECONDS = int(os.getenv("LOGIN_IP_WINDOW_SECONDS", "300"))
