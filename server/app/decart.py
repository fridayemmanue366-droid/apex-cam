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
FACE_PROMPT = ("Replace the person with the person in the reference image — exact "
               "same face, hair and identity, photorealistic.")


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
                      data={"prompt": text}, timeout=(10, 90))
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
    files = {"data": (filename or "in.mp4", video_bytes, content_type or "video/mp4")}
    if reference_bytes:
        files["reference_image"] = ("ref.jpg", reference_bytes, "image/jpeg")
    try:
        r = requests.post(f"{API_BASE}/v1/jobs/{model}", headers={"X-API-KEY": _key()},
                          files=files, data={"prompt": prompt or FACE_PROMPT},
                          timeout=(10, 300))
    except requests.Timeout:
        raise RuntimeError("The upload timed out — try a shorter or smaller video.")
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
    ws = await websockets.connect(url, open_timeout=25, max_size=None)
    await ws.send(json.dumps({"type": "livekit_join", "passthrough": False}))
    if ref_b64:
        await ws.send(json.dumps({"type": "set_image", "image_data": ref_b64,
                                  "prompt": prompt or FACE_PROMPT}))
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
