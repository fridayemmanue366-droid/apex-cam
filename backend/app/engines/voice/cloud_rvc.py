"""Cloud voice cloning — streams mic audio to the Modal GPU service
(cloud-voice/app.py) instead of running RVC locally. Same public shape as
RVCVoiceEngine (enabled/ready/convert) so audio_pipeline.py can use either
one interchangeably, but the internals are necessarily different: the audio
callback that calls convert() runs on a tight ~10ms real-time thread
(sounddevice) that must never block on network I/O, while talking to Modal
means a WebSocket round-trip of tens to hundreds of ms.

Architecture: a background thread runs its own asyncio event loop holding
the WebSocket open for the session's lifetime. convert() itself never
touches the network — it only pushes into an input queue and pops from an
output queue, both thread-safe and bounded, so a slow/stalled network never
blocks the audio thread. If no converted audio is ready yet (still filling
the pipe, or the network hiccups), convert() passes the raw input through
unchanged for that block — same "never silence, never glitch" principle
RVCVoiceEngine's own buffering already uses locally.

Billing/session setup follows the SAME split as lucy_pro.py's cloud mode:
the server-round-trip (mint a token, heartbeat /studio/voice/cloud/tick) is
owned by the route layer (api/routes/audio.py), not this engine — this class
only owns the WebSocket + audio queues, and exposes `live` (real audio
actually flowed in the last tick) for the route's heartbeat to read, mirroring
lucy_pro.live's fair-metering contract.
"""
from __future__ import annotations

import queue
import threading

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

SR = 16000                    # the cloud model's sample rate (matches rvc.py)
_QUEUE_MAX = 50                # ~ a few seconds of 10ms blocks; bounded so a
                                # stalled network can't grow latency forever

# IMPORTANT: the wire protocol carries RAW DEVICE-RATE audio (48kHz), not
# pre-resampled 16kHz — the server's VoiceSession.convert() was ported
# directly from RVCVoiceEngine.convert() (rvc.py), which also takes device-SR
# samples and does its OWN internal 48k<->16k resampling per window. Declaring
# the real device rate in cfg and sending unresampled bytes keeps this client
# consistent with that same contract instead of resampling twice.


