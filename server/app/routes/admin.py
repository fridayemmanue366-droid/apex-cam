"""Owner-only admin actions — granting promo/test credit and comping customers.

SECURITY: this endpoint hands out real money's worth of credit, so it is locked
down three ways:
  1. FAIL CLOSED — if APEXCAM_ADMIN_KEY is not set in the environment the route
     returns 404 and cannot be used at all. A forgotten config can never expose it.
  2. Constant-time key comparison (no timing oracle).
  3. A hard per-call cap, so even a leaked key can't drain the account in one shot.
Every grant is written to the transactions table (kind=topup, tx_ref=admin-…) so
the books stay auditable — a grant looks exactly like a payment, minus the payment.
"""
from __future__ import annotations

import hmac
import os
import uuid

from fastapi import APIRouter, Form, HTTPException

from app import db

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_GRANT_MINUTES = 600.0        # blast-radius cap for a single call


def _require_admin(key: str) -> None:
    admin = os.environ.get("APEXCAM_ADMIN_KEY") or ""
    if not admin:
        # Not configured -> behave as if the route does not exist.
        raise HTTPException(404, "Not found")
    if not hmac.compare_digest(key or "", admin):
        raise HTTPException(403, "Forbidden")


@router.post("/grant")
def grant(key: str = Form(...), email: str = Form(...), minutes: float = Form(...),
          note: str = Form("")) -> dict:
    """Add cloud credit to an account without a payment (promo, testing, refund)."""
    _require_admin(key)
    if minutes <= 0 or minutes > MAX_GRANT_MINUTES:
        raise HTTPException(400, f"minutes must be between 0 and {MAX_GRANT_MINUTES:g}")
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No account for {email}")
    uid = int(user["id"])
    seconds = float(minutes) * 60.0
    db.topup(uid, seconds, tx_ref=f"admin-{uuid.uuid4().hex}",
             detail=note or f"admin grant {minutes:g} min")
    total = db.credit_seconds(uid)
    return {"email": email, "granted_minutes": minutes,
            "credit_seconds": total, "credit_minutes": round(total / 60.0, 2)}


@router.post("/balance")
def balance(key: str = Form(...), email: str = Form(...)) -> dict:
    """Look up an account's credit (support: 'my credit didn't show')."""
    _require_admin(key)
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No account for {email}")
    secs = db.credit_seconds(int(user["id"]))
    return {"email": email, "credit_seconds": secs, "credit_minutes": round(secs / 60.0, 2)}
