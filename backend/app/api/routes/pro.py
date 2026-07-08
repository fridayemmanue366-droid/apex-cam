"""Apex Cam Pro — Lucy cloud tier config/status.

Enables the premium cloud engine (Decart Lucy 2.1 via fal.ai). Off by default;
activates when an API key is set. Payment/credits are handled separately (later).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.core.logging import get_logger
from app.engines.lucy_pro import lucy_pro

log = get_logger(__name__)

# Callback Flutterwave redirects to after payment (local backend catches it).
PAY_CALLBACK = "http://127.0.0.1:8790/pro/pay/callback"

REFERENCE_IMG = Path("data") / "pro_reference.jpg"

router = APIRouter(prefix="/pro", tags=["pro"])


class ProStatus(BaseModel):
    enabled: bool
    configured: bool
    model: str
    has_reference: bool
    prompt: str
    live: bool = False
    minutes_remaining: float = 0.0
    has_credit: bool = False
    error: str | None = None


class ProConfig(BaseModel):
    enabled: bool = False
    prompt: str = ""


def _stop_pro_call() -> None:
    """Meter callback: balance hit zero -> kill the Pro call (video + voice)."""
    from app.engines.fal_voice import fal_voice

    lucy_pro.enabled = False
    fal_voice.enabled = False


def _status() -> ProStatus:
    from app.engines.pro_credits import pro_credits

    return ProStatus(**lucy_pro.status(),
                     minutes_remaining=pro_credits.remaining_minutes,
                     has_credit=pro_credits.has_credit())


@router.get("")
def get_pro() -> ProStatus:
    return _status()


@router.put("")
def set_pro(cfg: ProConfig) -> ProStatus:
    # The cloud key is server-side only (our billing) — users can't set it.
    from app.engines.pro_credits import pro_credits

    lucy_pro.prompt = cfg.prompt
    want_live = cfg.enabled and lucy_pro.configured
    if want_live:
        # Only go live if there are minutes, and start burning them — but only
        # while Lucy is actually transforming (lucy_pro.live). start() returns
        # False on an empty balance -> GO LIVE is blocked at zero.
        if pro_credits.start(_stop_pro_call, is_active=lambda: lucy_pro.live):
            lucy_pro.enabled = True
        else:
            lucy_pro.enabled = False
    else:
        lucy_pro.enabled = False
        pro_credits.stop()
    return _status()


@router.post("/reference")
async def set_reference(file: UploadFile) -> ProStatus:
    lucy_pro.set_reference(await file.read())
    return _status()


@router.get("/reference")
def get_reference() -> FileResponse:
    if not REFERENCE_IMG.exists():
        raise HTTPException(404, "No reference set")
    return FileResponse(REFERENCE_IMG)


class Credits(BaseModel):
    minutes_remaining: float
    has_credit: bool


class AddMinutes(BaseModel):
    minutes: float


@router.get("/credits")
def get_credits() -> Credits:
    from app.engines.pro_credits import pro_credits

    return Credits(minutes_remaining=pro_credits.remaining_minutes,
                   has_credit=pro_credits.has_credit())


@router.post("/credits/add")
def add_credits(a: AddMinutes) -> Credits:
    """Add minutes directly (testing/admin). Real purchases go through /pro/pay."""
    from app.engines.pro_credits import pro_credits

    pro_credits.add_minutes(a.minutes)
    return get_credits()


class Pricing(BaseModel):
    # Display is in USD (premium feel); the actual charge is in charge_currency.
    usd_per_minute: float
    charge_currency: str
    charge_per_minute: float


@router.get("/pricing")
def get_pricing() -> Pricing:
    from app.engines.flutterwave import CURRENCY, package_amount, usd_price

    return Pricing(usd_per_minute=usd_price(1),
                   charge_currency=CURRENCY,
                   charge_per_minute=package_amount(1))


# --- Payments (Flutterwave) ------------------------------------------------
class PayStart(BaseModel):
    minutes: float


class PayLink(BaseModel):
    link: str
    tx_ref: str
    amount: float


@router.post("/pay/start")
def pay_start(p: PayStart) -> PayLink:
    """Create a Flutterwave checkout for a minutes package. The app opens the
    returned link; after payment Flutterwave redirects to our callback."""
    from app.engines.flutterwave import flutterwave

    if not flutterwave.configured:
        raise HTTPException(503, "Payments not configured")
    try:
        r = flutterwave.create_payment(p.minutes, PAY_CALLBACK)
    except Exception as exc:
        raise HTTPException(502, f"Could not start payment: {exc}")
    return PayLink(**r)


@router.get("/pay/callback")
def pay_callback(status: str = "", tx_ref: str = "",
                 transaction_id: str = "") -> HTMLResponse:
    """Flutterwave redirects here after payment. Verify server-side, credit the
    minutes on success, and show a simple page the user can close."""
    from app.engines.flutterwave import flutterwave
    from app.engines.pro_credits import pro_credits

    ok = False
    minutes = 0.0
    if status in ("successful", "completed") and transaction_id:
        try:
            v = flutterwave.verify(transaction_id, tx_ref)
            if v["ok"]:
                minutes = v["minutes"]
                pro_credits.add_minutes(minutes)
                ok = True
        except Exception as exc:
            log.exception("payment verify failed: %s", exc)

    if ok:
        body = (f"<h1 style='color:#f4d06f'>Payment successful ✓</h1>"
                f"<p>{minutes:g} minutes added to Apex Pro.</p>"
                f"<p>You can close this tab and return to Apex Cam.</p>")
    else:
        body = ("<h1 style='color:#e66'>Payment not completed</h1>"
                "<p>No minutes were added. You can close this tab and try again.</p>")
    return HTMLResponse(
        f"<html><body style='font-family:sans-serif;background:#14152b;color:#f0ead9;"
        f"text-align:center;padding:60px'>{body}</body></html>")


class ProVoice(BaseModel):
    enabled: bool = False
    voice: str | None = None
    configured: bool = False
    model: str = ""


@router.get("/voice")
def get_voice() -> ProVoice:
    from app.engines.fal_voice import fal_voice

    s = fal_voice.status()
    return ProVoice(enabled=s["enabled"], voice=s["voice"],
                    configured=s["configured"], model=s["model"])


@router.put("/voice")
def set_voice(v: ProVoice) -> ProVoice:
    from app.engines.fal_voice import fal_voice

    fal_voice.set_voice(v.voice)
    fal_voice.enabled = v.enabled and fal_voice.configured
    return get_voice()
