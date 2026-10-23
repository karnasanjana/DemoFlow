"""SQLite-backed user store (stdlib only) for email/password + Google accounts.

A single file DB at output/demoflow.db. Passwords are stored as PBKDF2-HMAC-SHA256
hashes with a per-user salt — never in plaintext. Kept deliberately small and
synchronous; for this local tool that's plenty, and callers may wrap reads/writes
in asyncio.to_thread if they want to stay off the event loop.

GCS/CLOUD MIGRATION: swap these helpers for a managed DB (Cloud SQL / Firestore).
"""

import hashlib
import os
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from core.config import settings

_DB_PATH = settings.output_dir / "demoflow.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_PBKDF2_ROUNDS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                   id TEXT PRIMARY KEY,
                   email TEXT UNIQUE NOT NULL,
                   name TEXT NOT NULL DEFAULT '',
                   password_hash TEXT,
                   google_sub TEXT,
                   created_at TEXT NOT NULL
               )"""
        )
        _conn.commit()
    return _conn


def init_db() -> None:
    with _lock:
        _connect()


# ---- password hashing (stdlib pbkdf2) ----

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return secrets.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---- public dict shape (never leaks password_hash) ----

def _public(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"],
            "created_at": row["created_at"], "has_password": bool(row["password_hash"])}


# ---- queries ----

def get_by_email(email: str) -> sqlite3.Row | None:
    with _lock:
        cur = _connect().execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
        return cur.fetchone()


def get_user(user_id: str) -> dict | None:
    with _lock:
        cur = _connect().execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return _public(cur.fetchone())


def create_user(email: str, password: str, name: str = "") -> dict:
    """Create an email/password user. Raises ValueError if the email is taken."""
    email = email.strip().lower()
    if get_by_email(email) is not None:
        raise ValueError("An account with this email already exists.")
    uid = uuid.uuid4().hex
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, email, name.strip() or email.split("@")[0], hash_password(password), _now()),
        )
        conn.commit()
    return get_user(uid)


def login(email: str, password: str) -> dict | None:
    row = get_by_email(email)
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return _public(row)


def upsert_google_user(sub: str, email: str, name: str = "") -> dict:
    """Find-or-create a user signing in with Google, keyed by email."""
    email = email.strip().lower()
    row = get_by_email(email)
    if row is not None:
        if not row["google_sub"]:
            with _lock:
                conn = _connect()
                conn.execute("UPDATE users SET google_sub = ? WHERE id = ?", (sub, row["id"]))
                conn.commit()
        return _public(row)
    uid = uuid.uuid4().hex
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO users (id, email, name, google_sub, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, email, name.strip() or email.split("@")[0], sub, _now()),
        )
        conn.commit()
    return get_user(uid)
