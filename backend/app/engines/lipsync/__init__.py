"""Lip-sync engine (Phase 9): drives mouth animation from audio, backed by
Wav2Lip. Phase 1 ships a pass-through frame engine.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.engines.base import FrameEngine


class PassthroughLipSyncEngine(FrameEngine):
    kind = "lipsync"

    def load(self) -> None:
        self._loaded = True

    def process(self, frame: np.ndarray, *, ctx: dict[str, Any] | None = None) -> np.ndarray:
        return frame
