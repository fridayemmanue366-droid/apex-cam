"""Apex Cam Pro — Lucy cloud tier config/status.

Enables the premium cloud engine (Decart Lucy 2.1 via fal.ai). Off by default;
activates when an API key is set. Payment/credits are handled separately (later).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.engines.lucy_pro import lucy_pro

REFERENCE_IMG = Path("data") / "pro_reference.jpg"

router = APIRouter(prefix="/pro", tags=["pro"])


class ProStatus(BaseModel):
    enabled: bool
    configured: bool
    model: str
    has_reference: bool
    prompt: str
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
        # Only go live if there are minutes, and start burning them. start()
        # returns False on an empty balance -> GO LIVE is blocked at zero.
        if pro_credits.start(_stop_pro_call):
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
    """Add minutes to the balance. This is where a completed purchase tops up the
    user (payment integration comes later; for now it also serves testing)."""
    from app.engines.pro_credits import pro_credits

    pro_credits.add_minutes(a.minutes)
    return get_credits()


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
