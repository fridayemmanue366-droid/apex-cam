"""Server-side fal calls for Lucy Image's replacement — Nano Banana 2 (Google's
Gemini image model, hosted on fal). Same fal account/key as fal_lucy.py (env
APEXCAM_LUCY_KEY), never in the shipped app.

Nano Banana 2, not Decart's Lucy Image: better instruction-following and
character consistency (identity-preserving face swap in particular), at a
similar price ($0.08/image at 1K vs Decart's $0.02, still cheap relative to
what we sell an image for). Uses the official fal_client SDK rather than
hand-rolled REST, since fal pushes upload reliability (endpoint fallback,
retries) through the SDK and doesn't fully document the raw storage API.

Unlike Decart's endpoint, nano-banana-2/edit's `image_urls` field needs real
hosted URLs, not data URIs (confirmed against fal's docs) — so every image
gets uploaded to fal's CDN first via the SDK, then referenced by URL.
"""
from __future__ import annotations

import os

MODEL = os.environ.get("APEXCAM_FAL_IMAGE_MODEL", "fal-ai/nano-banana-2/edit")
RESOLUTION = os.environ.get("APEXCAM_FAL_IMAGE_RESOLUTION", "1K")
FACE_PROMPT = ("Replace the face of the person in the first image with the face of "
               "the person in the second image. Keep everything else in the first "
               "image — pose, body, clothing, background — exactly the same. "
               "Photorealistic, seamless blend, exact identity match.")
DEFAULT_PROMPT = "Enhance this photo, sharp and clean."


def _key() -> str:
    # Same fal account as fal_lucy.py's realtime tokens — .strip(): a trailing
    # newline from a pasted env var makes an HTTP header invalid.
    k = (os.environ.get("APEXCAM_LUCY_KEY") or "").strip()
    if not k:
        raise RuntimeError("APEXCAM_LUCY_KEY not set on the server")
    return k


def generate_photo(input_bytes: bytes, prompt: str | None, reference_bytes: bytes | None) -> bytes:
    import fal_client
    import requests

    client = fal_client.SyncClient(key=_key())
    image_urls = [client.upload(input_bytes, "image/jpeg")]
    if reference_bytes:
        image_urls.append(client.upload(reference_bytes, "image/jpeg"))
        text = prompt or FACE_PROMPT
    else:
        text = prompt or DEFAULT_PROMPT

    result = client.subscribe(MODEL, arguments={
        "prompt": text,
        "image_urls": image_urls,
        "resolution": RESOLUTION,
        "output_format": "png",
        "num_images": 1,
    })
    images = result.get("images") or []
    if not images or not images[0].get("url"):
        raise RuntimeError(f"nano-banana-2 returned no image: {result}")
    r = requests.get(images[0]["url"], timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"could not fetch generated image: {r.status_code}")
    return r.content
