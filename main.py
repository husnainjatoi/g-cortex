"""
G-Cortex: Campus Operating System for GIKI - FastAPI application entry point.

One backend, two interfaces: the portal (static SPA in app/static) and the AI assistant both call the SAME service layer in
app/services, authenticated by the same server-side session and authorised by the same RBAC rules.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config, db
from app.api import admin_router, auth_router, course_router, mess_router, orchestrator_router, portal_router, req_router
from app.api.deps import csrf_guard, service_error_handler
from app.services import billing, events, sports
from app.services.errors import ServiceError

logger = logging.getLogger("g_cortex.main")
_STATIC = Path(__file__).resolve().parent / "app" / "static"


async def _background_jobs():
    """Periodic maintenance: zero-trust no-show auto-release and retry of failed event handlers."""
    while True:
        try:
            await asyncio.to_thread(sports.auto_release, None)
            await asyncio.to_thread(events.retry_failed)
            try:  # idempotent: no-op once last month is invoiced or when rates are not configured yet
                await asyncio.to_thread(billing.generate_monthly_invoices, None)
            except ServiceError:
                pass
        except Exception:
            logger.exception("background job failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_background_jobs()) if config.BACKGROUND_JOBS else None
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="G-Cortex Campus Operating System",
    description="Campus management portal and multi-agent AI assistant for GIKI, sharing one authenticated backend.",
    version="2.0.0", docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json", lifespan=lifespan,
)
app.middleware("http")(csrf_guard)
app.add_exception_handler(ServiceError, service_error_handler)

app.include_router(auth_router.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(portal_router.router, prefix="/api/portal", tags=["Portal"])
app.include_router(course_router.router, prefix="/api/courses", tags=["Course Management"])
app.include_router(mess_router.router, prefix="/api/mess", tags=["Mess Management"])
app.include_router(req_router.router, prefix="/api/req", tags=["Requisition Management"])
app.include_router(admin_router.router, prefix="/api/admin", tags=["Administration"])
app.include_router(orchestrator_router.router, prefix="/api/orchestrator", tags=["AI Assistant"])


@app.get("/health", tags=["Platform"])
def health_check():
    return {"status": "healthy" if db.ping() else "degraded", "service": "g-cortex", "version": "2.0.0", "database": db.ping()}


@app.get("/api", tags=["Platform"])
def api_root():
    return {"platform": "G-Cortex Campus Operating System", "docs": "/docs", "portal": "/", "health": "/health"}


if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str = ""):
        if path.startswith(("api/", "static/")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(_STATIC / "index.html")
