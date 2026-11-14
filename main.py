"""
G-Cortex: Campus Operating System for GIKI
Main FastAPI application entry point.
"""
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.database import engine, Base
from app.api.course_router import router as course_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="G-Cortex",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Mount course subagent router
app.include_router(course_router, prefix="/api/courses")


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "g-cortex",
        "active_subagents": ["course_management_agent"],
    }
