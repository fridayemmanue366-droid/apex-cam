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
        # net_g output sample rate — set at export time. RVC v2 is 40000 (some are
        # 48000). Overridable via APEXCAM_RVC_SR. Verify per model on a GPU.
        import os
        self.model_sr = int(os.environ.get("APEXCAM_RVC_SR", "40000"))

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
        # Matches the proven RVC ONNX inference contract (codename0og/RVC_Onnx_Infer):
        # standard net_g takes 6 POSITIONAL inputs in this order:
        #   hubert[1,2T,768] f32, hubert_length[1] i64, pitch[1,2T] i64,
        #   pitchf[1,2T] f32, ds(sid)[1] i64, rnd[1,192,2T] f32
        # and outputs a waveform at the model's SR. Guarded so a mismatch no-ops.
        import onnxruntime  # noqa: F401

        audio16 = self._resample(samples, sample_rate, self.SR)
        if audio16.size < 400:
            return samples
        # Content features -> [1, 2T, 768] (RVC v2 upsamples the encoder x2).
        ci = self._content.get_inputs()[0].name
        logits = self._content.run(
            None, {ci: audio16[None, None, :].astype(np.float32)})[0]
        feats = np.repeat(logits, 2, axis=1).astype(np.float32)   # [1, 2T, 768]
        n = feats.shape[1]
        pitch, pitchf = self._extract_pitch(audio16, n)

        ins = self._voice.get_inputs()
        if len(ins) < 6:   # not a standard net_g export -> don't risk corrupting audio
            return samples
        feed = {
            ins[0].name: feats,
            ins[1].name: np.array([n], dtype=np.int64),
            ins[2].name: pitch,
            ins[3].name: pitchf,
            ins[4].name: np.array([0], dtype=np.int64),
            ins[5].name: np.random.randn(1, 192, n).astype(np.float32),
        }
        out = self._voice.run(None, feed)[0].squeeze().astype(np.float32)
        # net_g outputs at the model's SR (e.g. 40 kHz) -> back to device rate.
        return self._resample(out, self.model_sr, sample_rate).astype(np.float32)

    def _extract_pitch(self, audio16: np.ndarray, n: int):
        """RMVPE F0 -> (coarse pitch int64 [1,n], continuous f0 float32 [1,n]).
        Coarse-pitch mel mapping uses the standard RVC 50-1100 Hz range. Guarded."""
        try:
            inp = self._pitch.get_inputs()[0].name
            f0 = self._pitch.run(None, {inp: audio16[None, :].astype(np.float32)})[0].reshape(-1)
        except Exception:
            f0 = np.zeros(n, np.float32)
        if len(f0) != n:
            f0 = (np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(f0)), f0)
                  if len(f0) else np.zeros(n, np.float32))
        f0 = f0 * (2 ** (self.pitch_shift / 12.0))          # apply user pitch shift
        f0_mel_min = 1127 * np.log(1 + 50 / 700)
        f0_mel_max = 1127 * np.log(1 + 1100 / 700)
        f0_mel = 1127 * np.log(1 + f0 / 700)
        f0_mel = np.where(f0_mel > 0,
                          (f0_mel - f0_mel_min) * 254 / (f0_mel_max - f0_mel_min) + 1,
                          f0_mel)
        pitch = np.rint(np.clip(f0_mel, 1, 255)).astype(np.int64)
        return pitch[None, :], f0[None, :].astype(np.float32)

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
