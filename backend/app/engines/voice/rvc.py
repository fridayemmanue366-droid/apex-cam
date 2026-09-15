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
from app.core.onnx_providers import get_providers as _providers

log = get_logger(__name__)

RVC_DIR = Path("models") / "rvc"
CONTENT_MODEL = RVC_DIR / "content_vec.onnx"
PITCH_MODEL = RVC_DIR / "rmvpe.onnx"
VOICES_DIR = RVC_DIR / "voices"

# --- RMVPE mel-spectrogram front end ----------------------------------------
# rmvpe.onnx does NOT take raw audio — its real input is a (1, 128, T) log-mel
# spectrogram (confirmed via the model's own input metadata: shape [1, 128,
# 'time']). Feeding raw audio throws a hard ONNX shape error, which used to be
# silently swallowed by the bare except below, so pitch extraction was
# returning all-zero F0 on every single call. Exact params below are ported
# verbatim from the reference implementation (RVC-Project's infer/rmvpe.py,
# MelSpectrogram + RMVPE class) — cross-checked numerically against librosa
# (max abs diff ~1.6e-3 in log-mel space) and end-to-end against this exact
# rmvpe.onnx file with a 220 Hz test tone (decoded median F0: 220.1 Hz).
_MEL_SR = 16000
_MEL_N_FFT = 1024
_MEL_WIN = 1024
_MEL_HOP = 160
_MEL_NMELS = 128
_MEL_FMIN = 30.0
_MEL_FMAX = 8000.0
_MEL_CLAMP = 1e-5
_RMVPE_PAD_TO = 32   # RMVPE's U-Net needs the time axis padded to a multiple of this


