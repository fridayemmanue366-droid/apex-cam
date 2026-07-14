"""Virtual camera output (Phase 4).

Publishes the pipeline's processed frames as a standard OS camera device via
pyvirtualcam. Any desktop app with a camera picker — Zoom, Discord, Meet, Teams,
WhatsApp Desktop, Telegram, YouCam, OBS — can select it. Only the processed Apex
Cam output flows through here; the raw camera never does. No per-app hooks.

Apex Cam ships its OWN virtual-camera driver (Unity Capture, MIT-licensed),
registered as the device "Apex Cam" by the installer — so customers do NOT need
to install OBS. We prefer that backend, then fall back to OBS's driver (handy on a
dev box), then pyvirtualcam's default.
"""
from __future__ import annotations

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

# Preference order for the underlying driver. "unitycapture" is the one Apex Cam
# bundles and registers as "Apex Cam"; "obs" is a fallback if only OBS is present.
BACKENDS = ("unitycapture", "obs")

DRIVER_HELP = (
    "The Apex Cam virtual camera isn't set up. Re-run the Apex Cam installer and keep "
    "the “Install the Apex Cam virtual camera” option ticked (it shows a one-time "
    "approval prompt). Then try Go Live again."
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
        # Try our bundled driver first, then OBS, then whatever pyvirtualcam finds.
        last_exc: Exception | None = None
        for backend in (*BACKENDS, None):
            try:
                kwargs = {"width": width, "height": height, "fps": fps,
                          "fmt": pyvirtualcam.PixelFormat.BGR}
                if backend:
                    kwargs["backend"] = backend
                self._cam = pyvirtualcam.Camera(**kwargs)
                break
            except Exception as exc:      # backend missing/unavailable — try the next
                last_exc = exc
                self._cam = None
        if self._cam is None:
            raise RuntimeError(DRIVER_HELP) from last_exc
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
