"""Real-time audio pipeline (Phase 5).

Captures the microphone, runs it through the voice engine chain (noise gate →
voice engine), and writes the processed audio to a virtual audio device
(VB-CABLE Input) so any app that lets you pick a microphone — Zoom, WhatsApp,
Telegram, Meet, Teams, Discord — can select Apex Cam's processed voice. Only the
processed audio is ever published; the raw mic is not.

Runs on a sounddevice callback (its own high-priority audio thread), so it stays
independent of the video pipeline and the HTTP server. Designed to degrade
gracefully: if the virtual device is missing, capture still runs for metering
and the UI shows how to install the driver.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from app.core.logging import get_logger
from app.engines.voice import PassthroughVoiceEngine
from app.engines.voice.pitch import PitchShifter

log = get_logger(__name__)

SAMPLE_RATE = 48000
BLOCK = 480  # 10 ms blocks — low latency
CABLE_HINT = (
    "Virtual audio device not found. Install VB-CABLE (free) from "
    "vb-audio.com/Cable, then pick 'CABLE Input' as the output here and "
    "'CABLE Output' as the microphone in your call app."
)


@dataclass
class AudioStats:
    running: bool = False
    level: float = 0.0          # 0..1 output RMS, for the UI meter
    input_device: int | None = None
    output_device: int | None = None
    output_name: str | None = None
    error: str | None = None


@dataclass
class VoiceParams:
    enabled: bool = True
    noise_reduction: bool = True
    gain: float = 1.0           # automatic-gain target multiplier
    pitch: float = 0.0          # semitones (applied by the voice engine, Phase 8)


@dataclass
class _Shared:
    lock: threading.Lock = field(default_factory=threading.Lock)
    stats: AudioStats = field(default_factory=AudioStats)


class AudioPipeline:
    def __init__(self) -> None:
        self._shared = _Shared()
        self._stream = None
        self.params = VoiceParams()
        self.voice_engine = PassthroughVoiceEngine("passthrough")
        self.voice_engine.load()
        self._noise_floor = 0.0
        self._pitch = PitchShifter(SAMPLE_RATE)

    # -- device discovery ---------------------------------------------------

    @staticmethod
    def list_devices() -> dict[str, list[dict]]:
        import sounddevice as sd

        inputs, outputs = [], []
        for i, d in enumerate(sd.query_devices()):
            entry = {"index": i, "name": d["name"]}
            if d["max_input_channels"] > 0:
                inputs.append(entry)
            if d["max_output_channels"] > 0:
                outputs.append(entry)
        return {"inputs": inputs, "outputs": outputs}

    @staticmethod
    def find_cable_output() -> int | None:
        """Locate the VB-CABLE input endpoint (what we write processed audio to;
        the app then reads 'CABLE Output' as its mic)."""
        import sounddevice as sd

        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and "cable input" in d["name"].lower():
                return i
        return None

    # -- lifecycle ----------------------------------------------------------

    def start(self, input_device: int | None = None, output_device: int | None = None) -> AudioStats:
        if self._stream is not None:
            return self.stats()
        import sounddevice as sd

        self._pitch.reset()
        if output_device is None:
            output_device = self.find_cable_output()

        out_name = None
        if output_device is not None:
            try:
                out_name = sd.query_devices(output_device)["name"]
            except Exception:
                out_name = None

        with self._shared.lock:
            self._shared.stats = AudioStats(
                running=True, input_device=input_device,
                output_device=output_device, output_name=out_name,
                error=None if output_device is not None else CABLE_HINT,
            )

        try:
            self._stream = sd.Stream(
                samplerate=SAMPLE_RATE, blocksize=BLOCK, dtype="float32",
                channels=1, device=(input_device, output_device),
                callback=self._callback, latency="low",
            )
            self._stream.start()
            log.info("Audio pipeline started (in=%s out=%s '%s')",
                     input_device, output_device, out_name)
        except Exception as exc:
            self._stream = None
            with self._shared.lock:
                self._shared.stats.running = False
                self._shared.stats.error = f"{exc}"
            log.error("Audio pipeline failed to start: %s", exc)
        return self.stats()

    def stop(self) -> AudioStats:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        with self._shared.lock:
            self._shared.stats.running = False
            self._shared.stats.level = 0.0
        return self.stats()

    def stats(self) -> AudioStats:
        with self._shared.lock:
            return AudioStats(**vars(self._shared.stats))

    # -- audio callback -----------------------------------------------------

    def _callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("audio status: %s", status)
        mono = indata[:, 0].copy()

        if self.params.enabled:
            mono = self._process(mono)

        outdata[:, 0] = mono
        rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
        with self._shared.lock:
            self._shared.stats.level = min(1.0, rms * 3)

    def _process(self, samples: np.ndarray) -> np.ndarray:
        if self.params.noise_reduction:
            samples = self._noise_gate(samples)
        # Voice cloning — either the cloud engine (Apex Pro, GPU on Modal) or
        # the local RVC engine (needs models installed). audio.py's routes
        # keep these mutually exclusive, same as Pro vs the local face swap.
        from app.engines.voice.cloud_rvc import cloud_rvc
        from app.engines.voice.rvc import rvc
        if cloud_rvc.enabled and cloud_rvc.ready:
            samples = cloud_rvc.convert(samples, SAMPLE_RATE)
        elif rvc.enabled and rvc.ready:
            samples = rvc.convert(samples, SAMPLE_RATE)
        # Real-time pitch/voice shift (deeper/higher, incl. gender-ish shifts).
        elif abs(self.params.pitch) > 0.05:
            self._pitch.set_semitones(self.params.pitch)
            samples = self._pitch.process(samples)
        # Automatic gain toward the configured target, softly clamped.
        samples = np.clip(samples * self.params.gain, -1.0, 1.0)
        # Neural voice cloning (OpenVoice/XTTS/Fish Speech) plugs in here on GPU
        # machines; today the engine is a pass-through after the pitch stage.
        return self.voice_engine.process(samples, sample_rate=SAMPLE_RATE)

    def _noise_gate(self, samples: np.ndarray) -> np.ndarray:
        """Simple adaptive noise gate: track the quiet floor and attenuate
        anything close to it. Cheap, real-time, no model required."""
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        self._noise_floor = 0.995 * self._noise_floor + 0.005 * rms
        threshold = self._noise_floor * 1.5
        if rms < threshold:
            return samples * 0.15
        return samples


class MicLevelMonitor:
    """Tiny input-only mic stream that only computes a loudness level. Used to
    drive lip-sync from the video pipeline without needing the full virtual-mic
    pipeline running. Independent and cheap; safe to fail (level stays 0)."""

    def __init__(self) -> None:
        self._stream = None
        self.level = 0.0

    def start(self, input_device: int | None = None) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        def cb(indata, frames, time_info, status) -> None:  # noqa: ANN001
            rms = float(np.sqrt(np.mean(indata[:, 0] ** 2))) if indata.size else 0.0
            self.level = min(1.0, rms * 3)

        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, blocksize=BLOCK, channels=1,
                dtype="float32", device=input_device, callback=cb, latency="low",
            )
            self._stream.start()
        except Exception as exc:
            log.debug("mic level monitor unavailable: %s", exc)
            self._stream = None

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self.level = 0.0


# Singletons used by the API routes / pipeline.
audio_pipeline = AudioPipeline()
mic_level_monitor = MicLevelMonitor()
