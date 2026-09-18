"""Apex Cam cloud — pricing. DYNAMIC: prices follow the live USD->NGN rate and
per-model margins, all editable in the admin panel (no redeploy).

Money model
-----------
The wallet holds "live seconds" — ONE ledger, shared by every model. Each
model (live, video, restyle, image) has its OWN cost floor and its OWN
tunable margin, priced independently: sell = cost x that model's margin. A
non-live model converts into wallet-seconds at whatever its own dollar price
is worth against live's CURRENT price (mode_rate()) — so the ledger stays
internally consistent even as any one model's margin is retuned without
touching the others.

  live     $0.02/s  ($1.20/min)  <- fal's cost for realtime Lucy, reconfirmed
                                     2026-09-18 on fal's own pricing page
  video    $0.04/s     (Decart)
  restyle  $0.01/s     (Decart)
  image    $0.08/image (fal Nano Banana 2 at 1K — see IMAGE_COST_USD)

  sell price (per mode) = cost x that mode's margin
  effective rate         = live_rate x buffer   (buffer default 1.18, covers street gap)
  charge (NGN)            = sell_usd x effective_rate

Every knob (each mode's margin, the credit price, the rate buffer, rate mode,
manual rate) is a DB setting the owner edits in /admin/panel; the env vars
below are only the first-run fallback.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from app import db

# --- Our cost to run each mode (USD) — the floor we can never sell under -----
# "live" (realtime) runs on fal now; video/restyle are still direct Decart,
# untouched by the fal migration. cloud_voice = Modal's real L4 GPU price,
# $0.000222/sec, confirmed on Modal's own pricing page — NOT guessed.
# "live" reconfirmed 2026-09-18 directly on fal's decart/lucy-2-5/realtime
# page: $0.02/s ($1.20/min), down from $0.04/s -- fal moved their own price,
# this isn't a correction of an earlier mistake here.
COST_USD_PER_SEC = {"live": 0.02, "video": 0.04, "restyle": 0.01, "cloud_voice": 0.000222}
COST_LIVE_PER_MIN = COST_USD_PER_SEC["live"] * 60.0        # $1.20/min

# Lucy Image — the ACTIVE provider's real cost, flat per image, not per
# second. fal's Nano Banana 2 at 1K is $0.08/image (confirmed on fal's
# pricing page) — update this if routes/studio.py's IMAGE_PROVIDER ever
# changes, so the admin panel's profit math stays honest.
IMAGE_COST_USD = 0.08

# Voice Notes — clone a voice from a sample, type a message, get it spoken in
# that voice. Priced per-character rather than flat like Lucy Image, since
# cost scales with message length, not a fixed unit. Owner decision
# (2026-09-16): charge 4 credits per 250-character block, not 1 — raw cost
# per block was $0.0125 on F5-TTS ($0.05/1000 chars), so 4 credits ($0.20 at
# the default credit price) was a 16x margin / ~93.75% profit per block, not
# the 4x/75% every other mode uses. Every block costs the same 4 credits
# (not just the first), so the margin stays consistent whether the message
# is short or long.
#
# SWITCHED TO ELEVENLABS 2026-09-18 (see fal_voice_note.py's module
# docstring for the full reasoning/caveats) — the real per-block cost on
# ElevenLabs hasn't been confirmed yet (no fal credit available to test),
# so this $0.05/1000-char figure is STILL THE OLD F5-TTS NUMBER, left as
# the working assumption for now. Re-check this against a real generation's
# actual fal cost as soon as credit is available, and adjust if it's wrong
# — the margin math above depends on it being roughly accurate.
VOICENOTE_COST_USD_PER_1K_CHARS = 0.05
DEFAULT_VOICENOTE_CHARS_PER_CREDIT = 250     # block size (fallback; owner-tunable below)
DEFAULT_VOICENOTE_CREDITS_PER_BLOCK = 4      # credits charged per block (fallback; owner-tunable below)

# Video/restyle USED to sell at a fixed RATIO of live's price (2x, 0.667x) —
# meaning retuning live's margin silently moved both with it, and there was
# no way to price one independently. Each now has its own margin
# (mode_margin()), defaulting to whatever preserves TODAY's actual sell price
# at live's current 1.63x margin, so this change doesn't silently cut or hike
# anyone's price on deploy — from here, the owner can move either
# independently in the panel. cloud_voice's 4.0 default is a fresh choice
# (new feature, no prior price to preserve) landing on ~$0.05/min at launch
# — cheap enough to encourage trying it, ~4x margin like the others, and
# instantly adjustable in the panel like everything else here.
DEFAULT_MODE_MARGIN = {"video": 3.26, "restyle": 4.35, "cloud_voice": 4.0}

# Minute packages the app sells (wallet minutes). Owner decision (2026-09-16,
# revised same day): trimmed from a long list [1,5,10,20,30,60,120,300,600]
# down to just two — simpler for a customer to choose from. Drives BOTH the
# desktop app's Credits tab and the mobile Voice Note page's "Buy credit"
# card (both read /pay/packages), so this one list is the single source of
# truth for either.
PACKAGES = [1, 5]

# Owner decision (2026-09-16): show/charge in USD, not NGN. This is now a
# live DB setting (currency(), below), not a fixed constant — changeable
# from the admin panel without a redeploy, same as every other pricing knob
# here. IMPORTANT real-world caveat this code can't verify on its own: the
# actual charge amount is sent to Flutterwave as this currency — switching
# to "USD" only works for real payments if the Flutterwave merchant account
# is actually enabled for USD settlement. That's an account-level setting
# on Flutterwave's side, not something this app controls.
DEFAULT_CURRENCY = os.environ.get("APEXCAM_PAY_CURRENCY", "NGN")


def currency() -> str:
    """DISPLAY currency -- what the customer SEES a price labeled in
    (packages list, admin panel, pricing snapshot). Independent of
    pay_currency() below."""
    return (db.get_setting("currency", DEFAULT_CURRENCY) or DEFAULT_CURRENCY).upper()


# Owner discovery (2026-09-17): a USD-denominated Flutterwave checkout drops
# to card-only -- bank transfer, USSD, and other local rails are NGN-only and
# Flutterwave hides them the moment the currency isn't NGN. Most Nigerian
# customers pay by transfer, not a dollar-capable card, so charging in USD
# for real (even while DISPLAYING dollars) would quietly cost conversions.
# Fix: keep the customer-facing price in USD (currency(), above) but always
# send the actual Flutterwave charge in this currency instead -- decoupled
# so the owner can flip it independently if Flutterwave's local-rail-on-USD
# limitation ever changes. Defaults to NGN (every payment method available).
DEFAULT_PAY_CURRENCY = os.environ.get("APEXCAM_FLW_CURRENCY", "NGN")


def pay_currency() -> str:
    """The currency ACTUALLY sent to Flutterwave for real charges -- may
    differ from currency() (what's merely displayed to the customer)."""
    return (db.get_setting("pay_currency", DEFAULT_PAY_CURRENCY) or DEFAULT_PAY_CURRENCY).upper()

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


def mode_margin(mode: str) -> float:
    """Video/restyle's OWN margin — independent of live's, unlike the old
    fixed-ratio system. "live" routes to margin() so callers don't need to
    special-case it."""
    if mode == "live":
        return margin()
    return max(1.0, _sf(f"margin_{mode}", DEFAULT_MODE_MARGIN.get(mode, 1.25)))


def mode_sell_usd_per_sec(mode: str) -> float:
    """What we sell one second of `mode` for, in USD."""
    return round(COST_USD_PER_SEC[mode] * mode_margin(mode), 4)


def mode_rate(mode: str) -> float:
    """Wallet-seconds (live-equivalent) burned per real second of `mode` — the
    wallet is ONE ledger in live-seconds, so a mode priced differently from
    live converts into however many live-seconds its OWN dollar price is
    worth right now. Replaces the old static MODE_RATE dict: this recomputes
    live from each mode's own tunable margin instead of a fixed ratio."""
    if mode == "live":
        return 1.0
    live_per_sec = mode_sell_usd_per_sec("live")
    return round(mode_sell_usd_per_sec(mode) / live_per_sec, 4) if live_per_sec > 0 else 0.0


def credit_usd() -> float:
    # Floor is cost PER CREDIT (an image costs IMAGE_CREDITS of them), not the
    # whole image's cost — bugged as the latter until caught: once the real
    # provider cost rose above the $0.05 default credit price, that wrongly
    # clamped every credit up to the FULL image cost instead of its 1/10 share.
    floor = IMAGE_COST_USD / IMAGE_CREDITS if IMAGE_CREDITS else IMAGE_COST_USD
    return max(floor, _sf("credit_usd", DEFAULT_CREDIT_USD))


def credits_available(credit_seconds: float) -> int:
    """How many Image/Voice-Note credits a wallet-seconds balance is worth,
    at the CURRENT credit price — floored, so it never overstates what a
    customer can actually afford (matches how /me shows "minutes" as the
    per-second view of the exact same one balance; this is the per-credit
    view of it, not a second balance)."""
    per_sec = sell_usd_per_min() / 60.0
    if per_sec <= 0:
        return 0
    usd = max(0.0, credit_seconds) * per_sec
    c = credit_usd()
    return int(usd / c) if c > 0 else 0


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
    """DISPLAY price, in currency(). NGN tracks the live dollar; USD is the raw sell."""
    usd = usd_price(minutes)
    return float(round(usd * effective_rate())) if currency() == "NGN" else usd


def pay_charge_amount(minutes: float) -> float:
    """What's ACTUALLY sent to Flutterwave, in pay_currency() -- see pay_currency()."""
    usd = usd_price(minutes)
    return float(round(usd * effective_rate())) if pay_currency() == "NGN" else usd


# --- Lucy Image (flat per-image charge, priced in credits) -------------------
def image_sell_usd() -> float:
    """What we sell one 720p image edit for, in USD — IMAGE_CREDITS x one credit."""
    return round(credit_usd() * IMAGE_CREDITS, 4)


def image_charge_amount() -> float:
    """What we charge for one image, in currency()."""
    usd = image_sell_usd()
    return float(round(usd * effective_rate())) if currency() == "NGN" else usd


def image_cost_wallet_seconds() -> float:
    """The wallet is one ledger, denominated in live-equivalent seconds — an
    image is billed by converting its USD sell price into however many
    live-seconds that same money buys AT THE CURRENT live rate, not a fixed
    number. So the ledger stays internally consistent (a customer's minutes
    balance always means the same thing) even as either price is retuned in
    the admin panel independently."""
    per_sec = sell_usd_per_min() / 60.0
    return round(image_sell_usd() / per_sec, 2) if per_sec > 0 else 0.0


# --- Voice Notes (priced per character, charged in credits) ------------------
def voicenote_chars_per_block() -> int:
    return max(1, int(_sf("voicenote_chars_per_block", DEFAULT_VOICENOTE_CHARS_PER_CREDIT)))


def voicenote_credits_per_block() -> int:
    return max(1, int(_sf("voicenote_credits_per_block", DEFAULT_VOICENOTE_CREDITS_PER_BLOCK)))


def voicenote_credits(char_count: int) -> int:
    """Credits for a message of this length — rounds UP to a whole block, so
    a 1-character message still costs a full block's credits (never zero),
    and every block (not just the first) costs voicenote_credits_per_block()."""
    import math
    blocks = max(1, math.ceil(max(0, char_count) / voicenote_chars_per_block()))
    return blocks * voicenote_credits_per_block()


def voicenote_sell_usd(char_count: int) -> float:
    return round(credit_usd() * voicenote_credits(char_count), 4)


def voicenote_charge_amount(char_count: int) -> float:
    usd = voicenote_sell_usd(char_count)
    return float(round(usd * effective_rate())) if currency() == "NGN" else usd


def voicenote_cost_wallet_seconds(char_count: int) -> float:
    """Same live-seconds conversion as image_cost_wallet_seconds() — keeps
    the one shared wallet ledger internally consistent."""
    per_sec = sell_usd_per_min() / 60.0
    return round(voicenote_sell_usd(char_count) / per_sec, 2) if per_sec > 0 else 0.0


# --- Local-app subscription (flat price, separate from the Pro wallet) -------
SUB_MONTHLY_NGN = float(os.environ.get("APEXCAM_SUB_NGN", "20000"))
SUB_DAYS = int(os.environ.get("APEXCAM_SUB_DAYS", "30"))
TRIAL_DAYS = float(os.environ.get("APEXCAM_TRIAL_DAYS", "1"))


def sub_charge_amount() -> float:
    """DISPLAY price, in currency()."""
    return SUB_MONTHLY_NGN if currency() == "NGN" else round(SUB_MONTHLY_NGN / effective_rate(), 2)


def sub_pay_amount() -> float:
    """What's ACTUALLY sent to Flutterwave, in pay_currency()."""
    return SUB_MONTHLY_NGN if pay_currency() == "NGN" else round(SUB_MONTHLY_NGN / effective_rate(), 2)


def sub_usd() -> float:
    return round(SUB_MONTHLY_NGN / effective_rate(), 2)


# --- reporting for the owner panel ------------------------------------------
def pricing_snapshot() -> dict:
    """Everything the pricing card shows: cost, live rate, effective rate, margin,
    and a per-package preview with the profit on each."""
    eff = effective_rate()
    cost_min = COST_LIVE_PER_MIN
    return {
        "currency": currency(),
        "pay_currency": pay_currency(),
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
        "voicenote_chars_per_block": voicenote_chars_per_block(),
        "voicenote_credits_per_block": voicenote_credits_per_block(),
        "voicenote_cost_usd_per_block": round(
            VOICENOTE_COST_USD_PER_1K_CHARS * voicenote_chars_per_block() / 1000.0, 4),
        "voicenote_sell_usd_per_block": voicenote_sell_usd(voicenote_chars_per_block()),
        "voicenote_charge_per_block": voicenote_charge_amount(voicenote_chars_per_block()),
        "voicenote_profit_pct": round((1.0 - (VOICENOTE_COST_USD_PER_1K_CHARS
                                              * voicenote_chars_per_block() / 1000.0)
                                       / voicenote_sell_usd(voicenote_chars_per_block())) * 100, 1)
                                 if voicenote_sell_usd(voicenote_chars_per_block()) > 0 else 0.0,
        "modes": {
            m: {
                "cost_usd_per_sec": COST_USD_PER_SEC[m],
                "margin": round(mode_margin(m), 3),
                "sell_usd_per_sec": mode_sell_usd_per_sec(m),
                "profit_pct": round((1.0 - 1.0 / mode_margin(m)) * 100, 1),
            }
            for m in ("video", "restyle", "cloud_voice")
        },
        "packages": [
            {"minutes": m,
             "charge": charge_amount(m),   # in currency() -- USD raw or NGN-converted, whichever is active
             "usd": usd_price(m),
             # Cost/profit must follow the SAME currency charge_amount() used,
             # not always multiply by the NGN rate -- that was a real bug:
             # switching to USD left these two showing NGN-scale numbers
             # mislabeled as the active currency.
             "cost": round(m * cost_min * eff, 2) if currency() == "NGN" else round(m * cost_min, 2),
             "profit": round(charge_amount(m) - (m * cost_min * eff if currency() == "NGN" else m * cost_min), 2)}
            for m in PACKAGES
        ],
    }
