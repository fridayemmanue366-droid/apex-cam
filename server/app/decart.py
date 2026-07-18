"""Server-side Decart calls — the API key lives HERE (env APEXCAM_DECART_KEY),
never in the shipped app. Proven recipe (see the desktop engine): photo is sync;
video/restyle are jobs (submit -> poll -> /content); live cam does a WS handshake
that returns a LiveKit room the client joins directly.
"""
from __future__ import annotations

import json
import os
import urllib.request

API_BASE = os.environ.get("APEXCAM_DECART_API", "https://api.decart.ai")
WS_BASE = os.environ.get("APEXCAM_DECART_WS", "wss://api3.decart.ai/v1/stream")
IMAGE_MODEL = os.environ.get("APEXCAM_DECART_IMAGE_MODEL", "lucy-image-2")
VIDEO_MODEL = os.environ.get("APEXCAM_DECART_VIDEO_MODEL", "lucy-2.5")
RESTYLE_MODEL = os.environ.get("APEXCAM_DECART_RESTYLE_MODEL", "lucy-restyle-2")
LIVE_MODEL = os.environ.get("APEXCAM_DECART_MODEL", "lucy-2.5")
# Always ask for the sharp tier — without `resolution` Decart falls back to 480p
# (832x480). With "720p" we get 1280x720. enhance_prompt = Decart's own prompt
# enhancement (better results). Both verified against the live API.
RESOLUTION = os.environ.get("APEXCAM_DECART_RESOLUTION", "720p")
ENHANCE_PROMPT = os.environ.get("APEXCAM_DECART_ENHANCE", "1") not in ("0", "false", "")
# Realtime only: "false" tracks the live camera on a face swap; "true" (Decart's
# default) can lock onto an early generated frame. Set env to "" to omit entirely.
SELF_ANCHOR = os.environ.get("APEXCAM_DECART_SELF_ANCHOR", "false") or None
FACE_PROMPT = ("Replace the person with the person in the reference image — exact "
               "same face, hair and identity, photorealistic.")
# Used when the customer has no persona photo — a prompt-only realtime transform.
DEFAULT_LIVE_PROMPT = "Photorealistic person, natural face, studio lighting, following their movements."


def _key() -> str:
    k = os.environ.get("APEXCAM_DECART_KEY")
    if not k:
        raise RuntimeError("APEXCAM_DECART_KEY not set on the server")
    return k


def generate_photo(input_bytes: bytes, prompt: str | None, reference_bytes: bytes | None) -> bytes:
    import requests

    files = {"data": ("in.jpg", input_bytes, "image/jpeg")}
    if reference_bytes:
        files["reference_image"] = ("ref.jpg", reference_bytes, "image/jpeg")
        text = prompt or FACE_PROMPT
    else:
        text = prompt or "Enhance this photo, sharp and clean."
    r = requests.post(f"{API_BASE}/v1/generate/{IMAGE_MODEL}",
                      headers={"X-API-KEY": _key()}, files=files,
                      data={"prompt": text, "resolution": RESOLUTION,
                            "enhance_prompt": "true" if ENHANCE_PROMPT else "false"},
                      timeout=(15, 300))
    if r.status_code != 200 or "image" not in (r.headers.get("content-type") or ""):
        raise RuntimeError(f"photo failed: {r.status_code} {r.text[:200]}")
    return r.content


MAX_VIDEO_BYTES = 200 * 1024 * 1024   # Decart's documented limit


