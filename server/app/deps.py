"""Shared FastAPI dependencies."""
from __future__ import annotations

from fastapi import Header, HTTPException

from app import db
from app.security import verify_token


def current_user(authorization: str = Header(default="")) -> int:
    """Resolve the caller's user id from the Bearer token, or 401."""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    uid = verify_token(token)
    if uid is None or db.get_user(uid) is None:
        raise HTTPException(401, "Not signed in")
    return uid
