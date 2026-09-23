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
import logging
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
# fal answers "Concurrent session limit reached." when every Lucy slot is busy
# (seen 2026-09-23 coming and going on its own, with nothing of ours running).
# It arrives BEFORE any video is set up, so waiting it out costs nothing --
# but the old 2-4s retry just hammered it and burned the dead-attempt cap in
# seconds. Wait longer between tries, and give up after this long in total.
BUSY_RETRY_S = float(os.environ.get("APEXCAM_LUCY_BUSY_RETRY_S", "10"))
BUSY_GIVE_UP_S = float(os.environ.get("APEXCAM_LUCY_BUSY_GIVE_UP_S", "90"))
# Video that arrives but can't be decoded (seen 2026-09-23: connected, fal
# sending, every packet "Vp8Decoder() failed to decode") is billed like working
# video. Once this many damaged packets pile up with no good frame, reconnect
# right away instead of waiting out the full FIRST_FRAME_TIMEOUT.
BAD_PACKETS_RECONNECT = int(os.environ.get("APEXCAM_LUCY_BAD_PACKETS", "40"))


class _DecodeFailureCounter(logging.Filter):
    """aiortc only LOGS a damaged incoming packet (and drops it) -- nothing
    ever reaches our code, so "video is arriving but unreadable" looked exactly
    like "fal sent nothing". Count those log lines so the engine can tell."""
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if "failed to decode" in record.getMessage():
            self.count += 1
        return True


_decode_failures = _DecodeFailureCounter()
for _name in ("aiortc.codecs.vpx", "aiortc.codecs.h264"):
    logging.getLogger(_name).addFilter(_decode_failures)


