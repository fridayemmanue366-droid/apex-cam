"""Apex Cam -> Windows 11 Media Foundation virtual camera.

Publishes the "Apex Cam" camera that Media-Foundation apps (WhatsApp, the Windows
Camera app, Teams) can see — which the DirectShow virtual camera can't reach.

It launches the C# bridge (vcam_mf/ApexCamVCam.exe), which registers the MF
camera, and streams processed frames to it through a shared memory-mapped file.
Requires the LocosLab virtual camera runtime to be installed (the Apex Cam
installer ships it). If the bridge/runtime isn't present, start() just returns
False and the app keeps working with the DirectShow camera.
"""
from __future__ import annotations

import mmap
import os
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

WIDTH, HEIGHT = 960, 540   # must match ApexCamVCam.cs
FRAME_BYTES = WIDTH * HEIGHT * 4
MMF_TAG = "Local\\ApexCamFrame"
# Verified on a real Win11 machine: our top-down frames come out right-side up
# WITHOUT a vertical flip. Set APEXCAM_MF_FLIP=1 to flip if a setup ever needs it.
FLIP = os.environ.get("APEXCAM_MF_FLIP", "0") not in ("0", "false", "")
CREATE_NO_WINDOW = 0x08000000


def _find_exe() -> Path | None:
    here = Path(__file__).resolve()
    for c in (
        here.parents[2] / "vcam_mf" / "ApexCamVCam.exe",   # dev: backend/vcam_mf
        here.parents[3] / "vcam_mf" / "ApexCamVCam.exe",   # bundle: <appRoot>/vcam_mf
    ):
        if c.exists():
            return c
    return None


class MFCamera:
    """Feeds processed frames to the Media-Foundation "Apex Cam" device."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._mm: mmap.mmap | None = None
        self._lock = threading.Lock()
        self.active = False
        self.error: str | None = None

    def start(self) -> bool:
        with self._lock:
            if self.active:
                return True
            exe = _find_exe()
            if not exe:
                self.error = "MF camera bridge not found"
                return False
            try:
                self._proc = subprocess.Popen(
                    [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    creationflags=CREATE_NO_WINDOW)
                started = False
                for _ in range(80):
                    line = self._proc.stdout.readline() if self._proc.stdout else ""
                    if not line:
                        break
                    if "APEXCAM_STARTED" in line:
                        started = True
                        break
                    if "FAILED" in line or "ERROR" in line:
                        self.error = line.strip()
                        break
                if not started:
                    self.error = self.error or ("Apex Cam camera could not start — is the "
                                                "Apex Cam virtual camera installed?")
                    self._proc.terminate()
                    self._proc = None
                    return False
                self._mm = mmap.mmap(-1, FRAME_BYTES, tagname=MMF_TAG, access=mmap.ACCESS_WRITE)
                self.active = True
                self.error = None
                log.info("MF virtual camera 'Apex Cam' started")
                return True
            except Exception as exc:
                self.error = str(exc)
                log.warning("MF camera start failed: %s", exc)
                self._proc = None
                return False

    def send(self, frame_bgr: np.ndarray) -> None:
        if not self.active or self._mm is None:
            return
        try:
            if frame_bgr.shape[1] != WIDTH or frame_bgr.shape[0] != HEIGHT:
                frame_bgr = cv2.resize(frame_bgr, (WIDTH, HEIGHT))
            bgra = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA)
            if FLIP:
                bgra = np.flipud(bgra)
            self._mm.seek(0)
            self._mm.write(np.ascontiguousarray(bgra).tobytes())
        except Exception as exc:
            log.debug("MF camera send failed: %s", exc)

    def close(self) -> None:
        with self._lock:
            self.active = False
            if self._proc:
                try:
                    if self._proc.stdin:
                        self._proc.stdin.write("stop\n")
                        self._proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None
            if self._mm:
                try:
                    self._mm.close()
                except Exception:
                    pass
                self._mm = None
