"""
G-Cortex: Campus Operating System for GIKI
Main FastAPI application entry point.

Mounts the Central Orchestrator and three autonomous subagent REST API routers:
  - /api/orchestrator — Central Intelligent Orchestrator & Multi-Agent Interface
  - /api/courses      — Course Management Subagent
  - /api/mess         — Mess Management Subagent
  - /api/req          — Requisition Management Subagent
  - /chat             — Web Chat Interface
"""
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.database import engine, Base
from app.api.course_router import router as course_router
from app.api.mess_router import router as mess_router
from app.api.req_router import router as req_router
from app.api.orchestrator_router import router as orchestrator_router

_TEMPLATES_DIR = Path(__file__).resolve().parent / "app" / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure all database tables exist on startup."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="G-Cortex Campus Operating System",
    description=(
        "Autonomous campus operating system for GIKI with a Central Intelligent Orchestrator "
        "and three specialized subagents: Course Management, Mess Management, and Requisition Management."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── Mount Central Orchestrator & Subagent Routers ───────────────
app.include_router(
    orchestrator_router, prefix="/api/orchestrator", tags=["Central Orchestrator"]
)
app.include_router(course_router, prefix="/api/courses", tags=["Course Management"])
app.include_router(mess_router, prefix="/api/mess", tags=["Mess Management"])
app.include_router(req_router, prefix="/api/req", tags=["Requisition Management"])


# ── Functional Chat Interface ──────────────────────────────────
@app.get("/chat", response_class=HTMLResponse, tags=["Web Interface"])
def chat_ui():
    """Serves the interactive G-Cortex Central Orchestrator web chat interface."""
    chat_file = _TEMPLATES_DIR / "chat.html"
    if not chat_file.exists():
        return HTMLResponse(
            "<h3>Chat template not found. Please verify app/templates/chat.html.</h3>",
            status_code=404,
        )
    return HTMLResponse(content=chat_file.read_text(encoding="utf-8"))


# ── Platform Health Endpoint ───────────────────────────────────
@app.get("/health", tags=["Platform"])
def health_check():
    """G-Cortex platform liveness check."""
    return {
        "status": "healthy",
        "service": "g-cortex",
        "version": "1.0.0",
        "orchestrator": "online",
        "active_subagents": [
            "course_management_agent",
            "mess_management_agent",
            "requisition_management_agent",
        ],
    }


@app.get("/", tags=["Platform"])
def root():
    """G-Cortex API root — platform overview."""
    return {
        "platform": "G-Cortex Campus Operating System",
        "institution": "Ghulam Ishaq Khan Institute (GIKI)",
        "orchestrator": "/api/orchestrator",
        "chat_interface": "/chat",
        "subagents": {
            "course_management": "/api/courses",
            "mess_management": "/api/mess",
            "requisition_management": "/api/req",
        },
        "docs": "/docs",
        "health": "/health",
    }
