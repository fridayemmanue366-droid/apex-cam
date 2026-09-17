"""Apex Cam Pro — Lucy cloud engine (Decart Lucy, via fal's realtime WebRTC relay).

Alternate provider to lucy_pro.py (Decart's own API + LiveKit). Same public shape
(process/status/prompt/set_reference/start_cloud/stop_cloud) so app/engines/pro_engine.py
can pick either one with no other code changes — see APEXCAM_PRO_PROVIDER.

PROVEN protocol (verified end-to-end against the funded fal account, including the
real identity-swap model, not just the VTON demo):

  1. mint a short-lived JWT: POST https://rest.fal.ai/tokens/ (server-side key)
  2. WS connect wss://fal.run/{model}/realtime?fal_jwt_token=<JWT>
  3. send {type:"ready"} -> recv {type:"ready"} -> recv iceservers/iceServers
     (field casing differs by model — handle both)
  4. WebRTC: our camera track goes out via aiortc; send {type:"offer", sdp}
  5. send {prompt, reference_image_url} — reference_image_url is a base64 DATA URI
     (data:image/jpeg;base64,...), NOT the `image_data` field Decart's own direct
     API uses. This was the missing piece in the original 2026-07 version of this
     engine: it never actually sent the reference photo, so the model only ever
     did generic prompt-driven styling, never real identity transfer.
  6. recv {type:"answer", sdp} -> setRemoteDescription; frames start flowing back

Frame size: send the camera's NATIVE resolution/aspect (1280x720), not a squashed
square. lucy_pro.py (Decart direct) already learned this the hard way — a 512x512
square both under-fed the model and squashed a 16:9 camera; the same applies here.

Reliability: auto-reconnects with jittered backoff on any failure (including fal's
"Concurrent session limit reached", observed in real testing to be common and
transient). A stalled-frames watchdog forces a reconnect if the socket stays open
but frames stop arriving, since a hung track otherwise looks identical to "working"
from the WS's point of view.

ICE/TURN: fal only ever returns a public STUN server in testing (no TURN), which
will fail to connect on restrictive/symmetric-NAT networks (common on corporate
and some mobile networks). APEXCAM_FAL_TURN_URL/USER/CRED let ops add a TURN
provider later without touching this file.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import threading
import time
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

CONFIG_DIR = Path("data")
REFERENCE_IMG = CONFIG_DIR / "pro_reference.jpg"
CONFIG_FILE = CONFIG_DIR / "pro_config.json"

# Adding another fal-hosted model later (per the plan to bring in more fal AI
# models) is just adding a name here and trying it — the protocol above is generic.
FAL_MODEL = os.environ.get("APEXCAM_LUCY_MODEL", "decart/lucy-2-5")
TOKENS_URL = os.environ.get("APEXCAM_FAL_TOKENS_URL", "https://rest.fal.ai/tokens/")
SEND_WIDTH = int(os.environ.get("APEXCAM_LUCY_SEND_W", "1280"))
SEND_HEIGHT = int(os.environ.get("APEXCAM_LUCY_SEND_H", "720"))
STALL_TIMEOUT = float(os.environ.get("APEXCAM_LUCY_STALL_S", "8"))  # reconnect if frames stop
# A CONNECTED session going silent for 8s is almost certainly broken. A
# session that hasn't produced its FIRST frame yet may just be slow (ICE/SDP
# negotiation over a slower network) -- give that one longer before treating
# it as dead, so this fix doesn't itself start killing legitimately-working-
# but-slow-to-connect sessions.
FIRST_FRAME_TIMEOUT = float(os.environ.get("APEXCAM_LUCY_FIRST_FRAME_S", "20"))
# Owner discovery (2026-09-17): a broken connection that never produces a single
# transformed frame used to retry FOREVER while GO LIVE stayed on -- each retry
# opens a real, billable connection to fal, so a stuck/broken session silently
# drained real fal cost with nothing ever reaching the customer's screen (and
# nothing billed to them locally either, since our own meter only counts while
# real frames are flowing). Give up after this many CONSECUTIVE attempts that
# never deliver a single live frame, instead of retrying indefinitely.
MAX_DEAD_ATTEMPTS = int(os.environ.get("APEXCAM_LUCY_MAX_DEAD_ATTEMPTS", "4"))


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
    """Mint a short-lived fal realtime JWT from the server-side API key. Call this
    from the SERVER (never the customer's PC) — the returned JWT is safe to hand
    to an untrusted client: short-lived and scoped to one app via `allowed_apps`."""
    import urllib.request

    body = json.dumps({"allowed_apps": [app], "token_expiration": seconds}).encode()
    req = urllib.request.Request(
        TOKENS_URL, data=body,
        headers={"Authorization": f"Key {api_key}", "Content-Type": "application/json"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())


def _extra_ice_servers() -> list:
    """Optional TURN server from env, appended to whatever fal provides. fal has
    only ever returned STUN in testing, which will fail on restrictive NATs."""
    url = os.environ.get("APEXCAM_FAL_TURN_URL")
    if not url:
        return []
    from aiortc import RTCIceServer
    return [RTCIceServer(urls=url,
                         username=os.environ.get("APEXCAM_FAL_TURN_USER"),
                         credential=os.environ.get("APEXCAM_FAL_TURN_CRED"))]


class LucyProEngine:
    def __init__(self) -> None:
        self.api_key: str | None = _load_local_key()
        self._prompt: str = ""
        self._enabled: bool = False
        self._reference: bytes | None = None
        self._last_error: str | None = None
        self._in_frame: np.ndarray | None = None
        self._out_frame: np.ndarray | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sent_ref: bytes | None = None
        self._sent_prompt: str | None = None
        self._live_flag = False
        self._last_frame_at = 0.0     # last time a real output frame arrived
        self._last_active = 0.0       # last time process() was called
        # Cloud mode: server minted this JWT with ITS key; this machine never
        # sees the raw fal API key. Mirrors lucy_pro.start_cloud(livekit_url, token).
        self._cloud: tuple[str, str] | None = None   # (jwt, model)
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

    # --- state -----------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def ready(self) -> bool:
        return self._enabled and (self.configured or self._cloud is not None)

    @property
    def live(self) -> bool:
        """True only while transformed frames are actually arriving (fair meter)."""
        return self._live_flag and (time.time() - self._last_frame_at) < 2.0

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
            "model": FAL_MODEL,
            "has_reference": self._reference is not None or REFERENCE_IMG.exists(),
            "prompt": self._prompt,
            "live": self.live,
            "error": self._last_error,
        }

    def set_reference(self, image_bytes: bytes | None) -> bool:
        if not image_bytes:
            self._reference = None
            return False
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        REFERENCE_IMG.write_bytes(image_bytes)
        self._reference = image_bytes
        return True

    # --- live processing (non-blocking) -------------------------------------
    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        if not self.ready:
            return frame_bgr
        self._last_active = time.time()
        with self._lock:
            self._in_frame = frame_bgr
            out = self._out_frame
        if out is None:
            return frame_bgr
        if out.shape[:2] != frame_bgr.shape[:2]:
            import cv2
            out = cv2.resize(out, (frame_bgr.shape[1], frame_bgr.shape[0]))
        return out

    # --- cloud mode (server-issued JWT; no raw key on this machine) ---------
    def start_cloud(self, jwt: str, model: str) -> None:
        if self._enabled:
            self._stop_session()
        self._cloud = (jwt, model)
        self._last_error = None
        self._enabled = True
        self._start_session()

    def stop_cloud(self) -> None:
        self._cloud = None
        self._enabled = False
        self._stop_session()

    # --- session lifecycle ---------------------------------------------------
    def _start_session(self) -> None:
        self._stop.clear()
        self._live_flag = False
        self._last_error = None
        self._sent_prompt = None
        self._sent_ref = None
        with self._lock:
            self._out_frame = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _stop_session(self) -> None:
        self._stop.set()
        self._live_flag = False
        with self._lock:
            self._out_frame = None

    def _run(self) -> None:
        try:
            asyncio.run(self._session_loop())
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("Apex Pro (fal) session ended: %s", exc)
        self._live_flag = False

    async def _session_loop(self) -> None:
        """Reconnect with jittered backoff. fal's "Concurrent session limit
        reached" was common and transient in testing — a flat retry delay risks
        repeatedly colliding with other reconnecting clients, so this backs off
        a little further each failure and adds jitter.

        A session that actually delivered at least one live frame resets the
        dead-attempt counter (it proved the connection CAN work — a later drop
        is likely a transient blip worth retrying). A session that NEVER
        delivers a frame counts toward MAX_DEAD_ATTEMPTS; hitting that gives up
        entirely (stops opening new billable fal connections) instead of
        retrying forever — see MAX_DEAD_ATTEMPTS's comment above."""
        dead_attempts = 0
        while not self._stop.is_set():
            try:
                await self._session_once()
            except Exception as exc:
                self._last_error = str(exc)
                log.debug("Apex Pro (fal) session error (will retry): %s", exc)
            got_live = self._live_flag
            self._live_flag = False
            if self._stop.is_set():
                return
            dead_attempts = 0 if got_live else dead_attempts + 1
            if dead_attempts >= MAX_DEAD_ATTEMPTS:
                self._last_error = (self._last_error or "Could not connect") + \
                    f" — gave up after {MAX_DEAD_ATTEMPTS} failed tries with no video, " \
                    "to avoid wasting cost. Try GO LIVE again."
                log.warning("Apex Pro (fal): giving up after %d dead attempts, no frames ever arrived",
                           dead_attempts)
                self._enabled = False
                self._cloud = None
                return
            delay = min(2.0 * dead_attempts, 10.0) + random.uniform(0, 1.0)
            await asyncio.sleep(delay)

    async def _session_once(self) -> None:
        import msgpack
        import websockets
        from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                            RTCSessionDescription, VideoStreamTrack)
        from av import VideoFrame

        engine = self

        class PipeTrack(VideoStreamTrack):
            async def recv(self):
                import cv2
                pts, tb = await self.next_timestamp()
                with engine._lock:
                    img = engine._in_frame
                if img is None:
                    img = np.zeros((SEND_HEIGHT, SEND_WIDTH, 3), np.uint8)
                elif img.shape[0] != SEND_HEIGHT or img.shape[1] != SEND_WIDTH:
                    img = cv2.resize(img, (SEND_WIDTH, SEND_HEIGHT))
                f = VideoFrame.from_ndarray(np.ascontiguousarray(img), format="bgr24")
                f.pts, f.time_base = pts, tb
                return f

        def dec(r):
            return msgpack.unpackb(r) if isinstance(r, (bytes, bytearray)) else json.loads(r)

        if self._cloud is not None:
            jwt, model = self._cloud
        else:
            if not self.api_key:
                raise RuntimeError("no fal key configured")
            model = FAL_MODEL
            jwt = mint_token(self.api_key, model)

        url = f"wss://fal.run/{model}/realtime?fal_jwt_token={jwt}"
        pc = None
        try:
            async with websockets.connect(url, max_size=None, open_timeout=45) as ws:
                async def send(o: dict) -> None:
                    await ws.send(msgpack.packb(o))

                await send({"type": "ready"})
                ice = list(_extra_ice_servers())
                for _ in range(8):
                    d = dec(await asyncio.wait_for(ws.recv(), timeout=10))
                    servers = d.get("iceservers") or d.get("iceServers")
                    if servers:
                        for s in servers:
                            ice.append(RTCIceServer(urls=s["urls"], username=s.get("username"),
                                                    credential=s.get("credential")))
                        break
                    if d.get("type") == "error":
                        raise RuntimeError(f"fal error before offer: {d.get('error')}")

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
                                self._live_flag = True
                                self._last_frame_at = time.time()
                                self._last_error = None
                            except asyncio.TimeoutError:
                                # The common real-world case: fal accepted the
                                # connection but never actually sent video back.
                                # str(TimeoutError()) is empty, so this needs its
                                # own readable message rather than falling into
                                # the generic branch below.
                                if not self._stop.is_set():
                                    self._last_error = "fal accepted the connection but never sent any video back"
                                return
                            except Exception as exc:
                                # Was swallowed silently before -- the customer only ever
                                # saw the generic "no frames" stall message below, never
                                # the actual reason a frame read failed.
                                if not self._stop.is_set():
                                    self._last_error = f"frame read failed: {exc}"
                                return
                    asyncio.ensure_future(pull())

                @pc.on("connectionstatechange")
                def on_connstate():
                    # The real, specific WebRTC failure reason (e.g. ICE couldn't
                    # traverse the network) -- previously invisible; the loop below
                    # would just silently wait until the generic stall timeout fired,
                    # which named a SYMPTOM ("no frames") rather than this cause.
                    if pc.connectionState in ("failed", "closed") and not self._stop.is_set():
                        self._last_error = f"WebRTC connection {pc.connectionState}"

                await pc.setLocalDescription(await pc.createOffer())
                await send({"type": "offer", "sdp": pc.localDescription.sdp})

                self._last_frame_at = time.time()   # start the stall clock at connect
                while not self._stop.is_set():
                    # (re)send prompt/reference whenever they change — also covers
                    # the very first send, right after the offer goes out.
                    if self._prompt != self._sent_prompt or self._reference != self._sent_ref:
                        msg: dict = {"prompt": self._prompt or
                                     "a photorealistic person, studio lighting"}
                        if self._reference:
                            b64 = base64.b64encode(self._reference).decode()
                            msg["reference_image_url"] = f"data:image/jpeg;base64,{b64}"
                        await send(msg)
                        self._sent_prompt = self._prompt
                        self._sent_ref = self._reference

                    # BUG FIXED: this used to only fire once `_live_flag` was already
                    # True, i.e. only AFTER at least one frame had arrived — so a
                    # connection that never produced a single frame (e.g. ICE never
                    # completing) hung here forever: no timeout, no error, and it
                    # never even reached the outer retry loop to count as a dead
                    # attempt. Now it applies from the very first connection attempt.
                    deadline = STALL_TIMEOUT if self._live_flag else FIRST_FRAME_TIMEOUT
                    if time.time() - self._last_frame_at > deadline:
                        reason = "frames stopped arriving" if self._live_flag else "no frames ever arrived"
                        # Fall back to the live WebRTC/ICE state if nothing more
                        # specific was captured — always a real, inspectable cause,
                        # never just a bare "no frames" symptom.
                        detail = self._last_error or f"webrtc={pc.connectionState} ice={pc.iceConnectionState}"
                        raise RuntimeError(f"{reason} ({deadline:.0f}s) — {detail}")

                    try:
                        r = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    d = dec(r)
                    if d.get("type") == "answer":
                        await pc.setRemoteDescription(
                            RTCSessionDescription(sdp=d["sdp"], type="answer"))
                    elif d.get("type") in ("error", "x-fal-error"):
                        raise RuntimeError(str(d))
        finally:
            if pc is not None:
                try:
                    await pc.close()
                except Exception:
                    pass


# Singleton
lucy_pro = LucyProEngine()
