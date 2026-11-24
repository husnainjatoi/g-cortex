"""Shared FastAPI dependencies: session authentication and error mapping."""
import hmac

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse

from app import config
from app.services import auth
from app.services.actor import Actor
from app.services.errors import Forbidden, ServiceError, Unauthenticated


def current_actor(request: Request) -> Actor:
    """The ONLY way a request obtains an identity: a valid server-side session cookie."""
    return auth.authenticate(request.cookies.get(config.COOKIE_NAME))


def optional_actor(request: Request) -> Actor | None:
    try:
        return current_actor(request)
    except Unauthenticated:
        return None


def require_admin(actor: Actor = Depends(current_actor)) -> Actor:
    actor.require_role("admin")
    return actor


def gate_key_ok(x_gate_key: str | None = Header(default=None)) -> bool:
    return bool(config.GATE_API_KEY) and bool(x_gate_key) and hmac.compare_digest(x_gate_key, config.GATE_API_KEY)


async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.message, "code": exc.code, "details": exc.details})


async def csrf_guard(request: Request, call_next):
    """Defence in depth on top of SameSite=Strict cookies: reject cross-origin state-changing requests."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked.", "code": "csrf"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response
