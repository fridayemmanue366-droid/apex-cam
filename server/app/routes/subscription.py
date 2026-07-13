"""Local-app subscription — a flat ₦20,000/month that unlocks Apex Cam.

New accounts get a free trial (TRIAL_DAYS); after it lapses the app is locked
until a payment. Access is stored server-side (users.access_until) so it can't be
edited on the customer's PC. Renewal is manual: each payment adds SUB_DAYS of
access, extending from whichever is later — now or the current expiry.

Payment reuses the Flutterwave flow in pay.py: /subscription/start creates the
checkout with `sub_days` in the meta, and /pay/callback applies it.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.deps import current_user
from app.pricing import (CURRENCY, SUB_DAYS, SUB_MONTHLY_NGN, sub_charge_amount,
                         sub_usd)
from app.routes.pay import FLW_SECRET, PUBLIC_URL, _flw

router = APIRouter(prefix="/subscription", tags=["subscription"])


class SubStatus(BaseModel):
    active: bool          # is the app unlocked right now?
    trial: bool           # currently in the free trial (active & never paid)
    ever_paid: bool       # has ever paid — tells an expired trial from a lapsed sub
    until: float          # unix time access expires (0 = never had access)
    days_left: float      # whole-ish days remaining (0 if expired)
    price_ngn: float      # what we charge per month
    price_usd: float      # USD equivalent, for display
    currency: str
    sub_days: int         # days added per payment


def _status(uid: int) -> SubStatus:
    until = db.access_until(uid)
    now = time.time()
    active = until > now
    ever_paid = db.has_subscription_payment(uid)
    days_left = round(max(0.0, until - now) / 86400.0, 1) if active else 0.0
    return SubStatus(
        active=active,
        trial=active and not ever_paid,
        ever_paid=ever_paid,
        until=until,
        days_left=days_left,
        price_ngn=SUB_MONTHLY_NGN,
        price_usd=sub_usd(),
        currency=CURRENCY,
        sub_days=SUB_DAYS,
    )


@router.get("")
def status(uid: int = Depends(current_user)) -> SubStatus:
    return _status(uid)


@router.post("/start")
def start(uid: int = Depends(current_user)) -> dict:
    """Create a Flutterwave checkout for one month of access. The user + sub_days
    ride in the meta, so the callback extends the right account."""
    if not FLW_SECRET:
        raise HTTPException(503, "Payments not configured")
    u = db.get_user(uid)
    tx_ref = f"apexsub-{uid}-{uuid.uuid4().hex[:12]}"
    body = {
        "tx_ref": tx_ref,
        "amount": sub_charge_amount(),
        "currency": CURRENCY,
        "redirect_url": f"{PUBLIC_URL}/pay/callback",
        "customer": {"email": u["email"]},
        "customizations": {"title": "Apex Cam subscription",
                           "description": f"{SUB_DAYS} days of Apex Cam"},
        "meta": {"user_id": uid, "sub_days": SUB_DAYS},
    }
    resp = _flw("POST", "/payments", body)
    if resp.get("status") != "success":
        raise HTTPException(502, "Could not start payment")
    return {"link": resp["data"]["link"], "tx_ref": tx_ref}
