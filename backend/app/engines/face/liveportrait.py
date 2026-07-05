"""LivePortrait avatar engine (ONNX) — the lifelike animation path.

Turns a chosen portrait photo into a live puppet: the photo keeps its identity
but takes on YOUR head pose, expression, eyes and mouth in real time. This is
the most advanced realism tier (the same model behind top face-animation tools),
running on onnxruntime — CPU or GPU, no PyTorch.

Pipeline (verified):
  source photo -> feature volume + source keypoints (once)
  each frame:   your face -> pose/expression -> driving keypoints
                generator(feature_volume, source_kp, driving_kp) -> animated face

Models in models/liveportrait/ (get via scripts/download_models.py).
Heavy: smooth on GPU, slow on CPU (it's a true GPU feature).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

LP_DIR = Path("models") / "liveportrait"
FILES = {
    "feature": LP_DIR / "live_portrait_feature_extractor.onnx",
    "motion": LP_DIR / "live_portrait_motion_extractor.onnx",
    "generator": LP_DIR / "live_portrait_generator.onnx",
}


def liveportrait_available() -> bool:
    return all(p.exists() and p.stat().st_size > 100_000 for p in FILES.values())


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


def _rotation(pitch: float, yaw: float, roll: float) -> np.ndarray:
    p, y, r = np.deg2rad([pitch, yaw, roll])
    rx = np.array([[1, 0, 0], [0, np.cos(p), -np.sin(p)], [0, np.sin(p), np.cos(p)]])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rz = np.array([[np.cos(r), -np.sin(r), 0], [np.sin(r), np.cos(r), 0], [0, 0, 1]])
    return (rx @ ry @ rz).astype(np.float32)


class LivePortraitEngine:
    def __init__(self) -> None:
        self._feat = None
        self._motion = None
        self._gen = None
        self._loaded = False
        # cached source
        self._fv = None            # feature volume
        self._x_s = None           # source keypoints
        self._src_kp = None        # source canonical motion_points
        self._src_scale = None
        self._src_t = None
        self.ready = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> bool:
        if self._loaded:
            return True
        if not liveportrait_available():
            return False
        try:
            import onnxruntime as ort
            p = _providers()
            self._feat = ort.InferenceSession(str(FILES["feature"]), providers=p)
            self._motion = ort.InferenceSession(str(FILES["motion"]), providers=p)
            self._gen = ort.InferenceSession(str(FILES["generator"]), providers=p)
            self._loaded = True
            log.info("LivePortrait loaded (%s)", p[0])
            return True
        except Exception as exc:
            log.warning("LivePortrait failed to load: %s", exc)
            self._loaded = False
            return False

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _prep(img256: np.ndarray) -> np.ndarray:
        return (img256[:, :, ::-1] / 255.0).transpose(2, 0, 1)[None].astype(np.float32)

    def _motion_info(self, img256: np.ndarray) -> dict:
        out = self._motion.run(None, {"input": self._prep(img256)})
        names = [o.name for o in self._motion.get_outputs()]
        return dict(zip(names, out))

    @staticmethod
    def _transform(mp, pitch, yaw, roll, exp, scale, t) -> np.ndarray:
        R = _rotation(float(pitch), float(yaw), float(roll))
        kp = mp @ R + exp
        kp = kp * scale[..., None]
        kp[..., 0:2] += t[:, None, 0:2]
        return kp.astype(np.float32)

    @staticmethod
    def _crop(img: np.ndarray, bbox) -> tuple[np.ndarray, tuple[int, int, int]]:
        x0b, y0b, x1b, y1b = bbox
        cx, cy = (x0b + x1b) / 2, (y0b + y1b) / 2
        s = int(max(x1b - x0b, y1b - y0b) * 1.4)
        x0, y0 = int(cx - s), int(cy - s)
        pad_t, pad_l = max(0, -y0), max(0, -x0)
        pad_b = max(0, y0 + 2 * s - img.shape[0])
        pad_r = max(0, x0 + 2 * s - img.shape[1])
        padded = cv2.copyMakeBorder(img, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REFLECT)
        x0 += pad_l
        y0 += pad_t
        crop = padded[y0:y0 + 2 * s, x0:x0 + 2 * s]
        return cv2.resize(crop, (256, 256)), (x0 - pad_l, y0 - pad_t, 2 * s)

    # -- public -------------------------------------------------------------

    def set_source(self, image_bgr: np.ndarray | None, app) -> bool:
        """Cache the source portrait (identity to animate). ``app`` is the
        insightface FaceAnalysis used to find the face."""
        self.ready = False
        if image_bgr is None or not self.load():
            return False
        faces = app.get(image_bgr)
        if not faces:
            return False
        face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        crop, _ = self._crop(image_bgr, face.bbox)
        self._fv = self._feat.run(None, {"input": self._prep(crop)})[0]
        d = self._motion_info(crop)
        self._src_kp = d["motion_points"]
        self._src_scale = d["scale"]
        self._src_t = d["translation"]
        self._x_s = self._transform(d["motion_points"], d["pitch"], d["yaw"], d["roll"],
                                    d["expression"], d["scale"], d["translation"])
        self.ready = True
        log.info("LivePortrait source set")
        return True

    def animate(self, frame_bgr: np.ndarray, face) -> np.ndarray:
        """Replace the given face with the source identity, animated by this
        frame's pose/expression."""
        if not self.ready:
            return frame_bgr
        try:
            crop, (x0, y0, size) = self._crop(frame_bgr, face.bbox)
            d = self._motion_info(crop)
            # source identity (canonical kp/scale/translation) + your motion
            x_d = self._transform(self._src_kp, d["pitch"], d["yaw"], d["roll"],
                                  d["expression"], self._src_scale, self._src_t)
            out = self._gen.run(None, {"feature_volume": self._fv,
                                       "source": self._x_s, "target": x_d})[0][0]
            gen = np.clip(out.transpose(1, 2, 0)[:, :, ::-1] * 255, 0, 255).astype(np.uint8)
            gen = cv2.resize(gen, (size, size))

            h, w = frame_bgr.shape[:2]
            gx0, gy0 = max(0, x0), max(0, y0)
            gx1, gy1 = min(w, x0 + size), min(h, y0 + size)
            cx0, cy0 = gx0 - x0, gy0 - y0
            region = gen[cy0:cy0 + (gy1 - gy0), cx0:cx0 + (gx1 - gx0)]
            # feathered oval blend
            mask = np.zeros((size, size), np.uint8)
            cv2.ellipse(mask, (size // 2, size // 2), (int(size * 0.42), int(size * 0.5)),
                        0, 0, 360, 255, -1)
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=size * 0.05)
            m = (mask[cy0:cy0 + (gy1 - gy0), cx0:cx0 + (gx1 - gx0)] / 255.0)[..., None]
            frame_bgr[gy0:gy1, gx0:gx1] = (
                region * m + frame_bgr[gy0:gy1, gx0:gx1] * (1 - m)
            ).astype(np.uint8)
        except Exception as exc:
            log.debug("liveportrait animate skipped: %s", exc)
        return frame_bgr


# Singleton
liveportrait = LivePortraitEngine()
