"""Simple session-based authentication for temporary sharing."""

import os
import secrets
from functools import wraps
from fastapi import Request, Response, HTTPException

# Credentials from environment, with defaults
AUTH_USER = os.getenv("AUTH_USER", "guest")
AUTH_PASS = os.getenv("AUTH_PASS", "council2026")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"

# In-memory session store (fine for temporary sharing)
_sessions: set[str] = set()


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    return token


def validate_session(token: str) -> bool:
    return token in _sessions


def remove_session(token: str):
    _sessions.discard(token)


async def require_auth(request: Request):
    """Dependency that checks for a valid session cookie."""
    if not AUTH_ENABLED:
        return
    if request.method == "OPTIONS":
        return
    # Allow login/logout endpoints through
    if request.url.path in ("/api/auth/login", "/api/auth/status", "/"):
        return
    # Allow static assets through
    if request.url.path.startswith("/assets/") or request.url.path.endswith((".svg", ".ico", ".png", ".js", ".css")):
        return
    token = request.cookies.get("session")
    if not token or not validate_session(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
