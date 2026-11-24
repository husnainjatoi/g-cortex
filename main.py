"""
G-Cortex: Campus Operating System for GIKI
Main FastAPI application entry point.

Mounts three autonomous subagent REST API routers:
  - /api/courses  — Course Management Subagent
  - /api/mess     — Mess Management Subagent
  - /api/req      — Requisition Management Subagent
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import engine, Base
from app.api.course_router import router as course_router
from app.api.mess_router import router as mess_router
from app.api.req_router import router as req_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure all database tables exist on startup."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="G-Cortex Campus Operating System",
    description=(
        "Autonomous campus operating system for GIKI with three specialized subagents: "
        "Course Management, Mess Management, and Requisition Management."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── Mount Subagent Routers ─────────────────────────────────────
app.include_router(course_router, prefix="/api/courses", tags=["Course Management"])
app.include_router(mess_router, prefix="/api/mess", tags=["Mess Management"])
app.include_router(req_router, prefix="/api/req", tags=["Requisition Management"])


# ── Platform Health Endpoint ───────────────────────────────────
@app.get("/health", tags=["Platform"])
def health_check():
    """G-Cortex platform liveness check."""
    return {
        "status": "healthy",
        "service": "g-cortex",
        "version": "1.0.0",
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
        "subagents": {
            "course_management": "/api/courses",
            "mess_management": "/api/mess",
            "requisition_management": "/api/req",
        },
        "docs": "/docs",
        "health": "/health",
    }
