"""Apex Pro — payments via Flutterwave (server-side).

Turns a package purchase into real money: creates a Flutterwave checkout, and on
a verified successful payment credits the user's minutes. The SECRET key is
server-side only (loaded from env or the gitignored .flutterwave.local) — it is
NEVER shipped in the app customers download.

Flow (Flutterwave "Standard"):
  1. POST /v3/payments (secret key)  -> a hosted checkout link.
  2. Customer pays on Flutterwave.
  3. Flutterwave redirects back to our callback with a transaction_id.
  4. GET /v3/transactions/{id}/verify (secret key) -> confirm amount+status, then
     credit the minutes. Verifying server-side is what makes it tamper-proof.

Prices are in USD (fal bills USD): amount = minutes x 60s x $0.03.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)

FLW_BASE = "https://api.flutterwave.com/v3"
RATE_PER_SEC = 0.03           # what we charge the customer per second
CURRENCY = os.environ.get("APEXCAM_PAY_CURRENCY", "USD")


def _load_keys() -> dict:
    keys = {
        "public": os.environ.get("FLW_PUBLIC"),
        "secret": os.environ.get("FLW_SECRET"),
        "encryption": os.environ.get("FLW_ENCRYPTION"),
    }
    if keys["secret"]:
        return keys
    try:
        f = Path(__file__).resolve().parents[2] / ".flutterwave.local"
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("FLW_PUBLIC="):
                    keys["public"] = line.split("=", 1)[1].strip()
                elif line.startswith("FLW_SECRET="):
                    keys["secret"] = line.split("=", 1)[1].strip()
                elif line.startswith("FLW_ENCRYPTION="):
                    keys["encryption"] = line.split("=", 1)[1].strip()
    except Exception as exc:
        log.debug("flutterwave key load skipped: %s", exc)
    return keys


def package_amount(minutes: float) -> float:
    return round(minutes * 60 * RATE_PER_SEC, 2)


class FlutterwavePay:
    def __init__(self) -> None:
        k = _load_keys()
        self.public = k["public"]
        self.secret = k["secret"]
        # tx_ref -> minutes, so the callback knows what to credit (also in meta).
        self._pending: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.secret)

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{FLW_BASE}{path}", data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.secret}",
                     "Content-Type": "application/json"}, method="POST")
        return json.load(urllib.request.urlopen(req, timeout=25))

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(
            f"{FLW_BASE}{path}",
            headers={"Authorization": f"Bearer {self.secret}"}, method="GET")
        return json.load(urllib.request.urlopen(req, timeout=25))

    def create_payment(self, minutes: float, redirect_url: str,
                       email: str = "customer@apexcam.app") -> dict:
        """Create a checkout for a minutes package. Returns {link, tx_ref, amount}."""
        amount = package_amount(minutes)
        tx_ref = f"apexpro-{uuid.uuid4().hex[:16]}"
        body = {
            "tx_ref": tx_ref,
            "amount": amount,
            "currency": CURRENCY,
            "redirect_url": redirect_url,
            "customer": {"email": email},
            "customizations": {"title": "Apex Pro minutes",
                               "description": f"{minutes} minutes of Apex Pro"},
            "meta": {"minutes": minutes},
        }
        resp = self._post("/payments", body)
        if resp.get("status") != "success":
            raise RuntimeError(f"Flutterwave create failed: {resp}")
        self._pending[tx_ref] = float(minutes)
        return {"link": resp["data"]["link"], "tx_ref": tx_ref, "amount": amount}

    def verify(self, transaction_id: str, tx_ref: str | None = None) -> dict:
        """Verify a transaction server-side. Returns {ok, minutes, amount}. Only
        returns ok=True when Flutterwave reports 'successful' AND the amount
        matches what that package should cost (tamper check)."""
        resp = self._get(f"/transactions/{transaction_id}/verify")
        data = resp.get("data", {}) if resp.get("status") == "success" else {}
        ok = data.get("status") == "successful"
        minutes = float((data.get("meta") or {}).get("minutes", 0)
                        or self._pending.get(tx_ref or data.get("tx_ref", ""), 0))
        amount = float(data.get("amount", 0))
        expected = package_amount(minutes) if minutes else -1
        # amount + currency must match the package (guard against tampering)
        if ok and minutes > 0 and abs(amount - expected) < 0.01 and \
           data.get("currency") == CURRENCY:
            self._pending.pop(data.get("tx_ref", ""), None)
            return {"ok": True, "minutes": minutes, "amount": amount}
        return {"ok": False, "minutes": minutes, "amount": amount}


# Singleton
flutterwave = FlutterwavePay()
