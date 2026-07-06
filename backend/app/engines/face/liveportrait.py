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
    def _apply_kp(mp, R, exp, scale, t) -> np.ndarray:
        kp = mp @ R + exp
        kp = kp * scale[..., None]
        kp[..., 0:2] += t[:, None, 0:2]
        return kp.astype(np.float32)

    # Larger crop so the whole head + hair is captured/generated (not just face).
    CROP_SCALE = 2.0

    @classmethod
    def _crop(cls, img: np.ndarray, bbox) -> tuple[np.ndarray, tuple[int, int, int]]:
        x0b, y0b, x1b, y1b = bbox
        cx, cy = (x0b + x1b) / 2, (y0b + y1b) / 2
        s = int(max(x1b - x0b, y1b - y0b) * cls.CROP_SCALE)
        # Bias the crop upward so hair above the face is included.
        cy -= s * 0.12
        x0, y0 = int(cx - s), int(cy - s)
        pad_t, pad_l = max(0, -y0), max(0, -x0)
        pad_b = max(0, y0 + 2 * s - img.shape[0])
        pad_r = max(0, x0 + 2 * s - img.shape[1])
        # REPLICATE (extend edge pixels), NOT REFLECT — reflect mirrors the face
        # into the padding and produces a duplicate/upside-down head at edges.
        padded = cv2.copyMakeBorder(img, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REPLICATE)
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
        self._src_exp = d["expression"]
        self._R_s = _rotation(float(d["pitch"]), float(d["yaw"]), float(d["roll"]))
        self._x_s = self._apply_kp(self._src_kp, self._R_s, self._src_exp,
                                   self._src_scale, self._src_t)
        self._d0 = None  # your neutral baseline — captured on the first live frame
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
            R_d = _rotation(float(d["pitch"]), float(d["yaw"]), float(d["roll"]))
            # Capture your neutral pose/expression on the first frame.
            if self._d0 is None:
                self._d0 = {"R": R_d, "exp": d["expression"],
                            "scale": d["scale"], "t": d["translation"]}
            d0 = self._d0
            # RELATIVE motion: apply the *change* in your pose/expression to the
            # source identity — so the photo blinks, talks and turns as YOU do,
            # instead of holding the photo's original expression.
            R_new = R_d @ d0["R"].T @ self._R_s
            exp_new = self._src_exp + (d["expression"] - d0["exp"])
            scale_new = self._src_scale * (d["scale"] / np.maximum(d0["scale"], 1e-6))
            t_new = self._src_t + (d["translation"] - d0["t"])
            x_d = self._apply_kp(self._src_kp, R_new, exp_new, scale_new, t_new)
            out = self._gen.run(None, {"feature_volume": self._fv,
                                       "source": self._x_s, "target": x_d})[0][0]
            gen512 = np.clip(out.transpose(1, 2, 0)[:, :, ::-1] * 255, 0, 255).astype(np.uint8)

            # Cut the generated head cleanly from the photo's background using the
            # RVM person matte (so hair + head shape come across, not a rectangle
            # of background). Intersect with a head-region oval to drop shoulders.
            from app.engines.background import background
            hm = background.matte(gen512)
            if hm is None or float(hm.mean()) < 0.02:
                hm = np.zeros((512, 512), np.float32)
                cv2.ellipse(hm, (256, 236), (int(512 * 0.42), int(512 * 0.5)), 0, 0, 360, 1.0, -1)
            else:
                # Solidify the matte: firm interior, soft edge (clean full-head cut).
                hm = np.clip((hm - 0.35) / 0.4, 0, 1)
            hm = cv2.GaussianBlur(hm.astype(np.float32), (0, 0), sigmaX=512 * 0.02)

            gen = cv2.resize(gen512, (size, size))
            mask = cv2.resize(hm, (size, size))
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=size * 0.02)

            h, w = frame_bgr.shape[:2]
            gx0, gy0 = max(0, x0), max(0, y0)
            gx1, gy1 = min(w, x0 + size), min(h, y0 + size)
            cx0, cy0 = gx0 - x0, gy0 - y0
            region = gen[cy0:cy0 + (gy1 - gy0), cx0:cx0 + (gx1 - gx0)]
            m = np.clip(mask[cy0:cy0 + (gy1 - gy0), cx0:cx0 + (gx1 - gx0)], 0, 1)[..., None]
            frame_bgr[gy0:gy1, gx0:gx1] = (
                region * m + frame_bgr[gy0:gy1, gx0:gx1] * (1 - m)
            ).astype(np.uint8)
        except Exception as exc:
            log.debug("liveportrait animate skipped: %s", exc)
        return frame_bgr


# Singleton
liveportrait = LivePortraitEngine()
