"""Apex Cam Pro — Lucy cloud tier config/status.

Enables the premium cloud engine (Decart Lucy 2.1 via fal.ai). Off by default;
activates when an API key is set. Payment/credits are handled separately (later).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
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
            # One click: also turn ON the camera pipeline + publish to the virtual
            # camera, so the user doesn't have to start the engine separately.
            try:
                from app.core.pipeline import pipeline
                if not pipeline.stats().running:
                    pipeline.start()
                pipeline.enable_vcam()
            except Exception:
                log.exception("Pro GO LIVE: pipeline/vcam start failed")
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


# --- Studio: Photo mode (image-to-image face swap) -------------------------
@router.post("/photo")
async def make_photo(file: UploadFile, reference: UploadFile | None = File(None),
                     prompt: str = Form(""), ref_mode: str = Form("none"),
                     face_swap: bool = Form(False)) -> Response:
    """AI photo edit (full editor).

    ref_mode: 'face'  -> become the reference person (uploaded, or the persona)
              'style' -> keep your face, copy the reference's look/outfit
              'none'  -> ignore the reference; `prompt` alone drives the edit
    (face_swap=true is accepted as a legacy alias for ref_mode='face'.)
    Charges one photo's worth of credit up-front (refunded on failure)."""
    from app.engines.pro_credits import IMAGE_COST_SECONDS, pro_credits
    from app.engines.pro_studio import configured, generate_photo

    if not configured():
        raise HTTPException(503, "Apex Pro not configured")
    if face_swap and ref_mode == "none":
        ref_mode = "face"
    if not pro_credits.deduct(IMAGE_COST_SECONDS):
        raise HTTPException(402, "Not enough credit for a photo — top up first")
    ref_bytes = await reference.read() if reference else None
    try:
        out = generate_photo(await file.read(), prompt or None,
                             ref_mode=ref_mode, reference_bytes=ref_bytes)
    except Exception as exc:
        pro_credits.add_minutes(IMAGE_COST_SECONDS / 60.0)  # refund on failure
        log.warning("photo generation failed: %s", exc)
        raise HTTPException(502, "Could not generate the photo — please try again")
    return Response(content=out, media_type="image/png")


# --- Studio: Video (face swap) + Restyle (artistic) background jobs ---------
class JobStarted(BaseModel):
    job_id: str
    cost_minutes: float


@router.post("/video/start")
async def video_start(file: UploadFile, reference: UploadFile | None = File(None),
                      prompt: str = Form(""), mode: str = Form("video"),
                      ref_mode: str = Form("none"),
                      face_swap: bool = Form(False)) -> JobStarted:
    """Start a video job — a full video editor on lucy-2.5 (mode='video'):
      ref_mode 'face'  -> become the reference person (uploaded, or the persona)
      ref_mode 'style' -> keep your face, copy the reference's look/outfit
      ref_mode 'none'  -> `prompt` alone drives the edit (your own face kept)
    mode='restyle' uses the cheaper style-only model (no reference). Charges the
    clip length x the mode's rate up-front (refunded if the submit fails)."""
    from app.engines.pro_credits import MODE_RATE, pro_credits
    from app.engines.pro_studio import (RESTYLE_MODEL, VIDEO_MODEL, configured,
                                         resolve_reference, shrink_video,
                                         submit_job, video_duration)

    if not configured():
        raise HTTPException(503, "Apex Pro not configured")
    raw = await file.read()
    dur = video_duration(raw)   # bill on the real clip length (shrink keeps duration)
    # Downscale a big/high-res clip before the internet hop so it uploads reliably
    # on a weak connection — Decart re-renders at 720p, so no output quality is lost.
    video_bytes, up_name, up_type = shrink_video(
        raw, file.filename or "in.mp4", file.content_type or "video/mp4")
    is_restyle = mode == "restyle"
    cost = dur * MODE_RATE["restyle" if is_restyle else "video"]
    if not pro_credits.deduct(cost):
        raise HTTPException(402, "Not enough credit for this video — top up first")
    # Restyle never takes a reference (style-only model).
    ref, default_prompt = (None, "")
    if not is_restyle:
        if face_swap and ref_mode == "none":
            ref_mode = "face"
        ref_bytes = await reference.read() if reference else None
        ref, default_prompt = resolve_reference(ref_mode, ref_bytes)
    try:
        jid = submit_job(RESTYLE_MODEL if is_restyle else VIDEO_MODEL,
                         video_bytes, (prompt or default_prompt) or None, ref,
                         filename=up_name, content_type=up_type)
    except Exception as exc:
        pro_credits.add_minutes(cost / 60.0)   # refund — nothing was charged
        log.warning("video job submit failed: %s", exc)
        # Surface the real reason (format, size, busy) instead of a blank failure.
        raise HTTPException(502, str(exc) or "Could not start the video — please try again")
    return JobStarted(job_id=jid, cost_minutes=round(cost / 60.0, 2))


@router.get("/job/{job_id}")
def job_stat(job_id: str) -> dict:
    from app.engines.pro_studio import job_status

    return {"status": job_status(job_id)}


@router.get("/job/{job_id}/content")
def job_result(job_id: str) -> Response:
    from app.engines.pro_studio import job_content

    try:
        data = job_content(job_id)
    except Exception:
        raise HTTPException(404, "Result not ready")
    return Response(content=data, media_type="video/mp4")


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
