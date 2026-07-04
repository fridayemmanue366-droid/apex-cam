"""Face engine implementations (Phase 6+): detection, landmarks, pose, swapping,
reenactment, expression transfer. Backed by InsightFace / MediaPipe / YOLO and
enhanced by CodeFormer / GFPGAN.

Phase 1 ships a pass-through so the pipeline is wireable end to end.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from app.engines.base import FrameEngine


class PassthroughFaceEngine(FrameEngine):
    """Returns frames unchanged. Placeholder until real models land."""

    def load(self) -> None:
        self._loaded = True

    def process(self, frame: np.ndarray, *, ctx: dict[str, Any] | None = None) -> np.ndarray:
        return frame
