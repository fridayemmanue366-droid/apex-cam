"""Background segmentation (Robust Video Matting, ONNX) — real-time on CPU.

Separates you from your background so EMY CAM can blur it, replace it with an
image, or key it to a solid colour (green screen). Uses RVM's recurrent matting
for smooth, flicker-free edges across frames. Runs on onnxruntime (CPU or GPU);
the mobilenet model is light enough for real-time on a laptop.

Model: models/rvm_mobilenetv3_fp32.onnx (get via scripts/download_models.py).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

RVM_FILE = Path("models") / "rvm_mobilenetv3_fp32.onnx"


def background_available() -> bool:
    return RVM_FILE.exists() and RVM_FILE.stat().st_size > 1_000_000


def _providers() -> list[str]:
    try:
        import onnxruntime as ort
        avail = ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]
    for gpu in ("CUDAExecutionProvider", "TensorrtExecutionProvider"):
        if gpu in avail:
            return [gpu, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


class BackgroundEngine:
    def __init__(self) -> None:
        self._session = None
        self._rec = [np.zeros([1, 1, 1, 1], np.float32)] * 4  # recurrent state
        self.mode = "off"            # off | blur | color | image
        self.blur_strength = 35
        self.color = (0, 200, 0)     # BGR green-screen
        self._bg_image = None        # replacement background (BGR)
        self._bg_path: str | None = None

    @property
    def available(self) -> bool:
        return background_available()

    def load(self) -> bool:
        if self._session is not None:
            return True
        if not background_available():
            return False
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(str(RVM_FILE), providers=_providers())
            log.info("Background matting loaded (RVM, %s)", _providers()[0])
            return True
        except Exception as exc:
            log.warning("Background matting failed to load: %s", exc)
            return False

    def reset_state(self) -> None:
        self._rec = [np.zeros([1, 1, 1, 1], np.float32)] * 4

    def set_bg_image(self, path: str | None) -> bool:
        self._bg_path = path
        if not path:
            self._bg_image = None
            return False
        img = cv2.imread(path)
        self._bg_image = img
        return img is not None

    def _alpha(self, frame_bgr: np.ndarray) -> np.ndarray | None:
        """Return a HxW float alpha matte (1 = person)."""
        if self._session is None and not self.load():
            return None
        h, w = frame_bgr.shape[:2]
        src = frame_bgr[:, :, ::-1].astype(np.float32) / 255.0
        src = src.transpose(2, 0, 1)[None]
        ratio = np.array([0.25 if max(h, w) <= 720 else 0.375], np.float32)
        feeds = {
            "src": src, "downsample_ratio": ratio,
            "r1i": self._rec[0], "r2i": self._rec[1],
            "r3i": self._rec[2], "r4i": self._rec[3],
        }
        out = self._session.run(["pha", "r1o", "r2o", "r3o", "r4o"], feeds)
        pha, self._rec = out[0], out[1:]
        return pha[0, 0]  # HxW

    def matte(self, img_bgr: np.ndarray) -> np.ndarray | None:
        """One-off person alpha matte (fresh state) — used to cut a generated
        head cleanly from its background. Returns HxW float alpha or None."""
        if self._session is None and not self.load():
            return None
        try:
            h, w = img_bgr.shape[:2]
            src = img_bgr[:, :, ::-1].astype(np.float32) / 255.0
            src = src.transpose(2, 0, 1)[None]
            zero = np.zeros([1, 1, 1, 1], np.float32)
            feeds = {"src": src, "downsample_ratio": np.array([0.5], np.float32),
                     "r1i": zero, "r2i": zero, "r3i": zero, "r4i": zero}
            pha = self._session.run(["pha"], feeds)[0][0, 0]
            return np.clip(pha, 0, 1)
        except Exception:
            return None

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        if self.mode == "off":
            return frame_bgr
        try:
            alpha = self._alpha(frame_bgr)
            if alpha is None:
                return frame_bgr
            h, w = frame_bgr.shape[:2]
            if alpha.shape != (h, w):
                alpha = cv2.resize(alpha, (w, h))
            a = np.clip(alpha, 0, 1)[..., None]

            if self.mode == "blur":
                k = max(3, int(self.blur_strength) | 1)
                bg = cv2.GaussianBlur(frame_bgr, (k, k), 0)
            elif self.mode == "color":
                bg = np.full_like(frame_bgr, self.color)
            elif self.mode == "image" and self._bg_image is not None:
                bg = cv2.resize(self._bg_image, (w, h))
            else:
                return frame_bgr

            out = frame_bgr.astype(np.float32) * a + bg.astype(np.float32) * (1 - a)
            return out.astype(np.uint8)
        except Exception as exc:
            log.debug("background process skipped: %s", exc)
            return frame_bgr


# Singleton
background = BackgroundEngine()
