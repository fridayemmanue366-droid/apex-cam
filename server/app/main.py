"""Apex Cam cloud API — accounts, tamper-proof credits, payments, and the Decart
proxy (keys live here, never in the shipped app).

Run:  uvicorn app.main:app --port 8900
"""
from __future__ import annotations

import re

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import db
from app.deps import current_user
from app.security import hash_password, make_token, verify_password

app = FastAPI(title="Apex Cam Cloud", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- models --------------------------------------------------------------
class Creds(BaseModel):
    email: str
    password: str


class AuthOut(BaseModel):
    token: str
    email: str
    minutes: float


class Me(BaseModel):
    email: str
    credit_seconds: float
    minutes: float


def _minutes(seconds: float) -> float:
    return round(seconds / 60.0, 2)


# --- routes --------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/register")
def register(c: Creds) -> AuthOut:
    if not EMAIL_RE.match(c.email or ""):
        raise HTTPException(400, "Enter a valid email")
    if len(c.password or "") < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if db.get_user_by_email(c.email):
        raise HTTPException(409, "An account with that email already exists")
    uid = db.create_user(c.email, hash_password(c.password))
    return AuthOut(token=make_token(uid), email=c.email.lower(), minutes=0.0)


@app.post("/auth/login")
def login(c: Creds) -> AuthOut:
    u = db.get_user_by_email(c.email or "")
    if not u or not verify_password(c.password or "", u["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return AuthOut(token=make_token(u["id"]), email=u["email"],
                   minutes=_minutes(u["credit_seconds"]))


@app.get("/me")
def me(uid: int = Depends(current_user)) -> Me:
    u = db.get_user(uid)
    return Me(email=u["email"], credit_seconds=u["credit_seconds"],
              minutes=_minutes(u["credit_seconds"]))


# Payments, subscription, Decart studio, and the auto-update manifest.
from app.routes import pay, studio, subscription, update  # noqa: E402

app.include_router(pay.router)
app.include_router(subscription.router)
app.include_router(studio.router)
app.include_router(update.router)
