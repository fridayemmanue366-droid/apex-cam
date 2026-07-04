"""Virtual camera output (Phase 4).

Publishes the pipeline's processed frames as a standard OS camera device via
pyvirtualcam (OBS Virtual Camera driver on Windows). Any desktop app with a
camera picker — Zoom, Discord, Meet, Teams, WhatsApp Desktop, Telegram
Desktop, OBS — can select it. Only the processed, AI-GENERATED-labeled output
ever flows through here; the raw camera never does. No per-app hooks.
"""
from __future__ import annotations

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

DRIVER_HELP = (
    "Virtual camera driver not found. Install OBS Studio (free, includes the "
    "'OBS Virtual Camera' driver) from https://obsproject.com, then try again."
)


class VirtualCamera:
    """Thin wrapper around pyvirtualcam with friendly errors and lazy open."""

    def __init__(self) -> None:
        self._cam = None
        self.device: str | None = None

    @property
    def active(self) -> bool:
        return self._cam is not None

    def open(self, width: int, height: int, fps: int) -> None:
        if self._cam is not None:
            return
        try:
            import pyvirtualcam
        except ImportError as exc:
            raise RuntimeError("pyvirtualcam is not installed") from exc
        try:
            self._cam = pyvirtualcam.Camera(
                width=width, height=height, fps=fps,
                fmt=pyvirtualcam.PixelFormat.BGR,
            )
        except Exception as exc:
            raise RuntimeError(DRIVER_HELP) from exc
        self.device = self._cam.device
        log.info("Virtual camera live: %s (%dx%d @ %d fps)", self.device, width, height, fps)

    def send(self, frame_bgr: np.ndarray) -> None:
        """Push one processed BGR frame (must match the opened size)."""
        if self._cam is None:
            raise RuntimeError("Virtual camera is not open")
        self._cam.send(frame_bgr)

    def close(self) -> None:
        if self._cam is not None:
            self._cam.close()
            self._cam = None
            self.device = None
            log.info("Virtual camera closed")
