"""RVC voice conversion framework (ONNX) — clone your voice into a target voice.

RVC (Retrieval-based Voice Conversion) makes you sound like a target speaker in
real time. Unlike pitch shifting, it changes the *timbre/identity* of the voice.

Design (activates automatically when the models are present):
  input mic audio -> ContentVec content features -> RMVPE pitch (F0)
                  -> RVC voice model (net_g) -> converted waveform

Models live in models/rvc/:
  content_vec.onnx           # speaker-independent content encoder (shared)
  rmvpe.onnx                 # pitch (F0) extractor (shared)
  voices/<name>.onnx         # one model PER target voice (user-supplied/trained)

IMPORTANT — honest notes:
  * A voice model is required *per voice* (you train or source each one). This
    engine provides the runtime; the voice models are content you add.
  * Real-time RVC needs a GPU (~300-500 ms latency). On CPU it is not real-time.
  * Runs on onnxruntime (GPU via onnxruntime-gpu). Gracefully no-ops until the
    models are installed, so the app is unaffected without them.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

RVC_DIR = Path("models") / "rvc"
CONTENT_MODEL = RVC_DIR / "content_vec.onnx"
PITCH_MODEL = RVC_DIR / "rmvpe.onnx"
VOICES_DIR = RVC_DIR / "voices"


def base_models_present() -> bool:
    return CONTENT_MODEL.exists() and PITCH_MODEL.exists()


def list_voices() -> list[str]:
    if not VOICES_DIR.exists():
        return []
    return sorted(p.stem for p in VOICES_DIR.glob("*.onnx"))


def rvc_available() -> bool:
    """True only when the shared models AND at least one voice model exist."""
    return base_models_present() and len(list_voices()) > 0


def _providers() -> list[str]:
    try:
        import onnxruntime as ort
        avail = ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]
    for gpu in ("CUDAExecutionProvider", "TensorrtExecutionProvider"):
        if gpu in avail:
            return [gpu, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


class RVCVoiceEngine:
    """Runtime for RVC voice models. Loads the shared content/pitch models plus a
    selected voice model, and converts mono float32 audio to the target voice."""

    SR = 16000  # content encoder sample rate

    def __init__(self) -> None:
        self._content = None
        self._pitch = None
        self._voice = None
        self.voice_name: str | None = None
        self.enabled = False
        self.pitch_shift = 0     # semitones applied to the target voice
        self.on_gpu = False

    @property
    def ready(self) -> bool:
        return self._content is not None and self._voice is not None

    def load(self) -> bool:
        if not base_models_present():
            return False
        try:
            import onnxruntime as ort
            p = _providers()
            self.on_gpu = p[0] != "CPUExecutionProvider"
            if self._content is None:
                self._content = ort.InferenceSession(str(CONTENT_MODEL), providers=p)
            if self._pitch is None:
                self._pitch = ort.InferenceSession(str(PITCH_MODEL), providers=p)
            log.info("RVC base models loaded (%s)", p[0])
            return True
        except Exception as exc:
            log.warning("RVC failed to load base models: %s", exc)
            return False

    def set_voice(self, name: str | None) -> bool:
        """Select a voice model by name (from models/rvc/voices/<name>.onnx)."""
        if not name:
            self._voice = None
            self.voice_name = None
            return False
        path = VOICES_DIR / f"{name}.onnx"
        if not path.exists():
            return False
        if not self.load():
            return False
        try:
            import onnxruntime as ort
            self._voice = ort.InferenceSession(str(path), providers=_providers())
            self.voice_name = name
            log.info("RVC voice model loaded: %s", name)
            return True
        except Exception as exc:
            log.warning("RVC voice model failed: %s", exc)
            self._voice = None
            return False

    def convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Convert a mono float32 chunk to the target voice. Returns the input
        unchanged if not ready (safe no-op)."""
        if not self.enabled or not self.ready:
            return samples
        try:
            return self._convert(samples, sample_rate)
        except Exception as exc:
            log.debug("RVC convert skipped: %s", exc)
            return samples

    def _convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        # NOTE: RVC voice-model ONNX I/O varies by export. This follows the common
        # rvc-python / w-okada layout; verify against your specific voice model on
        # a GPU. It is intentionally guarded so a mismatch no-ops rather than
        # corrupting audio.
        import onnxruntime  # noqa: F401

        audio16 = self._resample(samples, sample_rate, self.SR)
        feats = self._content.run(None, {self._content.get_inputs()[0].name:
                                          audio16[None, None, :].astype(np.float32)})[0]
        # feats: [1, T, 256/768]; upsample x2 (RVC convention)
        feats = np.repeat(feats, 2, axis=1)
        n = feats.shape[1]
        pitch, pitchf = self._extract_pitch(audio16, n)

        vin = {i.name: None for i in self._voice.get_inputs()}
        names = list(vin)
        # Best-effort mapping to the standard net_g inputs.
        feed = {}
        for name in names:
            ln = name.lower()
            if "phone" in ln or "feat" in ln or ln in ("c", "hubert"):
                feed[name] = feats.astype(np.float32)
            elif ln in ("phone_lengths", "feat_lengths", "length"):
                feed[name] = np.array([n], dtype=np.int64)
            elif "pitchf" in ln or "f0" in ln and "coarse" not in ln:
                feed[name] = pitchf.astype(np.float32)
            elif "pitch" in ln:
                feed[name] = pitch.astype(np.int64)
            elif ln in ("ds", "sid", "speaker"):
                feed[name] = np.array([0], dtype=np.int64)
            elif "rnd" in ln or "noise" in ln:
                shape = [d if isinstance(d, int) else 1 for d in self._voice.get_inputs()
                         [names.index(name)].shape]
                feed[name] = np.random.randn(*shape).astype(np.float32)
        if len(feed) < 2:  # couldn't map inputs -> don't risk it
            return samples
        out = self._voice.run(None, feed)[0].squeeze()
        target_pitch = float(2 ** (self.pitch_shift / 12.0))
        if abs(target_pitch - 1.0) > 1e-3:
            out = self._resample(out, int(self.SR * target_pitch), self.SR)
        return self._resample(out, self.SR, sample_rate).astype(np.float32)

    def _extract_pitch(self, audio16: np.ndarray, n: int):
        """RMVPE F0 -> (coarse pitch int64 [1,n], f0 float [1,n]). Guarded."""
        try:
            inp = self._pitch.get_inputs()[0].name
            f0 = self._pitch.run(None, {inp: audio16[None, :].astype(np.float32)})[0].reshape(-1)
        except Exception:
            f0 = np.zeros(n, np.float32)
        if len(f0) != n:
            f0 = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(f0)), f0) if len(f0) else np.zeros(n)
        f0 = f0 * (2 ** (self.pitch_shift / 12.0))
        f0_mel = 1127 * np.log(1 + f0 / 700)
        f0_mel = np.clip((f0_mel - 550) * 119 / (1147 - 550) + 1, 1, 255)
        pitch = np.rint(f0_mel).astype(np.int64)
        return pitch[None, :], f0[None, :]

    @staticmethod
    def _resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
        if sr_from == sr_to or x.size == 0:
            return x
        n = int(round(len(x) * sr_to / sr_from))
        if n <= 0:
            return x
        return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


# Singleton
rvc = RVCVoiceEngine()
