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
# Decart's schema defaults resolution to 720p CLIENT-side — if we don't send it the
# server may fall back to 480p (that's the $0.01 vs $0.02 tier). Always ask for the
# sharp one. enhance_prompt turns on Decart's own prompt enhancement (better results).
RESOLUTION = os.environ.get("APEXCAM_DECART_RESOLUTION", "720p")
ENHANCE_PROMPT = os.environ.get("APEXCAM_DECART_ENHANCE", "1") not in ("0", "false", "")
FACE_PROMPT = ("Replace the person with the person in the reference image — exact "
               "same face, hair and identity, photorealistic.")
STYLE_PROMPT = ("Apply the look, outfit and style from the reference image to the "
                "person, keeping their own face and identity.")
REFERENCE_IMG = Path("data") / "pro_reference.jpg"


def configured() -> bool:
    return bool(_load_key())


def resolve_reference(ref_mode: str, reference_bytes: bytes | None) -> tuple[bytes | None, str]:
    """Decide what (if anything) the reference image is for, and the default prompt.

    ref_mode:
      "face"  -> become the reference person (identity/face swap). Falls back to
                 the saved persona when no image is uploaded.
      "style" -> keep your own face; copy the reference's look/outfit/style.
      "none"  -> ignore any reference; the prompt alone drives the edit.
    """
    if ref_mode == "face":
        ref = reference_bytes or (REFERENCE_IMG.read_bytes() if REFERENCE_IMG.exists() else None)
        return ref, FACE_PROMPT
    if ref_mode == "style":
        return reference_bytes, STYLE_PROMPT
    return None, ""


def generate_photo(input_bytes: bytes, prompt: str | None = None,
                   ref_mode: str = "none", reference_bytes: bytes | None = None) -> bytes:
    """AI photo editing on an uploaded picture. Returns image bytes.

    The reference image only matters when `ref_mode` says so — see
    resolve_reference(). Synchronous; raises on failure."""
    import requests

    key = _load_key()
    if not key:
        raise RuntimeError("Decart key not configured")
    files = {"data": ("in.jpg", input_bytes, "image/jpeg")}
    ref, default_prompt = resolve_reference(ref_mode, reference_bytes)
    if ref:
        files["reference_image"] = ("ref.jpg", ref, "image/jpeg")
        text = prompt or default_prompt
    else:
        text = prompt or "Enhance this photo, sharp and clean."
    # Full Decart image-edit params: ask for 720p (not the cheap 480p default) and
    # let Decart enhance the prompt — both make the result noticeably sharper/better.
    form = {"prompt": text, "resolution": RESOLUTION,
            "enhance_prompt": "true" if ENHANCE_PROMPT else "false"}
    # 720p + prompt-enhancement takes longer than the old 480p default — give it room,
    # and survive a brief connection drop.
    import time as _time
    r = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{API_BASE}/v1/generate/{IMAGE_MODEL}",
                headers={"X-API-KEY": key}, files=files, data=form, timeout=(15, 300))
            break
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise RuntimeError("Lost the internet connection. Check your connection "
                                   "and try again.")
            _time.sleep(2 * (attempt + 1))
    if r.status_code != 200 or "image" not in (r.headers.get("content-type") or ""):
        raise RuntimeError(f"Decart photo failed: {r.status_code} {r.text[:200]}")
    return r.content


# --- Video / Restyle background jobs ---------------------------------------
VIDEO_MODEL = os.environ.get("APEXCAM_DECART_VIDEO_MODEL", "lucy-2.5")
RESTYLE_MODEL = os.environ.get("APEXCAM_DECART_RESTYLE_MODEL", "lucy-restyle-2")


def video_duration(video_bytes: bytes) -> float:
    """Seconds of a video (for billing). Falls back to a small default."""
    import tempfile

    import cv2

    p = Path(tempfile.gettempdir()) / f"apexdur_{os.getpid()}.mp4"
    try:
        p.write_bytes(video_bytes)
        cap = cv2.VideoCapture(str(p))
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        cap.release()
        return max(0.5, float(frames) / float(fps)) if fps else 1.0
    except Exception:
        return 1.0
    finally:
        try:
            p.unlink()
        except Exception:
            pass


MAX_VIDEO_BYTES = 200 * 1024 * 1024   # Decart's documented limit

# Above this we downscale before uploading — a phone clip is often 1080p/4K and
# tens of MB, which chokes a weak connection. Decart re-renders at 720p anyway, so
# shrinking the SOURCE costs no output quality but makes the upload far more reliable.
SHRINK_OVER_BYTES = int(os.environ.get("APEXCAM_SHRINK_OVER_MB", "8")) * 1024 * 1024
SHRINK_MAX_DIM = int(os.environ.get("APEXCAM_SHRINK_MAX_DIM", "1280"))   # cap long edge


