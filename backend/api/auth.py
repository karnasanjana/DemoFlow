"""Auth endpoints: signup / login / google / logout / me / config."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core import users
from core.auth import (
    clear_session, current_user, optional_user, set_session, verify_google_credential,
)
from core.config import settings

router = APIRouter(prefix="/api/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleRequest(BaseModel):
    credential: str


@router.get("/config")
async def auth_config() -> dict:
    """Tells the frontend whether to show the Google button."""
    return {"google_client_id": settings.google_client_id or None}


@router.get("/me")
async def me(request: Request) -> dict:
    user = optional_user(request)
    return {"user": user}


@router.post("/signup")
async def signup(body: SignupRequest, response: Response) -> dict:
    email = body.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(400, "Please enter a valid email address.")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    try:
        user = users.create_user(email, body.password, body.name)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    set_session(response, user["id"])
    return {"user": user}


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict:
    user = users.login(body.email, body.password)
    if user is None:
        raise HTTPException(401, "Incorrect email or password.")
    set_session(response, user["id"])
    return {"user": user}


@router.post("/google")
async def google(body: GoogleRequest, response: Response) -> dict:
    info = await verify_google_credential(body.credential)
    if not info["email"]:
        raise HTTPException(401, "Google did not return an email address.")
    user = users.upsert_google_user(info["sub"], info["email"], info["name"])
    set_session(response, user["id"])
    return {"user": user}


@router.post("/logout")
async def logout(response: Response) -> dict:
    clear_session(response)
    return {"ok": True}
