"""Server-side payments (Flutterwave) — credit lands on the user's account here.

The secret key is server-side (env FLW_SECRET). A purchase is only credited after
we VERIFY it with Flutterwave and the amount matches the package (tamper-proof),
and crediting is idempotent by tx_ref so a repeated webhook/callback can't
double-credit.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import db
from app.deps import current_user
from app.pricing import (CURRENCY, PACKAGES, charge_amount, sub_charge_amount,
                         usd_price)

router = APIRouter(prefix="/pay", tags=["pay"])

FLW_BASE = "https://api.flutterwave.com/v3"
FLW_SECRET = os.environ.get("FLW_SECRET", "")
# Where Flutterwave redirects after payment (this server's public URL).
PUBLIC_URL = os.environ.get("APEXCAM_PUBLIC_URL", "http://127.0.0.1:8900")


def _flw(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{FLW_BASE}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {FLW_SECRET}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=25))


@router.get("/packages")
def packages() -> list[dict]:
    return [{"minutes": m, "usd": usd_price(m), "charge": charge_amount(m),
             "currency": CURRENCY} for m in PACKAGES]


class Buy(BaseModel):
    minutes: float


@router.post("/start")
def start(b: Buy, uid: int = Depends(current_user)) -> dict:
    """Create a checkout for the signed-in user. The user + seconds ride in meta,
    so the callback credits the right account."""
    if not FLW_SECRET:
        raise HTTPException(503, "Payments not configured")
    u = db.get_user(uid)
    tx_ref = f"apex-{uid}-{uuid.uuid4().hex[:12]}"
    quoted = charge_amount(b.minutes)     # lock the price NOW (rate may drift later)
    body = {
        "tx_ref": tx_ref,
        "amount": quoted,
        "currency": CURRENCY,
        "redirect_url": f"{PUBLIC_URL}/pay/callback",
        "customer": {"email": u["email"]},
        "customizations": {"title": "Apex Pro credit",
                           "description": f"{b.minutes} minutes"},
        # `charge` freezes the quoted price into the payment so verification checks
        # against what the customer was actually shown — immune to the live rate
        # refreshing between start and completion.
        "meta": {"user_id": uid, "seconds": float(b.minutes) * 60.0, "charge": quoted},
    }
    resp = _flw("POST", "/payments", body)
    if resp.get("status") != "success":
        raise HTTPException(502, "Could not start payment")
    return {"link": resp["data"]["link"], "tx_ref": tx_ref}


def _apply(transaction_id: str) -> bool:
    """Verify a transaction and credit the user. Idempotent. Returns True if it
    resulted in (or already was) a successful, correctly-priced payment."""
    resp = _flw("GET", f"/transactions/{transaction_id}/verify")
    d = resp.get("data", {}) if resp.get("status") == "success" else {}
    if d.get("status") != "successful":
        return False
    meta = d.get("meta") or {}
    uid = int(meta.get("user_id", 0))
    if uid <= 0 or d.get("currency") != CURRENCY:
        return False
    tx_ref = d.get("tx_ref", transaction_id)
    amount = float(d.get("amount", 0))

    # Two kinds of payment share this callback, told apart by the meta:
    #   sub_days -> a local-app subscription (extend access)
    #   seconds  -> a Pro credit top-up (add wallet seconds)
    sub_days = float(meta.get("sub_days", 0) or 0)
    if sub_days > 0:
        if abs(amount - sub_charge_amount()) > 1.0:   # amount must match the plan
            return False
        db.extend_subscription(uid, sub_days, tx_ref=tx_ref, detail="subscription")
        return True

    seconds = float(meta.get("seconds", 0))
    minutes = seconds / 60.0
    # Validate against the price QUOTED at checkout (frozen in meta), so a live-rate
    # refresh between start and completion can never reject a real payment. Older
    # payments (no frozen price) fall back to recomputing. Tolerance is small but
    # scales a touch with size to absorb rounding.
    expected = float(meta.get("charge", 0)) or charge_amount(minutes)
    if seconds <= 0 or abs(amount - expected) > max(1.0, expected * 0.02):
        return False
    db.topup(uid, seconds, tx_ref=tx_ref, detail=f"{minutes:g} min")   # idempotent
    return True


@router.get("/callback")
def callback(status: str = "", tx_ref: str = "", transaction_id: str = "") -> HTMLResponse:
    ok = False
    if status in ("successful", "completed") and transaction_id:
        try:
            ok = _apply(transaction_id)
        except Exception:
            ok = False
    msg = ("<h1 style='color:#f4d06f'>Payment successful ✓</h1><p>Your credit has been "
           "added. Return to Apex Cam.</p>") if ok else \
          ("<h1 style='color:#e66'>Payment not completed</h1><p>No credit added.</p>")
    return HTMLResponse(
        f"<html><body style='font-family:sans-serif;background:#14152b;color:#f0ead9;"
        f"text-align:center;padding:60px'>{msg}</body></html>")


@router.post("/webhook")
async def webhook(payload: dict) -> dict:
    """Flutterwave server-to-server confirmation (more reliable than the redirect).
    Verify the event's transaction and credit — idempotent."""
    data = payload.get("data", payload)
    tid = data.get("id") or data.get("transaction_id")
    if tid:
        try:
            _apply(str(tid))
        except Exception:
            pass
    return {"received": True}