def shrink_video(video_bytes: bytes, filename: str = "in.mp4",
                 content_type: str = "video/mp4") -> tuple[bytes, str, str]:
    """Downscale/re-encode a big clip so it uploads reliably on a weak connection.

    Returns (bytes, filename, content_type). Only touches clips that are large AND
    higher-res than we need; small/already-small ones pass straight through
    UNCHANGED (original name/type kept). If anything goes wrong we return the
    original untouched — shrinking is a best-effort speed-up, never a gate. (Note:
    re-encoding drops the audio track, which these video-to-video models don't use.)
    """
    passthrough = (video_bytes, filename or "in.mp4", content_type or "video/mp4")
    if len(video_bytes) <= SHRINK_OVER_BYTES:
        return passthrough
    import tempfile

    import cv2

    src = Path(tempfile.gettempdir()) / f"apexshrink_in_{os.getpid()}.mp4"
    dst = Path(tempfile.gettempdir()) / f"apexshrink_out_{os.getpid()}.mp4"
    try:
        src.write_bytes(video_bytes)
        cap = cv2.VideoCapture(str(src))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        if not w or not h:
            cap.release()
            return passthrough
        scale = SHRINK_MAX_DIM / float(max(w, h))
        if scale >= 1.0:
            # Already small-dimensioned (just a long clip) — re-encoding wouldn't
            # meaningfully shrink it; send as-is rather than risk quality loss.
            cap.release()
            return passthrough
        nw, nh = (int(w * scale) // 2) * 2, (int(h * scale) // 2) * 2   # even dims
        writer = cv2.VideoWriter(str(dst), cv2.VideoWriter_fourcc(*"mp4v"),
                                 float(fps), (nw, nh))
        if not writer.isOpened():
            cap.release()
            return passthrough
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            writer.write(cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA))
        cap.release()
        writer.release()
        out = dst.read_bytes()
        # Only keep the shrunk copy if it actually saved bytes and looks valid.
        if 1000 < len(out) < len(video_bytes):
            log.info("shrank video %dx%d %.1fMB -> %dx%d %.1fMB",
                     w, h, len(video_bytes) / 1e6, nw, nh, len(out) / 1e6)
            return out, "in.mp4", "video/mp4"
        return passthrough
    except Exception as exc:
        log.warning("video shrink skipped: %s", exc)
        return passthrough
    finally:
        for p in (src, dst):
            try:
                p.unlink()
            except Exception:
                pass


def submit_job(model: str, video_bytes: bytes, prompt: str | None,
               reference_bytes: bytes | None = None, filename: str = "in.mp4",
               content_type: str = "video/mp4") -> str:
    """Submit a video job. Returns the job_id. `reference_bytes` = the reference
    image (a face to become, or — for restyle — a style source). Raises with the
    REAL reason so the user sees what's wrong (format, size, busy).

    Decart's schema differs per model:
      lucy-2.5      prompt (may be "") + optional reference_image
      lucy-restyle-2 EXACTLY ONE of prompt / reference_image (never both), and
                     enhance_prompt is only allowed with a text prompt.
    Both take resolution ("720p") — we always ask for the sharp tier.
    """
    import requests

    key = _load_key()
    if not key:
        raise RuntimeError("Decart key not configured")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise RuntimeError("That video is over the 200 MB limit — trim it or lower the quality.")
    is_restyle = model == RESTYLE_MODEL
    if is_restyle and prompt and reference_bytes:
        # Restyle accepts a prompt OR a reference — not both. Prefer the reference.
        prompt = None
    if is_restyle and not prompt and not reference_bytes:
        raise RuntimeError("Choose a style, or upload a reference image to restyle from.")
    files = {"data": (filename or "in.mp4", video_bytes, content_type or "video/mp4")}
    if reference_bytes:
        files["reference_image"] = ("ref.jpg", reference_bytes, "image/jpeg")
    form: dict[str, str] = {"resolution": RESOLUTION}
    if is_restyle:
        # exactly one of prompt / reference_image; enhance only valid with prompt
        if prompt:
            form["prompt"] = prompt
            form["enhance_prompt"] = "true" if ENHANCE_PROMPT else "false"
    else:
        # lucy-2.5 requires `prompt` (may be an empty string when a reference drives it)
        form["prompt"] = prompt if prompt is not None else (FACE_PROMPT if reference_bytes else "")
        form["enhance_prompt"] = "true" if ENHANCE_PROMPT else "false"
    # Video uploads are big, so a flaky connection drops them mid-transfer. Retry a
    # couple of times before giving up, and report network failures in plain English.
    import time as _time
    r = None
    last_net: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{API_BASE}/v1/jobs/{model}", headers={"X-API-KEY": key},
                files=files, data=form, timeout=(15, 300))
            # Decart's gateway intermittently 504s on submit (restyle especially).
            # Those are transient — retry rather than failing the user's upload.
            if r.status_code in (502, 503, 504) and attempt < 2:
                _time.sleep(3 * (attempt + 1))
                r = None
                continue
            break
        except requests.Timeout as exc:
            last_net = exc
        except requests.ConnectionError as exc:
            last_net = exc
        if attempt < 2:
            _time.sleep(2 * (attempt + 1))     # 2s, 4s backoff
    if r is None:
        if last_net is None:
            raise RuntimeError("The video service is busy right now — please try again "
                               "in a moment.")
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
        raise RuntimeError(f"No job_id in response: {r.text[:200]}")
    return jid


def job_status(job_id: str) -> str:
    """pending | processing | completed | failed (best-effort)."""
    import requests

    key = _load_key()
    r = requests.get(f"{API_BASE}/v1/jobs/{job_id}", headers={"X-API-KEY": key}, timeout=30)
    if r.status_code != 200:
        return "failed"
    return str(r.json().get("status", "processing"))


def job_content(job_id: str) -> bytes:
    """Download the finished job's result video (call when status == completed)."""
    import requests

    key = _load_key()
    r = requests.get(f"{API_BASE}/v1/jobs/{job_id}/content",
                     headers={"X-API-KEY": key}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Job content failed: {r.status_code}")
    return r.content
