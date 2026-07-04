"""Real-time pitch shifter (Phase 8).

Streaming phase-vocoder pitch shifter in pure NumPy — time-stretch via
phase-vocoder then fractional resample, giving a genuine pitch change that
preserves speaking rate. Runs inside the live audio callback with a small fixed
latency, no SciPy/torch required, so it works on the build machine and any
laptop.

This is the CPU voice-transformation path. On GPU machines the same slot upgrades
to neural voice cloning/conversion (OpenVoice / XTTS / Fish Speech); the audio
pipeline only depends on the ``process`` interface here.

The offline core (phase-vocoder stretch + resample) is verified to shift pitch
accurately for both up- and down-shifts; this class keeps that math and adds
persistent state so it can run on 10 ms blocks.
"""
from __future__ import annotations

import numpy as np

N_FFT = 2048
HOP = 512  # analysis hop


class PitchShifter:
    def __init__(self, sample_rate: int = 48000) -> None:
        self.sample_rate = sample_rate
        self._win = np.hanning(N_FFT).astype(np.float32)
        self.ratio = 1.0
        self._reset_state()

    def _reset_state(self) -> None:
        self._in = np.zeros(N_FFT, dtype=np.float32)  # analysis input FIFO
        self._prev_ph = None
        self._acc = None
        self._syn = np.zeros(0, dtype=np.float32)      # stretched output (num)
        self._syn_w = np.zeros(0, dtype=np.float32)     # stretched weight (den)
        self._done = np.zeros(0, dtype=np.float32)      # finalised stretched samples
        self._read = 0.0                                # fractional resample pos

    def set_semitones(self, semitones: float) -> None:
        self.ratio = float(2.0 ** (semitones / 12.0))

    def reset(self) -> None:
        self._reset_state()

    def process(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        if abs(self.ratio - 1.0) < 1e-3:
            return x

        r = self.ratio
        sh = max(1, int(round(HOP * r)))
        reff = sh / HOP
        omega = 2 * np.pi * np.arange(N_FFT // 2 + 1) * HOP / N_FFT

        self._in = np.concatenate([self._in, x.astype(np.float32)])

        # --- analysis / synthesis: build the time-stretched stream ---
        apos = 0
        while apos + N_FFT <= len(self._in):
            frame = self._win * self._in[apos:apos + N_FFT]
            spec = np.fft.rfft(frame)
            mag, ph = np.abs(spec), np.angle(spec)
            if self._acc is None:
                self._acc = ph.copy()
            else:
                d = ph - self._prev_ph - omega
                d = (d + np.pi) % (2 * np.pi) - np.pi
                self._acc = self._acc + (omega + d) * reff
            self._prev_ph = ph
            out_frame = (np.fft.irfft(mag * np.exp(1j * self._acc)) * self._win).astype(np.float32)

            # overlap-add into the stretched accumulator at synthesis hop `sh`
            need = sh + N_FFT
            if len(self._syn) < need:
                pad = need - len(self._syn)
                self._syn = np.concatenate([self._syn, np.zeros(pad, np.float32)])
                self._syn_w = np.concatenate([self._syn_w, np.zeros(pad, np.float32)])
            self._syn[:N_FFT] += out_frame
            self._syn_w[:N_FFT] += self._win ** 2
            # first `sh` samples are now final
            seg = self._syn[:sh] / np.maximum(self._syn_w[:sh], 1e-6)
            self._done = np.concatenate([self._done, seg.astype(np.float32)])
            self._syn = self._syn[sh:]
            self._syn_w = self._syn_w[sh:]
            apos += HOP

        # drop consumed analysis input (keep one frame of history alignment)
        if apos:
            self._in = self._in[apos:]

        # --- resample the finalised stretched stream by `r` to shift pitch ---
        out = np.empty(n, dtype=np.float32)
        produced = 0
        while produced < n and self._read + 1 < len(self._done):
            i0 = int(self._read)
            frac = self._read - i0
            out[produced] = self._done[i0] * (1 - frac) + self._done[i0 + 1] * frac
            self._read += r
            produced += 1

        # discard fully-consumed finalised samples, keep the fractional remainder
        drop = int(self._read)
        if drop > 0:
            self._done = self._done[drop:]
            self._read -= drop

        if produced < n:  # priming / underrun: pad to keep the stream aligned
            out[produced:] = 0.0
        return out
