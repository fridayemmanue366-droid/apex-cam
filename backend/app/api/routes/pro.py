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
    error: str | None = None


class ProConfig(BaseModel):
    enabled: bool = False
    prompt: str = ""


def _status() -> ProStatus:
    return ProStatus(**lucy_pro.status())


@router.get("")
def get_pro() -> ProStatus:
    return _status()


@router.put("")
def set_pro(cfg: ProConfig) -> ProStatus:
    # The cloud key is server-side only (our billing) — users can't set it.
    lucy_pro.prompt = cfg.prompt
    lucy_pro.enabled = cfg.enabled and lucy_pro.configured
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