def submit_job(model: str, video_bytes: bytes, prompt: str | None,
               reference_bytes: bytes | None, filename: str = "in.mp4",
               content_type: str = "video/mp4") -> str:
    import requests

    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise RuntimeError("That video is over the 200 MB limit — trim it or lower the quality.")
    is_restyle = model == RESTYLE_MODEL
    if is_restyle and prompt and reference_bytes:
        prompt = None   # restyle takes prompt XOR reference_image, never both
    if is_restyle and not prompt and not reference_bytes:
        raise RuntimeError("Choose a style, or upload a reference image to restyle from.")
    files = {"data": (filename or "in.mp4", video_bytes, content_type or "video/mp4")}
    if reference_bytes:
        files["reference_image"] = ("ref.jpg", reference_bytes, "image/jpeg")
    form: dict[str, str] = {"resolution": RESOLUTION}
    if is_restyle:
        if prompt:   # enhance_prompt is only valid alongside a text prompt
            form["prompt"] = prompt
            form["enhance_prompt"] = "true" if ENHANCE_PROMPT else "false"
    else:
        form["prompt"] = prompt if prompt is not None else (FACE_PROMPT if reference_bytes else "")
        form["enhance_prompt"] = "true" if ENHANCE_PROMPT else "false"
    import time as _time
    r = None
    last_net: Exception | None = None
    for attempt in range(3):        # big uploads die on flaky links — retry
        try:
            r = requests.post(f"{API_BASE}/v1/jobs/{model}", headers={"X-API-KEY": _key()},
                              files=files, data=form, timeout=(15, 300))
            break
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_net = exc
        if attempt < 2:
            _time.sleep(2 * (attempt + 1))
    if r is None:
        if isinstance(last_net, requests.Timeout):
            raise RuntimeError("The upload timed out — try a shorter or smaller video.")
        raise RuntimeError("Lost the internet connection while uploading. Check your "
                           "connection and try again — shorter clips upload more reliably.")
    if r.status_code in (502, 503, 504):
        raise RuntimeError("The video service is busy right now — please try again in a moment.")
    if r.status_code != 200:
        raise RuntimeError(f"Video rejected ({r.status_code}): {r.text[:160]}")
    jid = r.json().get("job_id")
    if not jid:
        raise RuntimeError("no job_id")
    return jid


def job_status(job_id: str) -> str:
    import requests

    r = requests.get(f"{API_BASE}/v1/jobs/{job_id}", headers={"X-API-KEY": _key()}, timeout=30)
    return str(r.json().get("status", "processing")) if r.status_code == 200 else "failed"


def job_content(job_id: str) -> bytes:
    import requests

    r = requests.get(f"{API_BASE}/v1/jobs/{job_id}/content",
                     headers={"X-API-KEY": _key()}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError("content not ready")
    return r.content


def video_duration(video_bytes: bytes) -> float:
    import tempfile

    import cv2

    p = os.path.join(tempfile.gettempdir(), f"apexdur_{os.getpid()}.mp4")
    try:
        with open(p, "wb") as f:
            f.write(video_bytes)
        cap = cv2.VideoCapture(p)
        frames, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 24.0
        cap.release()
        return max(0.5, float(frames) / float(fps)) if fps else 1.0
    except Exception:
        return 1.0
    finally:
        try:
            os.unlink(p)
        except Exception:
            pass


async def live_room(reference_bytes: bytes | None, prompt: str | None) -> dict:
    """Server-side WS handshake with the Decart key -> returns the LiveKit room
    ({livekit_url, token, session_id, room_name}). The client joins LiveKit
    DIRECTLY with the room token — the Decart key never leaves the server.
    (Prompt updates during the session go via /studio/live/prompt.)"""
    import asyncio
    import base64

    import websockets

    ref_b64 = base64.b64encode(reference_bytes).decode() if reference_bytes else None
    url = f"{WS_BASE}?model={LIVE_MODEL}&api_key={_key()}"
    # self_anchor feeds Lucy's own recent output back as a reference to stay stable.
    # Decart says to DISABLE it when the scene/person changes — which is exactly a
    # face swap — so it tracks the live camera instead of locking onto an early
    # (bad) generated frame. Probed against the live API: accepted, room issued.
    if SELF_ANCHOR is not None:
        url += f"&self_anchor={SELF_ANCHOR}"
    ws = await websockets.connect(url, open_timeout=25, max_size=None)
    await ws.send(json.dumps({"type": "livekit_join", "passthrough": False}))
    if ref_b64:
        # Face swap: become the reference person.
        await ws.send(json.dumps({"type": "set_image", "image_data": ref_b64,
                                  "prompt": prompt or FACE_PROMPT}))
    else:
        # No persona photo — drive Lucy with the text prompt alone. Without ANY
        # instruction Decart has nothing to generate and the output is blank.
        await ws.send(json.dumps({"type": "prompt",
                                  "prompt": prompt or DEFAULT_LIVE_PROMPT,
                                  "enhance_prompt": True}))
    info = None
    for _ in range(10):
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
        if d.get("type") == "livekit_room_info":
            info = d
            break
        if d.get("type") == "error":
            await ws.close()
            raise RuntimeError(d.get("error", "decart error"))
    if not info:
        await ws.close()
        raise RuntimeError("no room info")
    return {"ws": ws, "info": info}
