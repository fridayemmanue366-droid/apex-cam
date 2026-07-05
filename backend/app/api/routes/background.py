"""Background controls — blur / green-screen / image replace (RVM matting)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, UploadFile
from pydantic import BaseModel

from app.engines.background import background, background_available

router = APIRouter(prefix="/background", tags=["background"])

BG_DIR = Path("data")
BG_IMAGE = BG_DIR / "background.jpg"


class BackgroundSettings(BaseModel):
    mode: str = "off"          # off | blur | color | image
    blur_strength: int = 35
    color: str = "#00c800"     # green-screen colour (hex)
    available: bool = True
    has_image: bool = False


def _hex_to_bgr(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def _bgr_to_hex(bgr) -> str:
    b, g, r = bgr
    return f"#{r:02x}{g:02x}{b:02x}"


def _state() -> BackgroundSettings:
    return BackgroundSettings(
        mode=background.mode,
        blur_strength=background.blur_strength,
        color=_bgr_to_hex(background.color),
        available=background_available(),
        has_image=BG_IMAGE.exists(),
    )


@router.get("")
def get_background() -> BackgroundSettings:
    return _state()


@router.put("")
def set_background(s: BackgroundSettings) -> BackgroundSettings:
    background.mode = s.mode if s.mode in ("off", "blur", "color", "image") else "off"
    background.blur_strength = max(3, min(s.blur_strength, 99))
    background.color = _hex_to_bgr(s.color)
    if background.mode == "image" and BG_IMAGE.exists():
        background.set_bg_image(str(BG_IMAGE))
    return _state()


@router.post("/image")
async def upload_background(file: UploadFile) -> BackgroundSettings:
    BG_DIR.mkdir(parents=True, exist_ok=True)
    BG_IMAGE.write_bytes(await file.read())
    background.set_bg_image(str(BG_IMAGE))
    return _state()
