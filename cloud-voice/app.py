"""Apex Pro — cloud real-time voice cloning, on Modal.

Why this exists: fal has no true realtime voice-CONVERSION API (checked
thoroughly — only TTS, batch conversion, or a conversational agent). Local
RVC is genuinely real-time but needs a GPU the customer may not have. This
runs the same RVC pipeline (content encoder -> pitch extractor -> voice
model) on a Modal GPU instead, reached over a WebSocket, so it works from
any laptop regardless of local hardware — the same "cloud does the heavy
AI, customer streams to/from it" shape Lucy already uses for video.

Pipeline (identical math to backend/app/engines/voice/rvc.py's proven,
locally-verified _convert(), just running on Modal's GPU instead of the
customer's machine):
  mic audio -> ContentVec content features -> RMVPE pitch (F0)
            -> RVC voice model (net_g) -> converted waveform

Deploy:  modal deploy cloud-voice/app.py
Models live in the "apexcam-voice-models" Modal Volume (uploaded once via
`modal volume put`) — content_vec.onnx and rmvpe.onnx are the shared,
voice-independent base models; each target voice is its own .onnx alongside
them, selected by name per session.

SECURITY: this endpoint is reachable directly (Modal gives it a public URL),
gated by a short-lived HMAC-signed token minted server-side by apexcam-api
on Render (server/app/security.py's make_voice_token()) — same broker
pattern as the fal/Decart Lucy handshake elsewhere in this app. Verified
independently here (shared secret via a Modal Secret, `apexcam-voice-secret`
/ env `APEXCAM_VOICE_SECRET`) with NO callback to the Render server, so
there's zero added latency on the hot path. A token is checked as the very
first thing after ws.accept(), before any model loading — an invalid/missing/
expired token is rejected before it can cost a cent of GPU time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import modal

app = modal.App("apexcam-voice")


# --- token verification (mirrors server/app/security.py's make_voice_token,
# a SEPARATE signing secret from the main 30-day session token — narrow
# scope, short TTL, so a leaked token costs at most 5 minutes of GPU time on
# one session, not account access). Duplicated here rather than imported
# since this container doesn't have the server's codebase — deliberately
# tiny and dependency-free (stdlib only), same reasoning as security.py
# itself. ----------------------------------------------------------------
def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify_voice_token(token: str) -> int | None:
    """Return the user id if the token is valid, unexpired, and scoped to
    cloud_voice — else None."""
    secret = os.environ.get("APEXCAM_VOICE_SECRET", "").encode()
    if not secret:
        return None   # misconfigured deploy — fail closed, never open
    try:
        payload, sig = token.split(".")
        expected = base64.urlsafe_b64encode(
            hmac.new(secret, payload.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64d(payload))
        if data.get("scope") != "cloud_voice":
            return None
        if data.get("exp", 0) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None

# debian_slim only ships the NVIDIA driver + CUDA Driver API (confirmed via
# Modal's own docs) — not the CUDA 12.x + cuDNN 9.x RUNTIME libraries
# (cudart/cublas/cudnn .so files) that onnxruntime-gpu's CUDAExecutionProvider
# dynamically loads at session-creation time. Without them, CUDAExecutionProvider
# silently fails to load and onnxruntime falls back to CPU — this was the
# other blocker alongside the RMVPE mel-spectrogram bug. `onnxruntime-gpu==
# 1.19.2` installed plain from PyPI resolves to the CUDA 12.x build (the
# default since 1.19.0, confirmed against onnxruntime's own install docs),
# so the base image needs to match: CUDA 12.x + cuDNN 9.x runtime, verified
# against a real, currently-published nvidia/cuda tag on Docker Hub.
image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
    .pip_install(
        "onnxruntime-gpu==1.19.2",
        "numpy<2",
        "fastapi",
    )
)

MODELS_VOLUME = modal.Volume.from_name("apexcam-voice-models")
MODELS_DIR = "/models"

SR = 16000  # content encoder sample rate — matches rvc.py exactly

# --- RMVPE mel-spectrogram front end (ported from backend/app/engines/voice/
# rvc.py, verified there against a real 220 Hz test tone: decoded median F0
# 220.1 Hz) — rmvpe.onnx's real input is a (1, 128, T) log-mel spectrogram,
# not raw audio, and its output is a 360-class pitch-salience distribution
# needing _to_local_average_cents to decode, not F0 directly. Both were
# missing from this file originally (the same bug as the local engine had),
# with the resulting ONNX shape error silently caught and treated as "no
# pitch" on every call. See rvc.py for the full derivation/verification.
_MEL_SR = 16000
_MEL_N_FFT = 1024
_MEL_WIN = 1024
_MEL_HOP = 160
_MEL_NMELS = 128
_MEL_FMIN = 30.0
_MEL_FMAX = 8000.0
_MEL_CLAMP = 1e-5
_RMVPE_PAD_TO = 32


def _hz_to_mel_htk(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz_htk(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _build_mel_filterbank() -> np.ndarray:
    n_freqs = _MEL_N_FFT // 2 + 1
    fft_freqs = np.linspace(0, _MEL_SR / 2, n_freqs)
    mel_pts = np.linspace(_hz_to_mel_htk(_MEL_FMIN), _hz_to_mel_htk(_MEL_FMAX), _MEL_NMELS + 2)
    hz_pts = _mel_to_hz_htk(mel_pts)
    fb = np.zeros((_MEL_NMELS, n_freqs), dtype=np.float64)
    for i in range(_MEL_NMELS):
        lo, center, hi = hz_pts[i], hz_pts[i + 1], hz_pts[i + 2]
        left = (fft_freqs - lo) / (center - lo)
        right = (hi - fft_freqs) / (hi - center)
        fb[i] = np.maximum(0, np.minimum(left, right))
    enorm = 2.0 / (hz_pts[2:_MEL_NMELS + 2] - hz_pts[:_MEL_NMELS])
    fb *= enorm[:, None]
    return fb.astype(np.float32)


def _periodic_hann(n: int) -> np.ndarray:
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


_MEL_BASIS = _build_mel_filterbank()
_MEL_WINDOW = _periodic_hann(_MEL_WIN)


def _mel_spectrogram(audio16: np.ndarray) -> np.ndarray:
    pad = _MEL_N_FFT // 2
    if audio16.size <= pad:
        audio16 = np.pad(audio16, (0, pad + 1 - audio16.size))
    padded = np.pad(audio16, pad, mode="reflect")
    n_frames = 1 + (len(padded) - _MEL_N_FFT) // _MEL_HOP
    if n_frames < 1:
        return np.zeros((_MEL_NMELS, 0), np.float32)
    frames = np.stack([padded[i * _MEL_HOP: i * _MEL_HOP + _MEL_N_FFT] for i in range(n_frames)])
    windowed = frames * _MEL_WINDOW[None, :]
    spec = np.fft.rfft(windowed, n=_MEL_N_FFT, axis=1)
    magnitude = np.abs(spec)
    mel = _MEL_BASIS @ magnitude.T
    return np.log(np.clip(mel, _MEL_CLAMP, None)).astype(np.float32)


_CENTS_MAPPING = np.pad(20 * np.arange(360) + 1997.3794084376191, (4, 4))


def _to_local_average_cents(salience: np.ndarray, thred: float = 0.03) -> np.ndarray:
    center = np.argmax(salience, axis=1)
    sal = np.pad(salience, ((0, 0), (4, 4)))
    center = center + 4
    starts = center - 4
    ends = center + 5
    todo_sal = np.array([sal[idx, starts[idx]:ends[idx]] for idx in range(sal.shape[0])])
    todo_cents = np.array([_CENTS_MAPPING[starts[idx]:ends[idx]] for idx in range(sal.shape[0])])
    weight_sum = np.sum(todo_sal, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        devided = np.sum(todo_sal * todo_cents, 1) / weight_sum
    devided = np.nan_to_num(devided)
    maxx = np.max(salience, axis=1)
    devided[maxx <= thred] = 0
    return devided


class VoiceSession:
    """One customer's live conversion state — loaded once per voice, reused
    across every audio chunk in the session. Mirrors RVCVoiceEngine's
    _convert() in backend/app/engines/voice/rvc.py exactly; same math,
    verified there against real audio."""

    def __init__(self, voice_name: str, pitch_shift: int = 0) -> None:
        import onnxruntime as ort

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.content = ort.InferenceSession(f"{MODELS_DIR}/content_vec.onnx", providers=providers)
        self.pitch_model = ort.InferenceSession(f"{MODELS_DIR}/rmvpe.onnx", providers=providers)
        voice_path = f"{MODELS_DIR}/voices/{voice_name}.onnx"
        so = ort.SessionOptions()
        so.log_severity_level = 4
        self.voice = ort.InferenceSession(voice_path, sess_options=so, providers=providers)
        self.pitch_shift = pitch_shift
        self.model_sr = 40000  # RVC v2 default net_g output rate
        self._win_T = self._probe_length()
        # convert_chunk always runs a FULL win_T-frame pass through net_g
        # regardless of how much audio it's given — feeding it directly with
        # small streaming frames (as the raw WebSocket protocol receives them)
        # returns a fixed ~1s of audio per call regardless of input size, the
        # same class of bug the local engine's separate convert() buffering
        # wrapper exists to prevent. Ported that exact logic here.
        self._in_buf = np.zeros(0, np.float32)
        self._out_buf = np.zeros(0, np.float32)

    def _probe_length(self) -> int:
        ins = self.voice.get_inputs()

        def works(t: int) -> bool:
            try:
                self.voice.run(None, {
                    ins[0].name: np.zeros((1, t, 768), np.float32),
                    ins[1].name: np.array([t], np.int64),
                    ins[2].name: np.ones((1, t), np.int64),
                    ins[3].name: np.zeros((1, t), np.float32),
                    ins[4].name: np.array([0], np.int64),
                    ins[5].name: np.zeros((1, 192, t), np.float32),
                })
                return True
            except Exception:
                return False

        candidates = [100, 128, 200, 256, 300, 320, 400, 512]
        ok = [t for t in candidates if works(t)]
        if len(ok) >= 2:
            return 100
        if len(ok) == 1:
            return ok[0]
        return 200

    @staticmethod
    def _fit_seq(feats: np.ndarray, n: int) -> np.ndarray:
        t = feats.shape[1]
        if t == n:
            return feats
        if t > n:
            return feats[:, :n, :]
        pad = np.repeat(feats[:, -1:, :], n - t, axis=1)
        return np.concatenate([feats, pad], axis=1)

    def _extract_pitch(self, audio16: np.ndarray, n: int):
        try:
            mel = _mel_spectrogram(audio16)
            t = mel.shape[1]
            pad = _RMVPE_PAD_TO * ((t - 1) // _RMVPE_PAD_TO + 1) - t if t > 0 else 0
            mel_in = np.pad(mel, ((0, 0), (0, pad)), mode="constant") if pad > 0 else mel
            inp = self.pitch_model.get_inputs()[0].name
            hidden = self.pitch_model.run(None, {inp: mel_in[None, :, :].astype(np.float32)})[0]
            salience = hidden[:, :t].reshape(-1, hidden.shape[-1])
            f0 = _to_local_average_cents(salience)
            f0 = 10 * (2 ** (f0 / 1200))
            f0[f0 == 10] = 0
        except Exception:
            f0 = np.zeros(n, np.float32)
        if len(f0) != n:
            f0 = (np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(f0)), f0)
                  if len(f0) else np.zeros(n, np.float32))
        f0 = f0 * (2 ** (self.pitch_shift / 12.0))
        f0_mel_min = 1127 * np.log(1 + 50 / 700)
        f0_mel_max = 1127 * np.log(1 + 1100 / 700)
        f0_mel = 1127 * np.log(1 + f0 / 700)
        f0_mel = np.where(f0_mel > 0,
                          (f0_mel - f0_mel_min) * 254 / (f0_mel_max - f0_mel_min) + 1,
                          f0_mel)
        pitch = np.rint(np.clip(f0_mel, 1, 255)).astype(np.int64)
        return pitch[None, :], f0[None, :].astype(np.float32)

    def convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Streaming entry point — buffers arbitrary-sized incoming WebSocket
        frames into model-sized windows, calls convert_chunk once per full
        window, and emits output in sync with what was actually sent so the
        client always gets back exactly as many samples as it sent (matching
        real-time playback). Passes real audio through unconverted while the
        very first window is still filling (priming), same as the local
        engine — never silence, never raises into the WS loop."""
        win_T = self._win_T or 100
        win = max(1, int(win_T * 160 * sample_rate / SR))
        self._in_buf = np.concatenate([self._in_buf, samples.astype(np.float32)])
        while len(self._in_buf) >= win:
            chunk = self._in_buf[:win]
            self._in_buf = self._in_buf[win:]
            conv = self.convert_chunk(chunk, sample_rate)
            self._out_buf = np.concatenate([self._out_buf, conv])
        if len(self._out_buf) >= len(samples):
            out = self._out_buf[:len(samples)]
            self._out_buf = self._out_buf[len(samples):]
            return out.astype(np.float32)
        return samples

    def convert_chunk(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Convert one FULL model-window chunk of mono float32 audio at
        `sample_rate` Hz. Called by convert() once per buffered window —
        don't call directly from the WebSocket loop with raw small frames,
        it always emits win_T frames worth of audio regardless of input size."""
        audio16 = self._resample(samples, sample_rate, SR)
        if audio16.size < 400:
            return samples
        ci = self.content.get_inputs()[0].name
        logits = self.content.run(None, {ci: audio16[None, None, :].astype(np.float32)})[0]
        feats = np.repeat(logits, 2, axis=1).astype(np.float32)
        n = self._win_T
        feats = self._fit_seq(feats, n)
        pitch, pitchf = self._extract_pitch(audio16, n)

        ins = self.voice.get_inputs()
        feed = {
            ins[0].name: feats,
            ins[1].name: np.array([n], dtype=np.int64),
            ins[2].name: pitch,
            ins[3].name: pitchf,
            ins[4].name: np.array([0], dtype=np.int64),
            ins[5].name: np.random.randn(1, 192, n).astype(np.float32),
        }
        out = self.voice.run(None, feed)[0].squeeze().astype(np.float32)
        return self._resample(out, self.model_sr, sample_rate).astype(np.float32)

    @staticmethod
    def _resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
        if sr_from == sr_to or x.size == 0:
            return x
        n = int(round(len(x) * sr_to / sr_from))
        if n <= 0:
            return x
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


@app.cls(image=image, gpu="L4", volumes={MODELS_DIR: MODELS_VOLUME},
        secrets=[modal.Secret.from_name("apexcam-voice-secret")])
# REQUIRED for WebSockets on Modal: without a class + this decorator, Modal
# treats the whole connection as a single "input" and mishandles the
# long-lived accept/send/receive lifecycle a WebSocket actually needs (a
# DIFFERENT 500-on-handshake bug than the type-hint one documented on web()
# below — every official Modal WebSocket example uses @app.cls, never a bare
# @app.function). max_inputs caps concurrent live sessions sharing one GPU
# container — modest on purpose since each session holds real GPU work, not
# just I/O.
@modal.concurrent(max_inputs=4)
class VoiceServer:
    @modal.asgi_app()
    def web(self):
        # FastAPI/WebSocket are imported at MODULE level (top of file), not
        # here — this file has `from __future__ import annotations`, which
        # turns every type hint (including ws_convert's `ws: WebSocket`) into
        # a plain string, resolved later via the function's __globals__. A
        # LOCAL import here would never land in __globals__, so FastAPI can't
        # resolve "WebSocket" when it introspects the websocket route — this
        # was the actual root cause of a 500 on every handshake, silently
        # surfacing through Modal's own ASGI bridge as a malformed
        # websocket.close (code/reason coming back None) instead of a clear
        # NameError. echo_test.py never hit this because it has no `from
        # __future__ import annotations`, so Python resolves its annotations
        # eagerly, using the local scope at definition time.
        web_app = FastAPI()

        @web_app.get("/health")
        def health():
            return {"status": "ok"}

        @web_app.websocket("/ws")
        async def ws_convert(ws: WebSocket):
            """Protocol: first text message is JSON {"token": "<from apexcam-api>",
            "voice": "<name>", "sample_rate": 48000, "pitch_shift": 0}. Connection is
            rejected (code 4401) if the token is missing, invalid, expired, or wrong
            scope. After that, binary frames are raw float32 PCM mono chunks in,
            converted float32 PCM mono chunks out — one frame per frame, in order."""
            import asyncio
            import time

            await ws.accept()
            print("WS accepted", flush=True)
            session: VoiceSession | None = None
            sample_rate = 48000
            try:
                cfg = await ws.receive_json()
                print("got cfg:", {k: v for k, v in cfg.items() if k != "token"}, flush=True)
                uid = _verify_voice_token(cfg.get("token", ""))
                if uid is None:
                    print("REJECTED: invalid/missing/expired token", flush=True)
                    await ws.close(code=4401, reason="invalid or expired token")
                    return
                sample_rate = int(cfg.get("sample_rate", 48000))
                # Loading 3 large ONNX models is blocking, synchronous work —
                # run it off the event loop so the loop stays responsive to a
                # client disconnect during the (potentially many-second) load
                # instead of blocking Modal's whole connection lifecycle.
                t0 = time.time()
                session = await asyncio.to_thread(
                    VoiceSession, cfg["voice"], int(cfg.get("pitch_shift", 0)))
                print(f"VoiceSession built in {time.time()-t0:.1f}s", flush=True)
                await ws.send_json({"type": "ready", "providers": session.voice.get_providers()})
                print("sent ready", flush=True)
                while True:
                    data = await ws.receive_bytes()
                    samples = np.frombuffer(data, dtype=np.float32)
                    out = await asyncio.to_thread(session.convert, samples, sample_rate)
                    await ws.send_bytes(out.tobytes())
            except WebSocketDisconnect:
                print("client disconnected", flush=True)
            except Exception as exc:
                print(f"HANDLER EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
                try:
                    await ws.close(code=1011, reason=str(exc)[:120])
                except Exception as close_exc:
                    print(f"close failed too: {close_exc}", flush=True)

        return web_app