def _frame_for_send(img: np.ndarray) -> np.ndarray:
    """Camera frame -> what Lucy gets: 16:9 (center-cropped, never squashed --
    a 4:3 camera used to be stretched, and the one test run that got unreadable
    video back was exactly that), capped at SEND_WIDTH x SEND_HEIGHT but NOT
    enlarged. Sending a 640x360 camera at its own size tested faster to first
    frame (6s vs 9s) and smoother (~29 vs ~18 fps) than blowing it up to 720p."""
    import cv2
    h, w = img.shape[:2]
    target = SEND_WIDTH / SEND_HEIGHT
    if abs(w / h - target) > 0.01:
        if w / h > target:
            cw = int(round(h * target))
            x = (w - cw) // 2
            img = img[:, x:x + cw]
        else:
            ch = int(round(w / target))
            y = (h - ch) // 2
            img = img[y:y + ch]
        h, w = img.shape[:2]
    if w > SEND_WIDTH:
        w, h = SEND_WIDTH, SEND_HEIGHT
    w, h = w - (w % 2), h - (h % 2)          # VP8/H264 want even sizes
    if (w, h) != (img.shape[1], img.shape[0]):
        img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img)


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
        self._connected = False   # drives billing -- see the `live` property's docstring
        self._last_frame_at = 0.0     # last time a real output frame arrived
        self._last_active = 0.0       # last time process() was called
        # Cloud mode: server minted this JWT with ITS key; this machine never
        # sees the raw fal API key. Mirrors lucy_pro.start_cloud(livekit_url, token).
        self._cloud: tuple[str, str] | None = None   # (jwt, model)
        # Cloud mode: asks our server for a FRESH jwt before a reconnect (the
        # first one expires after 5 min). Set by routes/pro.py; returns
        # (jwt, model) or raises.
        self.token_refresher = None
        self._send_shape: tuple = (SEND_HEIGHT, SEND_WIDTH, 3)
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
        """Drives customer billing (see routes/pro.py's heartbeat). Owner
        decision (2026-09-22): count the moment fal's WebRTC connection is
        actually established, not only once a real video frame is confirmed
        flowing -- a connection fal accepts but never sends video for should
        land on the customer's balance, not only ever drain the real fal.ai
        account with nothing billed to anyone. This is DELIBERATELY separate
        from `_live_flag` (a real frame actually arrived), which still alone
        decides the dead-attempt retry cap in _session_loop -- do not merge
        these two, or a connection that connects but never sends frames
        would retry forever again (the exact bug fixed a few days earlier)."""
        return self._connected

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
        # Real bug found (2026-09-17): whatever bytes were uploaded got saved
        # as-is under a ".jpg" name with no check on the ACTUAL format (PNG,
        # WEBP, HEIC screenshots all pass straight through) -- but the session
        # protocol below always labels this file "image/jpeg" in the data URI
        # sent to fal. A mismatch there can make the model silently fail to
        # use the reference at all: no explicit error, it just never produces
        # video. Always re-encode to a REAL jpeg here so the bytes match the
        # label every time, regardless of what format was actually uploaded.
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
            fh, fw = frame_bgr.shape[:2]
            oh, ow = out.shape[:2]
            if abs(fw / fh - ow / oh) < 0.01:
                out = cv2.resize(out, (fw, fh))
            else:
                # Lucy got a 16:9 center crop of a differently-shaped camera
                # (see _frame_for_send): hand it back in the camera's shape
                # with bars around it, instead of stretching the face.
                sc = min(fw / ow, fh / oh)
                nw, nh = int(ow * sc), int(oh * sc)
                canvas = np.zeros_like(frame_bgr)
                x, y = (fw - nw) // 2, (fh - nh) // 2
                canvas[y:y + nh, x:x + nw] = cv2.resize(out, (nw, nh))
                out = canvas
        return out

    # --- cloud mode (server-issued JWT; no raw key on this machine) ---------
    def start_cloud(self, jwt: str, model: str) -> None:
        if self._enabled:
            self._stop_session()
        self._cloud = (jwt, model)
        self.token_refresher = None   # the caller sets this session's own, if any
        self._last_error = None
        self._enabled = True
        self._start_session()

    def stop_cloud(self) -> None:
        self._cloud = None
        self.token_refresher = None
        self._enabled = False
        self._stop_session()

    # --- session lifecycle ---------------------------------------------------
    def _start_session(self) -> None:
        self._stop.clear()
        self._live_flag = False
        self._connected = False
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
        self._connected = False
        with self._lock:
            self._out_frame = None

    def _run(self) -> None:
        try:
            asyncio.run(self._session_loop())
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("Apex Pro (fal) session ended: %s", exc)
        self._live_flag = False
        self._connected = False

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
        attempt = 0
        busy_since: float | None = None
        while not self._stop.is_set():
            busy = False
            try:
                if attempt > 0:
                    self._refresh_cloud_token()
                attempt += 1
                await self._session_once()
            except Exception as exc:
                msg = str(exc)
                busy = "Concurrent session limit" in msg
                self._last_error = ("Lucy's servers are busy right now — retrying…"
                                    if busy else msg)
                log.warning("Apex Pro (fal) session ended (will retry): %s", msg)
            got_live = self._live_flag
            if busy and not got_live:
                # Rejected before any video was set up: not billed, not "dead".
                self._live_flag = False
                self._connected = False
                if self._stop.is_set():
                    return
                busy_since = busy_since or time.time()
                if time.time() - busy_since > BUSY_GIVE_UP_S:
                    self._last_error = ("Lucy's servers stayed busy for too long — "
                                        "please try GO LIVE again in a few minutes.")
                    log.warning("Apex Pro (fal): giving up, fal busy for %.0fs", BUSY_GIVE_UP_S)
                    self._enabled = False
                    self._cloud = None
                    return
                await asyncio.sleep(BUSY_RETRY_S + random.uniform(0, 3.0))
                continue
            busy_since = None
            self._live_flag = False
            self._connected = False   # belt-and-suspenders: this attempt's connection is over either way
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

    def _refresh_cloud_token(self) -> None:
        """Before a RECONNECT in cloud mode, swap in a fresh JWT from our
        server -- the original one is only good for 5 minutes. If the server
        can't be reached, keep the old one (it may still be valid)."""
        if self._cloud is None or self.token_refresher is None:
            return
        try:
            self._cloud = self.token_refresher()
        except Exception as exc:
            log.warning("Apex Pro (fal): could not refresh live token: %s", exc)

    async def _session_once(self) -> None:
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
                    # Same size real frames will be, so the stream doesn't
                    # change resolution the moment the camera kicks in.
                    img = np.zeros(engine._send_shape, np.uint8)
                else:
                    img = _frame_for_send(img)
                    engine._send_shape = img.shape
                f = VideoFrame.from_ndarray(img, format="bgr24")
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
                                fr = await asyncio.wait_for(track.recv(), timeout=3)
                                img = fr.to_ndarray(format="bgr24")
                                with self._lock:
                                    self._out_frame = img
                                self._live_flag = True
                                self._last_frame_at = time.time()
                                self._last_error = None
                            except asyncio.TimeoutError:
                                # REAL BUG FOUND AND FIXED (2026-09-22): this used to
                                # `return` here -- give up reading FOREVER -- the very
                                # first time a single read missed its window. The
                                # WebRTC connection itself was still open and fal could
                                # still have been about to send video (a slightly slow
                                # model start is completely normal), but nothing was
                                # listening for it anymore, so we'd never see frames
                                # that arrived moments later, and the outer loop's own
                                # timeout would eventually tear down a connection that
                                # may well have actually worked if given the chance.
                                # Now a single miss just means "try again" -- only the
                                # OUTER loop's FIRST_FRAME_TIMEOUT/STALL_TIMEOUT (real
                                # elapsed time since the last actual frame) decides
                                # when to actually give up. Shortened the per-read wait
                                # from 10s to 3s too, so a real outage is still caught
                                # within roughly the same overall budget, just with far
                                # more chances for a merely-slow start to succeed.
                                if not self._stop.is_set():
                                    self._last_error = "fal accepted the connection but hasn't sent any video yet"
                                continue
                            except Exception as exc:
                                # A REAL failure (track ended, connection torn down,
                                # etc.) -- unlike a timeout, this genuinely means stop.
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
                        self._connected = False
                    elif pc.connectionState == "connected":
                        # Billing starts here -- see the `live` property's docstring.
                        self._connected = True

                await pc.setLocalDescription(await pc.createOffer())
                await send({"type": "offer", "sdp": pc.localDescription.sdp})

                self._last_frame_at = time.time()   # start the stall clock at connect
                bad_at_start = _decode_failures.count
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
                    bad = _decode_failures.count - bad_at_start
                    if bad and not self._live_flag:
                        self._last_error = (f"video is arriving from fal but can't be decoded "
                                            f"({bad} damaged packets)")
                        if bad >= BAD_PACKETS_RECONNECT:
                            raise RuntimeError(self._last_error + " — reconnecting")
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
            self._connected = False   # safety net if connectionstatechange never fired
            if pc is not None:
                try:
                    await pc.close()
                except Exception:
                    pass


# Singleton
lucy_pro = LucyProEngine()
