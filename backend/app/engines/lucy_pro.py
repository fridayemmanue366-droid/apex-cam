"""Apex Cam Pro — Lucy cloud engine (Decart Lucy 2.1 via fal.ai).

The Pro tier streams your camera to Decart's **Lucy 2.1** real-time
video-to-video model (the same engine MorphCam uses) and gets a fully
transformed "full cam" back — face + body + scene. This is a paid cloud feature:
it needs a fal.ai API key and burns credits (~$0.02/sec at fal retail).

This module is the runtime + config. It is OFF unless configured, and safely
returns the original frame when it can't reach the cloud, so the app is never
broken by Pro being unset. Payment/credits are wired separately (later).

Provider/model are configurable so we can switch to Decart-direct or partner
pricing later without touching the pipeline.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path("data")
REFERENCE_IMG = CONFIG_DIR / "pro_reference.jpg"

# fal endpoint for Lucy 2.1 real-time / image edit. Overridable via env.
FAL_MODEL = os.environ.get("APEXCAM_LUCY_MODEL", "fal-ai/lucy-2.1")


class LucyProEngine:
    def __init__(self) -> None:
        self.api_key: str | None = None
        self.prompt: str = ""
        self.enabled: bool = False
        self._reference: np.ndarray | None = None
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
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
            "prompt": self.prompt,
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

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Transform a frame via Lucy. Returns the input unchanged if not ready
        or on any error (never breaks the live stream)."""
        if not self.ready:
            return frame_bgr
        try:
            return self._call_lucy(frame_bgr)
        except Exception as exc:
            self._last_error = str(exc)
            log.debug("Lucy Pro call failed: %s", exc)
            return frame_bgr

    def _call_lucy(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Send one frame to Lucy (fal.ai) and return the transformed frame.

        NOTE: fal's real-time Lucy uses a streaming (WebSocket) session for low
        latency; this per-frame REST path is the simple integration to verify
        with a key + credits. Swap to the streaming client for production speed.
        """
        import cv2
        import urllib.request
        import json

        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return frame_bgr
        data_uri = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
        ref_uri = None
        if self._reference is not None:
            rok, rbuf = cv2.imencode(".jpg", self._reference)
            if rok:
                ref_uri = "data:image/jpeg;base64," + base64.b64encode(rbuf.tobytes()).decode()

        payload = {"video_url": data_uri, "prompt": self.prompt}
        if ref_uri:
            payload["reference_image_url"] = ref_uri
        req = urllib.request.Request(
            f"https://fal.run/{FAL_MODEL}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Key {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        resp = json.load(urllib.request.urlopen(req, timeout=20))
        # fal returns a URL or data-uri for the output; fetch + decode.
        out_url = resp.get("image", {}).get("url") or resp.get("video", {}).get("url") \
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
