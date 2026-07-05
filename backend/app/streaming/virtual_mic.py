"""Virtual microphone output.

Isolates the OS-specific virtual-audio backend. The real implementation
(Phase 5) writes processed audio into a standard virtual audio device so any app
that lets the user pick a microphone can select Apex Cam's output.
"""
from __future__ import annotations

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)


class VirtualMicrophone:
    def __init__(self, sample_rate: int = 48000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._open = False

    def open(self) -> None:
        # Phase 5: initialize the virtual audio device here.
        log.info("VirtualMicrophone.open() stub — %d Hz, %d ch", self.sample_rate, self.channels)
        self._open = True

    def send(self, samples: np.ndarray) -> None:
        if not self._open:
            raise RuntimeError("VirtualMicrophone is not open")

    def close(self) -> None:
        self._open = False
