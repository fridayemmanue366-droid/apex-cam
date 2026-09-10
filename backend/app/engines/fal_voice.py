"""Apex Pro — cloud voice change/cloning (fal).

The Pro tier changes the user's voice in the cloud to match their persona — this
is SEPARATE from the free local voice changer (pitch/RVC). It runs on the same
fal account as Lucy (shared server-side key), config-driven so we can point at
whatever fal speech-to-speech / voice model we settle on without code changes.

Mirrors LucyProEngine: OFF unless configured, a non-blocking background worker so
the live audio loop never stalls on the cloud, and a safe passthrough on any
error. Verifying real output needs fal balance + the chosen model — until then
this is the wired-and-ready structure.

Env:
  APEXCAM_LUCY_KEY            shared fal key (server-side; never shipped to users)
  APEXCAM_FAL_VOICE_MODEL     fal voice model id
  APEXCAM_FAL_VOICE_ENDPOINT  full POST URL (default https://fal.run/{model})
  APEXCAM_FAL_VOICE_INTERVAL  min seconds between cloud calls (default 0.2)
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

VOICE_MODEL = os.environ.get("APEXCAM_FAL_VOICE_MODEL", "fal-ai/voice-conversion")
_ENDPOINT = os.environ.get("APEXCAM_FAL_VOICE_ENDPOINT", f"https://fal.run/{VOICE_MODEL}")
_INTERVAL = float(os.environ.get("APEXCAM_FAL_VOICE_INTERVAL", "0.2"))
_SR = 16000  # audio is resampled to 16k for the cloud call


def _key() -> str | None:
    # Reuse the fal Lucy loader so both share one fal key (env or .lucy_key.local).
    # (Was pointed at lucy_pro.py, the Decart engine, which has no such function —
    # this import has been broken since the Decart switch; voice was silently dead.)
    from app.engines.fal_pro import _load_local_key
    return _load_local_key()


class FalVoiceEngine:
    def __init__(self) -> None:
        self.api_key: str | None = _key()
        self.voice: str | None = None       # selected target voice id/name
        self.enabled: bool = False
        self._last_error: str | None = None
        self._in: np.ndarray | None = None
        self._out: np.ndarray | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def ready(self) -> bool:
        return self.enabled and self.configured and bool(self.voice)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "voice": self.voice,
            "model": VOICE_MODEL,
            "error": self._last_error,
        }

    def set_voice(self, voice: str | None) -> None:
        self.voice = voice or None

    def convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Non-blocking: hand the latest audio to the worker, return the most
        recent cloud result. Passthrough until ready / first result."""
        if not self.ready:
            self._ensure_stopped()
            return samples
        self._ensure_worker()
        with self._lock:
            self._in = samples.astype(np.float32)
            out = self._out
        return out if out is not None else samples

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._out = None
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def _ensure_stopped(self) -> None:
        if self._worker and self._worker.is_alive():
            self._stop.set()
        with self._lock:
            self._out = None

    def _loop(self) -> None:
        log.info("fal voice worker started (%s)", _ENDPOINT)
        while not self._stop.is_set():
            with self._lock:
                buf = None if self._in is None else self._in.copy()
                self._in = None
            if buf is None:
                time.sleep(0.01)
                continue
            try:
                out = self._call(buf)
                with self._lock:
                    self._out = out
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                log.debug("fal voice call failed: %s", exc)
            time.sleep(_INTERVAL)
        log.info("fal voice worker stopped")

    def _call(self, samples: np.ndarray) -> np.ndarray:
        """Send audio to the fal voice model and return converted audio. The exact
        request/response shape depends on the chosen model — kept guarded so a
        mismatch passes the original audio through rather than corrupting it.
        Finalise this against the real model once fal has balance."""
        import urllib.request

        pcm16 = np.clip(samples * 32767, -32768, 32767).astype(np.int16).tobytes()
        audio_uri = "data:audio/pcm;base64," + base64.b64encode(pcm16).decode()
        payload = {"audio_url": audio_uri, "target_voice": self.voice, "sample_rate": _SR}
        req = urllib.request.Request(
            _ENDPOINT, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        resp = json.load(urllib.request.urlopen(req, timeout=20))
        out_url = (resp.get("audio", {}) or {}).get("url") or resp.get("url")
        if not out_url:
            return samples
        if out_url.startswith("data:"):
            raw = base64.b64decode(out_url.split(",", 1)[1])
        else:
            raw = urllib.request.urlopen(out_url, timeout=20).read()
        out = np.frombuffer(raw, np.int16).astype(np.float32) / 32767.0
        return out if out.size else samples


# Singleton
fal_voice = FalVoiceEngine()
