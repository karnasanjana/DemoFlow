"""Cookie-session auth: a signed (HMAC) token in an httpOnly cookie, plus a
FastAPI dependency that resolves the current user. Google ID tokens are verified
against Google's tokeninfo endpoint when google_client_id is configured.

Stdlib-only signing (hmac/hashlib/base64) — no JWT library needed.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

import httpx
from fastapi import HTTPException, Request, Response

from core.config import settings
from core.users import get_user

SESSION_COOKIE = "demoflow_session"
_SESSION_DAYS = 30
_secret_cache: bytes | None = None


def _secret() -> bytes:
    """Signing key: settings.auth_secret if set, else a random one persisted to
    output/.auth_secret so sessions survive restarts in dev."""
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    if settings.auth_secret:
        _secret_cache = settings.auth_secret.encode()
        return _secret_cache
    path = settings.output_dir / ".auth_secret"
    try:
        if path.exists():
            _secret_cache = path.read_text().strip().encode()
        else:
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            token = secrets.token_urlsafe(48)
            path.write_text(token)
            _secret_cache = token.encode()
    except Exception:
        _secret_cache = secrets.token_urlsafe(48).encode()  # last resort, in-memory
    return _secret_cache


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id: str) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + _SESSION_DAYS * 86400}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def parse_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = _b64e(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload.get("uid")


def set_session(response: Response, user_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, make_token(user_id),
        max_age=_SESSION_DAYS * 86400, httponly=True, samesite="lax", path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def optional_user(request: Request) -> dict | None:
    uid = parse_token(request.cookies.get(SESSION_COOKIE))
    return get_user(uid) if uid else None


def current_user(request: Request) -> dict:
    """FastAPI dependency — 401 if not signed in."""
    user = optional_user(request)
    if user is None:
        raise HTTPException(401, "Not authenticated")
    return user


async def verify_google_credential(credential: str) -> dict:
    """Verify a Google Identity Services ID token and return {sub, email, name}.
    Uses the tokeninfo endpoint; checks the audience matches our client id."""
    if not settings.google_client_id:
        raise HTTPException(400, "Google sign-in is not configured on this server.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://oauth2.googleapis.com/tokeninfo",
                                  params={"id_token": credential})
    except Exception:
        raise HTTPException(502, "Could not reach Google to verify sign-in.")
    if r.status_code != 200:
        raise HTTPException(401, "Invalid Google sign-in token.")
    data = r.json()
    if data.get("aud") != settings.google_client_id:
        raise HTTPException(401, "Google token was issued for a different app.")
    if data.get("email_verified") not in ("true", True):
        raise HTTPException(401, "Your Google email is not verified.")
    return {"sub": data["sub"], "email": data.get("email", ""),
            "name": data.get("name", "")}
