"""Voice engine implementations (Phase 8+): real-time cloning/conversion, noise
reduction, echo cancellation, pitch/emotion/accent handling. Backed by OpenVoice
/ XTTS / Fish Speech.

Phase 1 ships a pass-through.
"""
from __future__ import annotations

import numpy as np

from app.engines.base import AudioEngine


class PassthroughVoiceEngine(AudioEngine):
    """Returns audio unchanged. Placeholder until real models land."""

    def load(self) -> None:
        self._loaded = True

    def process(self, samples: np.ndarray, *, sample_rate: int) -> np.ndarray:
        return samples
