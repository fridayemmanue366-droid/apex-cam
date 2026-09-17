"""Apex Pro — Decart Lucy live face swap (realtime, over LiveKit).

Transforms the user's whole camera into their uploaded persona in real time — the
same class of engine MorphCam uses. Proven flow (verified live on the funded
Decart account):

  1. WS connect wss://api3.decart.ai/v1/stream?model=<model>&api_key=<key>
  2. send {type:"livekit_join", passthrough:false}
  3. send {type:"set_image", image_data:<b64 reference face>, prompt:...}  -> ack
  4. recv {type:"livekit_room_info", livekit_url, token}
  5. LiveKit: connect(url, token), publish our camera frames as a VideoSource,
     subscribe to the transformed track that streams back.

The provider key is SERVER-SIDE only (our billing) — never shipped to users. The
LiveKit session runs on a background asyncio thread; process() is non-blocking
(hands the latest frame in, returns the latest transformed frame out). `live` is
True only while transformed frames are actually arriving, so minutes burn fairly.

Env: APEXCAM_DECART_KEY, APEXCAM_DECART_MODEL (default lucy-2.5),
     APEXCAM_DECART_WS (default wss://api3.decart.ai/v1/stream).
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path("data")
REFERENCE_IMG = CONFIG_DIR / "pro_reference.jpg"
CONFIG_FILE = CONFIG_DIR / "pro_config.json"

MODEL = os.environ.get("APEXCAM_DECART_MODEL", "lucy-2.5")
WS_BASE = os.environ.get("APEXCAM_DECART_WS", "wss://api3.decart.ai/v1/stream")
# Model input square. 512 is the proven value the model accepts; the model
# outputs 720p regardless, so this only conveys pose/motion. Configurable.
# Decart declares lucy-2.5 realtime as 1280x720 @ 30fps — feed it its NATIVE size
# and aspect. (A 512x512 square both under-fed it and squashed a 16:9 camera.)
WIDTH = int(os.environ.get("APEXCAM_DECART_WIDTH", "1280"))
HEIGHT = int(os.environ.get("APEXCAM_DECART_HEIGHT", "720"))
SIZE = int(os.environ.get("APEXCAM_DECART_SIZE", "512"))   # legacy/fallback
# Input frame rate to the model — higher = smoother motion. Cost is per SECOND of
# streaming (not per frame), so more fps is free. Model runs up to 30fps.
FPS = int(os.environ.get("APEXCAM_DECART_FPS", "30"))   # model's native rate
DEFAULT_PROMPT = ("Replace the person with the reference person — exact same face, "
                  "hair and identity, photorealistic, following their movements.")


def _load_key() -> str | None:
    env = os.environ.get("APEXCAM_DECART_KEY")
    if env:
        return env
    try:
        f = Path(__file__).resolve().parents[2] / ".decart.local"
        if f.exists():
            for line in f.read_text().splitlines():
                if line.startswith("APEXCAM_DECART_KEY="):
                    return line.split("=", 1)[1].strip() or None
    except Exception:
        pass
    return None


class LucyProEngine:
    def __init__(self) -> None:
        self.api_key: str | None = _load_key()
        self._prompt: str = ""
        self._enabled: bool = False
        self._reference: bytes | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()
        self._in: np.ndarray | None = None    # latest pipeline frame (BGR)
        self._out: np.ndarray | None = None    # latest transformed frame (BGR)
        self._live_flag = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Cloud mode: (livekit_url, token) issued by OUR server, which did the
        # Decart handshake with ITS key. The customer's PC never needs the key.
        self._cloud: tuple[str, str] | None = None
        self._load_config()

    # --- persistence ---------------------------------------------------------
    def _load_config(self) -> None:
        try:
            if CONFIG_FILE.exists():
                self._prompt = str(json.loads(CONFIG_FILE.read_text()).get("prompt", ""))
        except Exception:
            pass
        if REFERENCE_IMG.exists():
            try:
                self._reference = REFERENCE_IMG.read_bytes()
            except Exception:
                self._reference = None

    # --- state ---------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def ready(self) -> bool:
        # Ready with a local key (dev) OR a server-issued cloud room (customers).
        return self._enabled and (self.configured or self._cloud is not None)

    @property
    def live(self) -> bool:
        """True only while transformed frames are actually arriving (fair meter)."""
        return self._live_flag and self._enabled

    @property
    def prompt(self) -> str:
        return self._prompt

    @prompt.setter
    def prompt(self, value: str) -> None:
        self._prompt = value or ""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps({"prompt": self._prompt}))
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        value = bool(value) and self.configured
        if value and not self._enabled:
            self._enabled = True
            self._start_session()
        elif not value and self._enabled:
            self._enabled = False
            self._stop_session()

    def status(self) -> dict:
        return {
            "enabled": self._enabled,
            "configured": self.configured,
            "model": MODEL,
            "has_reference": self._reference is not None or REFERENCE_IMG.exists(),
            "prompt": self._prompt,
            "error": self._last_error,
        }

    def set_reference(self, image_bytes: bytes | None) -> bool:
        if not image_bytes:
            self._reference = None
            return False
        # Same fix as fal_pro.py's set_reference: always re-encode to a REAL
        # jpeg, regardless of what format was actually uploaded -- see that
        # file's comment for the bug this closes.
        from io import BytesIO

        from PIL import Image
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=92)
        image_bytes = buf.getvalue()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        REFERENCE_IMG.write_bytes(image_bytes)
        self._reference = image_bytes
        return True

    # --- live processing (non-blocking) -------------------------------------
    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Hand the latest frame to the live session; return the latest transformed
        frame (or the input until the first result). Never blocks the pipeline.

        Returns Lucy's output at its NATIVE resolution (720p) — the pipeline scales
        it to the camera size once. Avoids a lossy shrink-then-grow, so the persona
        stays as sharp as the model allows."""
        if not self.ready:
            return frame_bgr
        with self._lock:
            self._in = frame_bgr
            out = self._out
        return out if out is not None else frame_bgr

    # --- cloud mode (server-issued room; no key on this machine) --------------
    def start_cloud(self, livekit_url: str, token: str) -> None:
        """Join a LiveKit room our CLOUD server created via its Decart handshake.
        Bypasses the enabled-setter (which requires a local key)."""
        if self._enabled:
            self._stop_session()
        self._cloud = (livekit_url, token)
        self._last_error = None
        self._enabled = True
        self._start_session()

    def stop_cloud(self) -> None:
        self._cloud = None
        self._enabled = False
        self._stop_session()

    # --- session (background asyncio thread) --------------------------------
    def _start_session(self) -> None:
        self._stop.clear()
        self._live_flag = False
        self._last_error = None
        with self._lock:
            self._out = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _stop_session(self) -> None:
        self._stop.set()
        self._live_flag = False

    def _run(self) -> None:
        import asyncio
        try:
            asyncio.run(self._session())
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("Apex Pro (Decart) session ended: %s", exc)
        self._live_flag = False

    async def _session(self) -> None:
        import asyncio

        import websockets

        # Cloud mode: our server already did the Decart handshake (its key) and
        # set the persona; we just join the room it gave us.
        if self._cloud is not None:
            url, token = self._cloud
            await self._run_room(url, token)
            return

        ref_b64 = base64.b64encode(self._reference).decode() if self._reference else None
        url = f"{WS_BASE}?model={MODEL}&api_key={self.api_key}"
        async with websockets.connect(url, open_timeout=25, max_size=None) as ws:
            await ws.send(json.dumps({"type": "livekit_join", "passthrough": False}))
            if ref_b64:
                await ws.send(json.dumps({
                    "type": "set_image", "image_data": ref_b64,
                    "prompt": self._prompt or DEFAULT_PROMPT}))
            info = None
            for _ in range(10):
                if self._stop.is_set():
                    return
                d = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                if d.get("type") == "livekit_room_info":
                    info = d
                    break
                if d.get("type") == "error":
                    self._last_error = str(d.get("error"))
                    log.warning("Apex Pro error: %s", d.get("error"))
                    return
            if not info:
                self._last_error = "no room info from Decart"
                return
            await self._run_room(info["livekit_url"], info["token"])

    async def _run_room(self, livekit_url: str, token: str) -> None:
        """Join the LiveKit room: publish camera frames, receive Lucy's output."""
        import asyncio

        import cv2
        from livekit import rtc

        room = rtc.Room()

        @room.on("track_subscribed")
        def _on_track(track, pub, participant):  # noqa: ANN001
            if track.kind == rtc.TrackKind.KIND_VIDEO:
                asyncio.create_task(self._read_output(track))

        await room.connect(livekit_url, token)
        src = rtc.VideoSource(WIDTH, HEIGHT)
        local = rtc.LocalVideoTrack.create_video_track("cam", src)
        await room.local_participant.publish_track(
            local, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
        try:
            while not self._stop.is_set():
                with self._lock:
                    f = self._in
                if f is not None:
                    img = cv2.resize(f, (WIDTH, HEIGHT))
                    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)
                    src.capture_frame(
                        rtc.VideoFrame(WIDTH, HEIGHT, rtc.VideoBufferType.RGBA, rgba.tobytes()))
                await asyncio.sleep(1 / FPS)
        finally:
            await room.disconnect()

    async def _read_output(self, track) -> None:  # noqa: ANN001
        import cv2
        from livekit import rtc

        stream = rtc.VideoStream(track)
        async for ev in stream:
            if self._stop.is_set():
                return
            f = ev.frame.convert(rtc.VideoBufferType.RGBA)
            arr = np.frombuffer(f.data, np.uint8).reshape(f.height, f.width, 4)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            with self._lock:
                self._out = bgr
            self._live_flag = True


# Singleton
lucy_pro = LucyProEngine()
