"""Abstract engine interfaces.

Every AI capability in EMY CAM implements one of these small interfaces so the
pipeline can treat engines uniformly and we can swap models (InsightFace,
MediaPipe, OpenVoice, Wav2Lip, ...) without touching the orchestration code.

These are intentionally minimal in Phase 1 — real implementations arrive in the
face/voice/lip-sync phases.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class EngineInfo:
    """Human-readable metadata for the UI's model manager."""

    name: str
    kind: str  # "face" | "voice" | "lipsync"
    loaded: bool = False
    device: str = "cpu"
    details: dict[str, Any] = field(default_factory=dict)


class Engine(ABC):
    """Base for all engines. Handles lifecycle; subclasses do the work."""

    kind: str = "generic"

    def __init__(self, name: str) -> None:
        self.name = name
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @abstractmethod
    def load(self) -> None:
        """Load weights / allocate GPU resources. Idempotent."""

    def unload(self) -> None:
        self._loaded = False

    def info(self) -> EngineInfo:
        return EngineInfo(name=self.name, kind=self.kind, loaded=self._loaded)


class FrameEngine(Engine):
    """Processes a single video frame (H x W x 3, uint8 RGB) and returns one."""

    kind = "face"

    @abstractmethod
    def process(self, frame: np.ndarray, *, ctx: dict[str, Any] | None = None) -> np.ndarray:
        ...


class AudioEngine(Engine):
    """Processes a chunk of mono float32 audio and returns a processed chunk."""

    kind = "voice"

    @abstractmethod
    def process(self, samples: np.ndarray, *, sample_rate: int) -> np.ndarray:
        ...