def _hz_to_mel_htk(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz_htk(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _build_mel_filterbank() -> np.ndarray:
    """HTK-scale mel filterbank with Slaney-style area normalization — matches
    librosa.filters.mel(sr=16000, n_fft=1024, n_mels=128, fmin=30, fmax=8000,
    htk=True) to within float32 precision, without depending on librosa."""
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
    # torch.hann_window's default periodic=True window — NOT np.hanning (which
    # is the symmetric variant and gives a slightly different STFT).
    return (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)).astype(np.float32)


_MEL_BASIS = _build_mel_filterbank()
_MEL_WINDOW = _periodic_hann(_MEL_WIN)


def _mel_spectrogram(audio16: np.ndarray) -> np.ndarray:
    """16 kHz mono float32 -> log-mel spectrogram (128, T), matching RMVPE's
    training-time front end exactly (centered/reflect-padded STFT, periodic
    Hann window, HTK+Slaney mel filterbank, log-clamped)."""
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


# --- RMVPE 360-class pitch-salience decode -----------------------------------
# rmvpe.onnx outputs (1, T, 360) per-frame salience over 360 pitch bins (20
# cents apart), NOT F0 directly — the old code reshaped this straight into an
# "f0" array, which is meaningless. to_local_average_cents is RMVPE's own
# decode: a salience-weighted average of the 9 bins around the peak, ported
# verbatim from infer/rmvpe.py.
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


def base_models_present() -> bool:
    return CONTENT_MODEL.exists() and PITCH_MODEL.exists()


def list_voices() -> list[str]:
    if not VOICES_DIR.exists():
        return []
    return sorted(p.stem for p in VOICES_DIR.glob("*.onnx"))


def rvc_available() -> bool:
    """True only when the shared models AND at least one voice model exist."""
    return base_models_present() and len(list_voices()) > 0


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
        # Some voice-model ONNX exports need a FIXED phone length; others are
        # dynamic. We probe it at load and buffer live audio into windows of that
        # length so conversion actually runs on the streaming pipeline. Buffers
        # live at the device sample rate.
        self._win_T: int | None = None
        self._in_buf = np.zeros(0, np.float32)
        self._out_buf = np.zeros(0, np.float32)

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
            # Quiet the session: probing tries several lengths and a fixed-length
            # export logs (expected) reshape errors we catch — don't spam them.
            so = ort.SessionOptions()
            so.log_severity_level = 4   # fatal only
            self._voice = ort.InferenceSession(
                str(path), sess_options=so, providers=_providers())
            self.voice_name = name
            self._in_buf = np.zeros(0, np.float32)
            self._out_buf = np.zeros(0, np.float32)
            self._probe_length()
            log.info("RVC voice model loaded: %s (window T=%s)", name, self._win_T)
            return True
        except Exception as exc:
            log.warning("RVC voice model failed: %s", exc)
            self._voice = None
            return False

    def _probe_length(self) -> None:
        """Find the phone length the voice model accepts. Fixed-length exports
        work at only one T; dynamic ones work at many (we then pick a ~1s window
        for low latency). Falls back to a default if probing is inconclusive."""
        self._win_T = None
        if self._voice is None:
            return
        ins = self._voice.get_inputs()

        def works(T: int) -> bool:
            try:
                self._voice.run(None, {
                    ins[0].name: np.zeros((1, T, 768), np.float32),
                    ins[1].name: np.array([T], np.int64),
                    ins[2].name: np.ones((1, T), np.int64),
                    ins[3].name: np.zeros((1, T), np.float32),
                    ins[4].name: np.array([0], np.int64),
                    ins[5].name: np.zeros((1, 192, T), np.float32),
                })
                return True
            except Exception:
                return False

        candidates = [100, 128, 200, 256, 300, 320, 400, 512]
        ok = [T for T in candidates if works(T)]
        if len(ok) >= 2:
            self._win_T = 100          # dynamic -> ~1s window (low latency)
        elif len(ok) == 1:
            self._win_T = ok[0]        # fixed-length export
        else:
            self._win_T = 200          # inconclusive -> common default

    def convert(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Convert a mono float32 chunk to the target voice. Buffers live audio
        into model-sized windows (voice models can't run on tiny 10 ms blocks),
        emits the converted stream in sync, and passes the original voice through
        while priming. Safe no-op if not ready; never raises into the audio loop."""
        if not self.enabled or not self.ready:
            return samples
        try:
            win_T = self._win_T or 100
            # Device-SR samples that yield ~win_T phone frames (content hop 320 @
            # 16k, x2 upsample -> win_T*160 samples @16k).
            win = max(1, int(win_T * 160 * sample_rate / self.SR))
            self._in_buf = np.concatenate([self._in_buf, samples.astype(np.float32)])
            while len(self._in_buf) >= win:
                chunk = self._in_buf[:win]
                self._in_buf = self._in_buf[win:]
                conv = self._convert(chunk, sample_rate)
                self._out_buf = np.concatenate([self._out_buf, conv])
            if len(self._out_buf) >= len(samples):
                out = self._out_buf[:len(samples)]
                self._out_buf = self._out_buf[len(samples):]
                return out.astype(np.float32)
            return samples   # priming: pass the real voice through until ready
        except Exception as exc:
            log.debug("RVC convert skipped: %s", exc)
            return samples

    @staticmethod
    def _fit_seq(feats: np.ndarray, n: int) -> np.ndarray:
        """Force [1, T, C] to exactly n frames (trim, or edge-pad the last frame).
        Fixed-length exports need an exact phone length."""
        t = feats.shape[1]
        if t == n:
            return feats
        if t > n:
            return feats[:, :n, :]
        pad = np.repeat(feats[:, -1:, :], n - t, axis=1)
        return np.concatenate([feats, pad], axis=1)

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
        # Force the exact phone length the model expects (fixed-length exports).
        n = self._win_T or feats.shape[1]
        feats = self._fit_seq(feats, n)
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
        Coarse-pitch mel mapping uses the standard RVC 50-1100 Hz range. Guarded.

        RMVPE's real input is a (1, 128, T) log-mel spectrogram, not raw audio
        (see _mel_spectrogram's docstring) — and its output is a 360-class
        pitch-salience distribution per frame, not F0 directly, so it needs
        _to_local_average_cents to decode. Both steps were missing before;
        the ONNX shape error that raw audio threw was being silently caught
        below and treated as "no pitch detected" on every single call."""
        try:
            mel = _mel_spectrogram(audio16)
            t = mel.shape[1]
            pad = _RMVPE_PAD_TO * ((t - 1) // _RMVPE_PAD_TO + 1) - t if t > 0 else 0
            mel_in = np.pad(mel, ((0, 0), (0, pad)), mode="constant") if pad > 0 else mel
            inp = self._pitch.get_inputs()[0].name
            hidden = self._pitch.run(None, {inp: mel_in[None, :, :].astype(np.float32)})[0]
            salience = hidden[:, :t].reshape(-1, hidden.shape[-1])
            f0 = _to_local_average_cents(salience)
            f0 = 10 * (2 ** (f0 / 1200))
            f0[f0 == 10] = 0
        except Exception as exc:
            log.debug("RMVPE pitch extraction failed, using silence: %s", exc)
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
