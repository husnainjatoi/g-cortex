"""Authentication endpoints (session cookie)."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app import config
from app.api.deps import current_actor
from app.services import auth, notifications
from app.services.actor import Actor

router = APIRouter()


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class PasswordBody(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _me(actor: Actor, must_change: bool = False) -> dict:
    return {"user_id": actor.user_id, "full_name": actor.full_name, "role": actor.role, "scopes": sorted(actor.scopes),
            "unread_notifications": notifications.unread_count(actor), "must_change_password": must_change}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    token, actor, must_change = auth.login(body.email, body.password, request.headers.get("user-agent", ""))
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
def change_password(body: PasswordBody, actor: Actor = Depends(current_actor)):
    auth.change_password(actor, body.current_password, body.new_password)
    return {"ok": True, "message": "Password changed. Please sign in again."}
