"""Apex Cam cloud — pricing. DYNAMIC: prices follow the live USD->NGN rate and the
owner's margin, both editable in the admin panel (no redeploy).

Money model
-----------
The wallet holds "live seconds"; each mode burns its own rate. We sell those
seconds for: cost x margin, converted to NGN at an effective rate that tracks
the live dollar with a safety buffer.

  live cost   $0.04/s  ($2.40/min)  <- fal's cost for realtime Lucy (the FLOOR;
                                        never sell below this). Was $0.02/s on
                                        direct Decart before the fal switch.
  video cost  $0.04/s     (Decart, unmigrated — burns 2.0 wallet-sec/real-sec)
  restyle     $0.01/s     (Decart, unmigrated — burns 0.667x)

  sell price     = cost x margin            (margin default 1.25 -> $0.05/s)
  effective rate = live_rate x buffer       (buffer default 1.18, covers street gap)
  charge (NGN)   = sell_usd x effective_rate

Every knob (margin, buffer, rate mode, manual rate) is a DB setting the owner edits
in /admin/panel; the env vars below are only the first-run fallback.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from app import db

# --- Our cost to run each mode (USD) — the floor we can never sell under -----
# "live" (realtime) runs on fal now; video/restyle/vton are still direct
# Decart, untouched by the fal migration. VTON's video mode is priced the
# same as plain Lucy video by Decart ($0.04/sec — confirmed on their pricing
# page), so it shares that cost tier rather than needing its own.
COST_USD_PER_SEC = {"live": 0.04, "video": 0.04, "restyle": 0.01, "vton": 0.04}
COST_LIVE_PER_MIN = COST_USD_PER_SEC["live"] * 60.0        # $2.40/min

# Lucy Image — the ACTIVE provider's real cost, flat per image, not per
# second. fal's Nano Banana 2 at 1K is $0.08/image (confirmed on fal's
# pricing page) — update this if routes/studio.py's IMAGE_PROVIDER ever
# changes, so the admin panel's profit math stays honest.
IMAGE_COST_USD = 0.08

# --- wallet mechanics (UNCHANGED) -------------------------------------------
# The wallet is in live-seconds. Each mode burns its own rate; a photo costs a
# fixed number of wallet-seconds. These define CONSUMPTION, not purchase price.
MODE_RATE = {"live": 1.0, "video": 2.0, "restyle": 2.0 / 3.0, "vton": 2.0}

# Minute packages the app sells (wallet minutes). The 1-min entry is the cheap
# tester for the live payment flow.
PACKAGES = [1, 5, 10, 20, 30, 60, 120, 300, 600]

CURRENCY = os.environ.get("APEXCAM_PAY_CURRENCY", "NGN")

# --- owner-tunable knobs (fallback defaults; live values live in db.settings) -
DEFAULT_MARGIN = float(os.environ.get("APEXCAM_MARGIN", "1.25"))       # sell = cost x margin
# Credits — the customer-facing unit for one-off spends (Lucy Image today,
# more later). A flat $ price, NOT a multiple of the live per-minute margin,
# so retuning live pricing never silently changes what a credit costs. The
# owner tunes this separately in the panel; it can't be set below our cost.
DEFAULT_CREDIT_USD = float(os.environ.get("APEXCAM_CREDIT_USD", "0.05"))
IMAGE_CREDITS = 10   # one Lucy Image generation = 10 credits ($0.50 at the default price)
DEFAULT_BUFFER = float(os.environ.get("APEXCAM_RATE_BUFFER", "1.18"))  # cushion on live rate
FALLBACK_RATE = float(os.environ.get("APEXCAM_NGN_PER_USD", "1600"))   # if no live rate yet
RATE_TTL = 6 * 3600.0                 # refresh the live rate at most every 6h


def _sf(key: str, default: float) -> float:
    try:
        v = db.get_setting(key, "")
        return float(v) if v else default
    except Exception:
        return default


def margin() -> float:
    return max(1.0, _sf("pricing_margin", DEFAULT_MARGIN))    # never below cost


def credit_usd() -> float:
    # Floor is cost PER CREDIT (an image costs IMAGE_CREDITS of them), not the
    # whole image's cost — bugged as the latter until caught: once the real
    # provider cost rose above the $0.05 default credit price, that wrongly
    # clamped every credit up to the FULL image cost instead of its 1/10 share.
    floor = IMAGE_COST_USD / IMAGE_CREDITS if IMAGE_CREDITS else IMAGE_COST_USD
    return max(floor, _sf("credit_usd", DEFAULT_CREDIT_USD))


def rate_buffer() -> float:
    return max(1.0, _sf("rate_buffer", DEFAULT_BUFFER))


def rate_mode() -> str:
    return db.get_setting("rate_mode", "auto") or "auto"


def manual_rate() -> float:
    return _sf("manual_rate", FALLBACK_RATE)


# --- live USD->NGN, cached in the DB, refreshed in the background ------------
_rate_lock = threading.Lock()


def _fetch_live_rate() -> float | None:
    try:
        req = urllib.request.Request("https://open.er-api.com/v6/latest/USD",
                                     headers={"User-Agent": "apexcam"})
        d = json.load(urllib.request.urlopen(req, timeout=8))
        r = float(d["rates"]["NGN"])
        return r if 500 < r < 5000 else None      # sanity guard
    except Exception:
        return None


def _refresh_live_rate() -> None:
    if not _rate_lock.acquire(blocking=False):
        return
    try:
        r = _fetch_live_rate()
        if r:
            db.set_setting("live_rate", str(round(r, 2)))
            db.set_setting("live_rate_at", str(time.time()))
    finally:
        _rate_lock.release()


def live_rate() -> float:
    """Last-known live USD->NGN, refreshed at most every 6h in the BACKGROUND so no
    request ever blocks on the network. Falls back to the env default until the
    first fetch lands."""
    cached = _sf("live_rate", 0.0)
    if time.time() - _sf("live_rate_at", 0.0) > RATE_TTL:
        threading.Thread(target=_refresh_live_rate, daemon=True).start()
    return cached or FALLBACK_RATE


def effective_rate() -> float:
    """The NGN-per-USD actually used to price packages."""
    if rate_mode() == "manual":
        return manual_rate()
    return live_rate() * rate_buffer()


# --- prices ------------------------------------------------------------------
def sell_usd_per_min() -> float:
    return COST_LIVE_PER_MIN * margin()


def usd_price(minutes: float) -> float:
    """Display price in USD (what we sell the minutes for)."""
    return round(minutes * sell_usd_per_min(), 2)


def charge_amount(minutes: float) -> float:
    """What we charge, in CURRENCY. NGN tracks the live dollar; USD is the raw sell."""
    usd = usd_price(minutes)
    return float(round(usd * effective_rate())) if CURRENCY == "NGN" else usd


# --- Lucy Image (flat per-image charge, priced in credits) -------------------
def image_sell_usd() -> float:
    """What we sell one 720p image edit for, in USD — IMAGE_CREDITS x one credit."""
    return round(credit_usd() * IMAGE_CREDITS, 4)


def image_charge_amount() -> float:
    """What we charge for one image, in CURRENCY."""
    usd = image_sell_usd()
    return float(round(usd * effective_rate())) if CURRENCY == "NGN" else usd


def image_cost_wallet_seconds() -> float:
    """The wallet is one ledger, denominated in live-equivalent seconds — an
    image is billed by converting its USD sell price into however many
    live-seconds that same money buys AT THE CURRENT live rate, not a fixed
    number. So the ledger stays internally consistent (a customer's minutes
    balance always means the same thing) even as either price is retuned in
    the admin panel independently."""
    per_sec = sell_usd_per_min() / 60.0
    return round(image_sell_usd() / per_sec, 2) if per_sec > 0 else 0.0


# --- Local-app subscription (flat price, separate from the Pro wallet) -------
SUB_MONTHLY_NGN = float(os.environ.get("APEXCAM_SUB_NGN", "20000"))
SUB_DAYS = int(os.environ.get("APEXCAM_SUB_DAYS", "30"))
TRIAL_DAYS = float(os.environ.get("APEXCAM_TRIAL_DAYS", "1"))


def sub_charge_amount() -> float:
    return SUB_MONTHLY_NGN if CURRENCY == "NGN" else round(SUB_MONTHLY_NGN / effective_rate(), 2)


def sub_usd() -> float:
    return round(SUB_MONTHLY_NGN / effective_rate(), 2)


# --- reporting for the owner panel ------------------------------------------
def pricing_snapshot() -> dict:
    """Everything the pricing card shows: cost, live rate, effective rate, margin,
    and a per-package preview with the profit on each."""
    eff = effective_rate()
    cost_min = COST_LIVE_PER_MIN
    return {
        "currency": CURRENCY,
        "live_cost_usd_per_min": round(cost_min, 2),   # fal's cost for realtime Lucy
        "live_rate": round(live_rate(), 2),
        "rate_mode": rate_mode(),
        "rate_buffer": round(rate_buffer(), 3),
        "manual_rate": round(manual_rate(), 2),
        "effective_rate": round(eff, 2),
        "margin": round(margin(), 3),
        "profit_pct": round((1.0 - 1.0 / margin()) * 100, 1),
        "image_cost_usd": IMAGE_COST_USD,
        "credit_usd": round(credit_usd(), 4),
        "image_credits": IMAGE_CREDITS,
        "image_sell_usd": image_sell_usd(),
        "image_charge": image_charge_amount(),
        "image_profit_pct": round((1.0 - IMAGE_COST_USD / image_sell_usd()) * 100, 1)
                             if image_sell_usd() > 0 else 0.0,
        "packages": [
            {"minutes": m,
             "ngn": charge_amount(m),
             "usd": usd_price(m),
             "cost_ngn": round(m * cost_min * eff),
             "profit_ngn": round(charge_amount(m) - m * cost_min * eff)}
            for m in PACKAGES
        ],
    }
