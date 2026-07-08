"""Apex Cam Pro — Lucy cloud engine (Decart Lucy 2.1 realtime, via fal WebRTC).

The Pro tier streams your camera to Decart's **Lucy** real-time model on fal and
gets a fully transformed persona back — face + body + scene, live. This is the
PROVEN protocol (verified end-to-end):

  1. mint a short-lived JWT: POST https://rest.fal.ai/tokens/ (server-side key)
  2. WS connect wss://fal.run/decart/lucy2-vton/realtime?fal_jwt_token=<JWT>
  3. send {type:"ready"} -> receive {type:"iceservers"}
  4. WebRTC: send {type:"offer",sdp} -> receive {type:"answer",sdp}
  5. send {prompt:"..."} to set the look
  6. our camera goes out as a video track; Lucy's persona comes back as a track

Runs the WebRTC session on a background thread (its own asyncio loop). process()
is non-blocking: it hands the latest camera frame to the session and returns the
latest transformed frame (passthrough until the stream is live). The provider key
is SERVER-SIDE only (our billing) — never shipped to users.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path("data")
REFERENCE_IMG = CONFIG_DIR / "pro_reference.jpg"
CONFIG_FILE = CONFIG_DIR / "pro_config.json"

FAL_MODEL = os.environ.get("APEXCAM_LUCY_MODEL", "decart/lucy2-vton")
TOKENS_URL = os.environ.get("APEXCAM_FAL_TOKENS_URL", "https://rest.fal.ai/tokens/")
SEND_SIZE = int(os.environ.get("APEXCAM_LUCY_SEND_SIZE", "512"))  # px sent to Lucy


def _load_local_key() -> str | None:
    """LOCAL dev only: read the fal key from the gitignored .lucy_key.local if the
    env var isn't set. In production the key is on OUR server env, never shipped."""
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


def mint_token(api_key: str, app: str = FAL_MODEL, seconds: int = 300) -> str:
    """Mint a short-lived fal realtime JWT from the server-side API key."""
    body = json.dumps({"allowed_apps": [app], "token_expiration": seconds}).encode()
    req = urllib.request.Request(
        TOKENS_URL, data=body,
        headers={"Authorization": f"Key {api_key}", "Content-Type": "application/json"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())


class LucyProEngine:
    def __init__(self) -> None:
        self.api_key: str | None = _load_local_key()
        self._prompt: str = ""
        self.enabled: bool = False
        self._reference: np.ndarray | None = None
        self._last_error: str | None = None
        # Shared frame holders between the sync pipeline and the async session.
        self._in_frame: np.ndarray | None = None
        self._out_frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sent_prompt: str | None = None
        self._live = False           # true once Lucy frames are arriving
        self._last_active = 0.0      # last time process() was called
        self._load_config()

    # --- persistence ---------------------------------------------------------
    def _load_config(self) -> None:
        try:
            if CONFIG_FILE.exists():
                self._prompt = str(json.loads(CONFIG_FILE.read_text()).get("prompt", ""))
        except Exception as exc:
            log.debug("Pro config load skipped: %s", exc)
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
            "live": self.live,
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
        """Hand the latest frame to the Lucy WebRTC session, return the latest
        transformed frame. Passthrough until the stream is live / if not ready."""
        if not self.ready:
            self._ensure_stopped()
            return frame_bgr
        self._last_active = time.time()
        self._ensure_started()
        with self._lock:
            self._in_frame = frame_bgr
            out = self._out_frame
        if out is None:
            return frame_bgr
        # match Lucy's output back to the incoming frame size
        if out.shape[:2] != frame_bgr.shape[:2]:
            import cv2
            out = cv2.resize(out, (frame_bgr.shape[1], frame_bgr.shape[0]))
        return out

    @property
    def live(self) -> bool:
        """True while Lucy frames are actually flowing (used for fair metering)."""
        return self._live and (time.time() - self._last_active) < 2.0

    def _ensure_started(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._sent_prompt = None
        self._live = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _ensure_stopped(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop.set()
        with self._lock:
            self._out_frame = None
        self._live = False

    def _run(self) -> None:
        try:
            asyncio.run(self._session())
        except Exception as exc:
            self._last_error = str(exc)
            log.debug("Lucy session ended: %s", exc)
        self._live = False

    async def _session(self) -> None:
        import cv2
        import msgpack
        import websockets
        from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                            RTCSessionDescription, VideoStreamTrack)
        from av import VideoFrame

        engine = self

        class PipeTrack(VideoStreamTrack):
            async def recv(self):
                pts, tb = await self.next_timestamp()
                with engine._lock:
                    img = engine._in_frame
                if img is None:
                    img = np.zeros((SEND_SIZE, SEND_SIZE, 3), np.uint8)
                elif img.shape[0] != SEND_SIZE or img.shape[1] != SEND_SIZE:
                    img = cv2.resize(img, (SEND_SIZE, SEND_SIZE))
                f = VideoFrame.from_ndarray(np.ascontiguousarray(img), format="bgr24")
                f.pts, f.time_base = pts, tb
                return f

        def dec(r):
            return msgpack.unpackb(r) if isinstance(r, (bytes, bytearray)) else json.loads(r)

        while not self._stop.is_set():
            pc = None
            try:
                jwt = mint_token(self.api_key)
                url = f"wss://fal.run/{FAL_MODEL}/realtime?fal_jwt_token={jwt}"
                async with websockets.connect(url, max_size=None, open_timeout=45) as ws:
                    async def send(o):
                        await ws.send(msgpack.packb(o))

                    await send({"type": "ready"})
                    ice = []
                    for _ in range(6):
                        d = dec(await asyncio.wait_for(ws.recv(), timeout=10))
                        if d.get("type") == "iceservers":
                            for s in d["iceservers"]:
                                ice.append(RTCIceServer(urls=s["urls"],
                                                        username=s.get("username"),
                                                        credential=s.get("credential")))
                            break
                    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice))
                    pc.addTrack(PipeTrack())

                    @pc.on("track")
                    def on_track(track):
                        async def pull():
                            while not self._stop.is_set():
                                try:
                                    fr = await asyncio.wait_for(track.recv(), timeout=10)
                                    img = fr.to_ndarray(format="bgr24")
                                    with self._lock:
                                        self._out_frame = img
                                    self._live = True
                                    self._last_error = None   # frames flowing -> clear blips
                                except Exception:
                                    return
                        asyncio.ensure_future(pull())

                    await pc.setLocalDescription(await pc.createOffer())
                    await send({"type": "offer", "sdp": pc.localDescription.sdp})
                    await send({"prompt": self._prompt or "a photorealistic person, studio lighting"})
                    self._sent_prompt = self._prompt
                    self._last_error = None

                    while not self._stop.is_set():
                        # resend the prompt when the user changes the look
                        if self._prompt != self._sent_prompt:
                            await send({"prompt": self._prompt})
                            self._sent_prompt = self._prompt
                        try:
                            r = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        d = dec(r)
                        if d.get("type") == "answer":
                            await pc.setRemoteDescription(
                                RTCSessionDescription(sdp=d["sdp"], type="answer"))
                        elif d.get("type") in ("error", "x-fal-error"):
                            self._last_error = str(d)
            except Exception as exc:
                self._last_error = str(exc)
                log.debug("Lucy session error (will retry): %s", exc)
            finally:
                if pc is not None:
                    try:
                        await pc.close()
                    except Exception:
                        pass
                self._live = False
            if not self._stop.is_set():
                await asyncio.sleep(2.0)   # reconnect backoff


# Singleton
lucy_pro = LucyProEngine()
