"""Apex Cam Pro — Lucy cloud tier config/status.

Enables the premium cloud engine (Decart Lucy 2.1 via fal.ai). Off by default;
activates when an API key is set. Payment/credits are handled separately (later).
"""
from __future__ import annotations

from fastapi import APIRouter, UploadFile
from pydantic import BaseModel

from app.engines.lucy_pro import lucy_pro

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
    api_key: str | None = None
    prompt: str = ""


def _status() -> ProStatus:
    return ProStatus(**lucy_pro.status())


@router.get("")
def get_pro() -> ProStatus:
    return _status()


@router.put("")
def set_pro(cfg: ProConfig) -> ProStatus:
    if cfg.api_key is not None:
        lucy_pro.set_api_key(cfg.api_key)
    lucy_pro.prompt = cfg.prompt
    lucy_pro.enabled = cfg.enabled and lucy_pro.configured
    return _status()


@router.post("/reference")
async def set_reference(file: UploadFile) -> ProStatus:
    lucy_pro.set_reference(await file.read())
    return _status()