class CloudVoiceEngine:
    """One cloud-voice session's streaming state. Call start() with a
    session URL/token from the server (POST /studio/voice/cloud/start),
    then convert() per audio block like the local RVC engine."""

    def __init__(self) -> None:
        self.enabled = False
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._in_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=_QUEUE_MAX)
        self._out_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=_QUEUE_MAX)
        self._connected = threading.Event()
        self._error: str | None = None
        self._streaming_flag = threading.Event()   # set on each convert() that sees real audio

    @property
    def ready(self) -> bool:
        return self._thread is not None and self._connected.is_set()

    @property
    def live(self) -> bool:
        """True only while real audio has actually been pushed through convert()
        recently — the route layer's heartbeat reads this so idle/connecting
        time is never billed, same fair-metering contract as lucy_pro.live."""
        if not self.ready:
            return False
        return self._streaming_flag.is_set()

    def consume_live(self) -> bool:
        """Read-and-clear `live`, for a heartbeat that ticks every N seconds and
        wants to know "did audio flow since my last tick" rather than "is audio
        flowing THIS instant."""
        was = self._streaming_flag.is_set()
        self._streaming_flag.clear()
        return was

    @property
    def error(self) -> str | None:
        return self._error

    def start(self, ws_url: str, voice_token: str, voice_name: str, pitch_shift: int,
             sample_rate: int = 48000) -> None:
        """Begin connecting in the background. Non-blocking — check `ready`
        before relying on convert() actually converting (it safely
        passes audio through until then). `sample_rate` is the DEVICE rate
        this session's convert() calls will use (audio_pipeline.py's
        SAMPLE_RATE, normally 48000) — declared once in the cfg handshake."""
        self.stop()
        self._stop_evt.clear()
        self._connected.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, args=(ws_url, voice_token, voice_name, pitch_shift, sample_rate),
            daemon=True, name="cloud-voice-ws")
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_evt.set()
        self._thread.join(timeout=5)
        self._thread = None
        self._connected.clear()
        with self._in_q.mutex:
            self._in_q.queue.clear()
        with self._out_q.mutex:
            self._out_q.queue.clear()

    def convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Called from the real-time audio thread — must never block. Pushes
        the block for the background thread to send, and returns whatever
        converted audio is ready (or the raw block unchanged if nothing is
        ready yet)."""
        if not self.enabled or not self.ready:
            return samples
        self._streaming_flag.set()
        try:
            self._in_q.put_nowait((samples.copy(), sample_rate))
        except queue.Full:
            pass   # network's behind — drop this block rather than block the audio thread
        try:
            out = self._out_q.get_nowait()
            if len(out) == len(samples):
                return out
        except queue.Empty:
            pass
        return samples

    # -- background thread: owns the WebSocket send/receive loop ------------
    def _run(self, ws_url: str, voice_token: str, voice_name: str, pitch_shift: int,
            sample_rate: int) -> None:
        import asyncio
        asyncio.run(self._run_async(ws_url, voice_token, voice_name, pitch_shift, sample_rate))

    async def _run_async(self, ws_url: str, voice_token: str, voice_name: str,
                         pitch_shift: int, sample_rate: int) -> None:
        import asyncio
        import json

        import websockets

        try:
            async with websockets.connect(ws_url, open_timeout=60, max_size=None) as ws:
                await ws.send(json.dumps({
                    "token": voice_token, "voice": voice_name,
                    "sample_rate": sample_rate, "pitch_shift": pitch_shift,
                }))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
                if msg.get("type") != "ready":
                    self._error = f"cloud voice: unexpected response {msg}"
                    return
                log.info("Cloud voice connected (providers=%s)", msg.get("providers"))
                self._connected.set()

                sender = asyncio.create_task(self._sender(ws))
                receiver = asyncio.create_task(self._receiver(ws))
                done, pending = await asyncio.wait(
                    [sender, receiver], return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    exc = t.exception()
                    if exc is not None and not self._stop_evt.is_set():
                        self._error = f"cloud voice session ended: {exc}"
                        log.warning("Cloud voice session ended: %s", exc)
        except Exception as exc:
            self._error = f"cloud voice connection failed: {exc}"
            log.warning("Cloud voice session ended: %s", exc)
        finally:
            self._connected.clear()

    async def _sender(self, ws) -> None:
        import asyncio
        while not self._stop_evt.is_set():
            try:
                samples, _sample_rate = self._in_q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            # Raw device-rate bytes — see the module docstring on why this is
            # NOT resampled to SR (16k) here; the server does that per-window.
            await ws.send(samples.astype(np.float32).tobytes())

    async def _receiver(self, ws) -> None:
        import asyncio
        while not self._stop_evt.is_set():
            # A plain `await ws.recv()` blocks until the SERVER sends
            # something, which can be arbitrarily long once the mic goes
            # quiet — that starved stop() of any chance to notice
            # _stop_evt, leaving the WebSocket open (and the Modal GPU
            # session billing) until Modal's own idle timeout eventually
            # killed it from the other end. Poll instead, so a stop()
            # is noticed within ~1s and the `async with` block above
            # actually sends a close frame right away.
            try:
                data = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            out = np.frombuffer(data, dtype=np.float32)
            try:
                self._out_q.put_nowait(out)
            except queue.Full:
                try:
                    self._out_q.get_nowait()   # drop oldest, keep latency bounded
                except queue.Empty:
                    pass
                try:
                    self._out_q.put_nowait(out)
                except queue.Full:
                    pass


# Singleton, same pattern as rvc.py's `rvc`.
cloud_rvc = CloudVoiceEngine()
