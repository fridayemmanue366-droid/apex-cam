"""Apex Pro Studio — the non-realtime Decart modes (Photo, Video, Restyle).

Shares the same Decart key and credit wallet as the live cam. Photo is a single
synchronous call (proven working); Video and Restyle are background JOBS (submit
-> poll -> download). All are server-side; the key never ships to users.

Decart endpoints (1 credit = $0.01):
  Photo    POST /v1/generate/lucy-image-2   multipart data + reference_image + prompt  (2 credits)
  Video    POST /v1/jobs/lucy-2.5           face-swap short clip                       (4 credits/sec)
  Restyle  POST /v1/jobs/lucy-restyle-2      artistic restyle, long video               (1 credit/sec)
"""
from __future__ import annotations

import os
from pathlib import Path

from app.core.logging import get_logger
from app.engines.lucy_pro import _load_key

log = get_logger(__name__)

API_BASE = os.environ.get("APEXCAM_DECART_API", "https://api.decart.ai")
IMAGE_MODEL = os.environ.get("APEXCAM_DECART_IMAGE_MODEL", "lucy-image-2")
FACE_PROMPT = ("Replace the person with the person in the reference image — exact "
               "same face, hair and identity, photorealistic.")
REFERENCE_IMG = Path("data") / "pro_reference.jpg"


def configured() -> bool:
    return bool(_load_key())


def generate_photo(input_bytes: bytes, prompt: str | None = None,
                   face_swap: bool = False) -> bytes:
    """AI photo editing on a single uploaded picture. Returns image bytes.

    - face_swap=True: attach the persona reference + become that person.
    - face_swap=False: a free-text edit (restyle, background, outfit, add/remove,
      etc.) driven by `prompt` — no reference so the person's own face is kept.
    Synchronous; raises on failure."""
    import requests

    key = _load_key()
    if not key:
        raise RuntimeError("Decart key not configured")
    files = {"data": ("in.jpg", input_bytes, "image/jpeg")}
    if face_swap and REFERENCE_IMG.exists():
        files["reference_image"] = ("ref.jpg", REFERENCE_IMG.read_bytes(), "image/jpeg")
        text = prompt or FACE_PROMPT
    else:
        text = prompt or "Enhance this photo, sharp and clean."
    r = requests.post(
        f"{API_BASE}/v1/generate/{IMAGE_MODEL}",
        headers={"X-API-KEY": key},
        files=files,
        data={"prompt": text},
        timeout=(10, 90),
    )
    if r.status_code != 200 or "image" not in (r.headers.get("content-type") or ""):
        raise RuntimeError(f"Decart photo failed: {r.status_code} {r.text[:200]}")
    return r.content
