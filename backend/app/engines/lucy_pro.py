"""Apex Cam Pro — Lucy cloud engine (Decart Lucy 2.1 real-time video-to-video).

The Pro tier streams your camera to Decart's **Lucy 2.1** real-time model (the
same class of engine MorphCam uses) and gets a fully transformed "full cam" back
— face + body + scene. This is a paid cloud feature: it needs a provider API key
and burns credits.

Provider is **config-driven** so we can point at either host without code
changes (env vars below). The two real options (verified July 2026):
  • fal.ai      — model `decart/lucy2-vton/realtime` (realtime is WebSocket via
                  the @fal-ai/client SDK; the REST image-edit path is the simple
                  one to verify with a key).
  • Decart-direct — platform.decart.ai, `@decartai/sdk`, model `lucy-2.1`
                  (contact tom@decart.ai for real-time API access; cheaper at
                  scale — switch to this once we have an audience).

Design notes:
  • The cloud key is SERVER-SIDE only (our billing/earning) — never set by users.
  • A slow cloud round-trip must never freeze the live camera, so calls run on a
    background worker: process() hands off the latest frame and returns the most
    recent cloud result immediately (the output lags a little but the stream stays
    smooth). Real-time WebSocket streaming is the production upgrade.
  • OFF unless configured; returns the input frame on any error.

Env:
  APEXCAM_LUCY_KEY          our provider API key (enables Pro when set)
  APEXCAM_LUCY_MODEL        model id (default fal-ai/decart/lucy2-vton/realtime)
  APEXCAM_LUCY_ENDPOINT     full POST URL (default https://fal.run/{model})
  APEXCAM_LUCY_AUTH_SCHEME  Authorization scheme word (default "Key"; Decart may
                            use "Bearer")
  APEXCAM_LUCY_INPUT_FIELD  request field for the frame data-uri (default image_url)
  APEXCAM_LUCY_INTERVAL     min seconds between cloud calls (default 0.2 = ~5/s)
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path("data")
REFERENCE_IMG = CONFIG_DIR / "pro_reference.jpg"
CONFIG_FILE = CONFIG_DIR / "pro_config.json"

FAL_MODEL = os.environ.get("APEXCAM_LUCY_MODEL", "fal-ai/decart/lucy2-vton/realtime")
_ENDPOINT = os.environ.get("APEXCAM_LUCY_ENDPOINT", f"https://fal.run/{FAL_MODEL}")
_AUTH_SCHEME = os.environ.get("APEXCAM_LUCY_AUTH_SCHEME", "Key")
_INPUT_FIELD = os.environ.get("APEXCAM_LUCY_INPUT_FIELD", "image_url")
_INTERVAL = float(os.environ.get("APEXCAM_LUCY_INTERVAL", "0.2"))


def _load_local_key() -> str | None:
    """For LOCAL dev/testing only: read the fal key from a gitignored file
    (backend/.lucy_key.local, `APEXCAM_LUCY_KEY=...`) if the env var isn't set.
    In production the key lives on OUR server env, never in the shipped app."""
    env = os.environ.get("APEXCAM_LUCY_KEY")
    if env:
        return env
    try:
        f = Path(__file__).resolve().parents[2] / ".lucy_key.local"
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("APEXCAM_LUCY_KEY="):
                    return line.split("=", 1)[1].strip() or None
    except Exception:
        pass
    return None


class LucyProEngine:
    def __init__(self) -> None:
        # OUR provider key — set by the operator (us) via env, NEVER by end users.
        self.api_key: str | None = _load_local_key()
        self._prompt: str = ""
        self.enabled: bool = False
        self._reference: np.ndarray | None = None
        self._last_error: str | None = None
        # Background worker (keeps the live loop from blocking on the cloud).
        self._in_frame: np.ndarray | None = None
        self._out_frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._load_config()

    # --- persistence ---------------------------------------------------------
    def _load_config(self) -> None:
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                self._prompt = str(data.get("prompt", ""))
        except Exception as exc:
            log.debug("Pro config load skipped: %s", exc)
        # Lazy-load the reference so it survives a restart.
        if REFERENCE_IMG.exists():
            try:
                import cv2

                self._reference = cv2.imread(str(REFERENCE_IMG))
            except Exception:
                self._reference = None

    def _save_config(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps({"prompt": self._prompt}))
        except Exception as exc:
            log.debug("Pro config save skipped: %s", exc)

    @property
    def prompt(self) -> str:
        return self._prompt

    @prompt.setter
    def prompt(self, value: str) -> None:
        self._prompt = value or ""
        self._save_config()

    # --- status --------------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True when OUR cloud key is set on the server — i.e. Pro is available."""
        return bool(self.api_key)

    @property
    def ready(self) -> bool:
        return self.enabled and self.configured

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "model": FAL_MODEL,
            "has_reference": self._reference is not None or REFERENCE_IMG.exists(),
            "prompt": self._prompt,
            "error": self._last_error,
        }

    def set_api_key(self, key: str | None) -> None:
        self.api_key = key or None
        self._last_error = None

    def set_reference(self, image_bytes: bytes | None) -> bool:
        import cv2

        if not image_bytes:
            self._reference = None
            return False
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        REFERENCE_IMG.write_bytes(image_bytes)
        self._reference = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        return self._reference is not None

    # --- live processing (non-blocking) -------------------------------------
    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Hand the latest frame to the background worker and return the most
        recent cloud result immediately. Never blocks the live loop; returns the
        input unchanged until the first result arrives (or if not ready)."""
        if not self.ready:
            self._ensure_stopped()
            return frame_bgr
        self._ensure_worker()
        with self._lock:
            self._in_frame = frame_bgr
            out = self._out_frame
        return out if out is not None else frame_bgr

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._out_frame = None
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _ensure_stopped(self) -> None:
        if self._worker and self._worker.is_alive():
            self._stop.set()
        with self._lock:
            self._out_frame = None

    def _worker_loop(self) -> None:
        log.info("Lucy Pro worker started (%s)", _ENDPOINT)
        while not self._stop.is_set():
            with self._lock:
                frame = None if self._in_frame is None else self._in_frame.copy()
                self._in_frame = None
            if frame is None:
                time.sleep(0.01)
                continue
            try:
                out = self._call_lucy(frame)
                with self._lock:
                    self._out_frame = out
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                log.debug("Lucy Pro call failed: %s", exc)
            time.sleep(_INTERVAL)
        log.info("Lucy Pro worker stopped")

    def _call_lucy(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Send one frame to the provider and return the transformed frame.

        REST image-edit shape (fal): POST {input_field: data-uri, prompt, [ref]}.
        The realtime model's production path is a WebSocket stream via the
        provider SDK — swap this body for the streaming client once we verify
        with a live key. Endpoint/scheme/field are env-configurable."""
        import cv2
        import urllib.request

        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return frame_bgr
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()

        payload: dict = {_INPUT_FIELD: data_uri, "prompt": self._prompt}
        if self._reference is not None:
            rok, rbuf = cv2.imencode(".jpg", self._reference)
            if rok:
                payload["reference_image_url"] = (
                    "data:image/jpeg;base64," + base64.b64encode(rbuf.tobytes()).decode())

        req = urllib.request.Request(
            _ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"{_AUTH_SCHEME} {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        resp = json.load(urllib.request.urlopen(req, timeout=20))
        # fal returns {image|video: {url}} or a top-level url; accept any.
        out_url = (resp.get("image", {}) or {}).get("url") \
            or (resp.get("video", {}) or {}).get("url") \
            or resp.get("url")
        if not out_url:
            return frame_bgr
        if out_url.startswith("data:"):
            out_bytes = base64.b64decode(out_url.split(",", 1)[1])
        else:
            out_bytes = urllib.request.urlopen(out_url, timeout=20).read()
        out = cv2.imdecode(np.frombuffer(out_bytes, np.uint8), cv2.IMREAD_COLOR)
        return out if out is not None else frame_bgr


# Singleton
lucy_pro = LucyProEngine()
