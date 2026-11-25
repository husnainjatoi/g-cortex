"""Authentication endpoints (session cookie)."""
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app import config
from app.api.deps import current_actor
from app.services import auth, notifications
from app.services.actor import Actor
from app.services.errors import ServiceError


class TooManyRequests(ServiceError):
    status, code = 429, "too_many_requests"

router = APIRouter()


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class PasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)


def _me(actor: Actor, must_change: bool = False) -> dict:
    return {"user_id": actor.user_id, "full_name": actor.full_name, "role": actor.role, "scopes": sorted(actor.scopes),
            "unread_notifications": notifications.unread_count(actor), "must_change_password": must_change}


_attempts: dict[str, deque] = defaultdict(deque)
_attempts_lock = threading.Lock()


def _check_rate_limit(ip: str) -> None:
    """Block an IP that has produced too many FAILED sign-ins in the window (successful sign-ins never count)."""
    now = time.monotonic()
    with _attempts_lock:
        q = _attempts[ip]
        while q and now - q[0] > config.LOGIN_IP_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= config.LOGIN_IP_LIMIT:
            raise TooManyRequests("Too many unsuccessful sign-in attempts. Please wait a few minutes and try again.")


def _record_failure(ip: str) -> None:
    with _attempts_lock:
        _attempts[ip].append(time.monotonic())


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)
    try:
        token, actor, must_change = auth.login(body.email, body.password, request.headers.get("user-agent", ""))
    except ServiceError:
        _record_failure(ip)
        raise
    response.set_cookie(config.COOKIE_NAME, token, httponly=True, samesite="strict", secure=config.COOKIE_SECURE,
                        max_age=config.SESSION_TTL_HOURS * 3600, path="/")
    return _me(actor, must_change)


@router.post("/logout")
def logout(request: Request, response: Response, actor: Actor = Depends(current_actor)):
    auth.logout(request.cookies.get(config.COOKIE_NAME), actor)
    response.delete_cookie(config.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(actor: Actor = Depends(current_actor)):
    from app import db
    row = db.query_one("SELECT must_change_password FROM users WHERE user_id=%s", (actor.user_id,))
    return _me(actor, bool(row and row["must_change_password"]))


@router.post("/change-password")
def change_password(body: PasswordBody, request: Request, response: Response, actor: Actor = Depends(current_actor)):
    token = auth.change_password(actor, body.current_password, body.new_password, body.confirm_password, request.headers.get("user-agent", ""))
    response.set_cookie(config.COOKIE_NAME, token, httponly=True, samesite="strict", secure=config.COOKIE_SECURE, max_age=config.SESSION_TTL_HOURS * 3600, path="/")
    return {"ok": True, "message": "Your password has been changed.", "must_change_password": False}
