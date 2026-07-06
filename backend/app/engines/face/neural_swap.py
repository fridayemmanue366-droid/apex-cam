"""Neural face swap engine (GPU) — the realistic path.

Uses InsightFace's detector/recogniser (buffalo_l) + the **inswapper_128** model.
Unlike the CPU landmark swapper, inswapper transfers only the *identity* of the
target face while preserving the driver's (the user's) pose, expression, mouth
movement and eye blinks — so the result talks, blinks and moves naturally on the
user's real body. Optionally sharpened with GFPGAN.

IMPORTANT: this requires a machine that can install `insightface`,
`onnxruntime-gpu` (CUDA) and download the models — see docs/GPU_SETUP.md. All
heavy imports are guarded so the app still runs (on the CPU landmark swapper)
when these aren't available. This module has been written to the standard
InsightFace API but must be verified on a GPU machine; the build machine here
cannot run it.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

MODELS_DIR = Path("models")
INSWAPPER_FILE = MODELS_DIR / "inswapper_128.onnx"
INSWAPPER_FP16 = MODELS_DIR / "inswapper_128_fp16.onnx"


def best_inswapper() -> Path:
    """Prefer the smaller/faster fp16 model when present (better on weak PCs)."""
    if INSWAPPER_FP16.exists() and INSWAPPER_FP16.stat().st_size > 1_000_000:
        return INSWAPPER_FP16
    return INSWAPPER_FILE
# Override with APEXCAM_INSWAPPER_URL if the default source is unavailable.
INSWAPPER_URL = os.environ.get(
    "APEXCAM_INSWAPPER_URL",
    "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx",
)


def gpu_available() -> bool:
    """True if an ONNX Runtime CUDA provider is present."""
    try:
        import onnxruntime as ort

        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def neural_swap_available() -> bool:
    """True if insightface + onnxruntime import and the swapper model exists."""
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return INSWAPPER_FILE.exists()


GFPGAN_FILE = MODELS_DIR / "GFPGANv1.4.pth"


def enhancer_available() -> bool:
    """True if GFPGAN + torch import and the weights exist (GPU setup done)."""
    try:
        import gfpgan  # noqa: F401
        import torch  # noqa: F401
    except Exception:
        return False
    return GFPGAN_FILE.exists()


def _providers() -> list[str]:
    try:
        import onnxruntime as ort

        avail = ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]
    order = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    return [p for p in order if p in avail] or ["CPUExecutionProvider"]


class NeuralFaceSwapEngine:
    """InsightFace inswapper wrapper. Self-contained: it runs its own detector,
    so it doesn't share the CPU tracker."""

    def __init__(self, use_enhancer: bool = False) -> None:
        self._app = None          # FaceAnalysis
        self._swapper = None      # INSwapper
        self._source_face = None  # target identity embedding/face
        self._loaded = False
        self.enhancer_kind = "none"  # "none" | "gfpgan" | "codeformer"
        self.strength = 1.0
        # Lock the swapped face to the SOURCE PHOTO's exact complexion/brightness
        # (0 = as-is, may darken to room light; 1 = fully the photo's skin tone).
        # Fixes the face coming out darker/off from the chosen photo.
        self.skin_match = 0.9
        self._source_color = None  # cached LAB mean of the source face

    @property
    def ready(self) -> bool:
        return self._loaded and self._source_face is not None

    def load(self) -> bool:
        if self._loaded:
            return True
        try:
            from insightface.app import FaceAnalysis
            from insightface.model_zoo import get_model

            providers = _providers()
            on_gpu = any("CUDA" in p or "Tensorrt" in p for p in providers)
            ctx_id = 0 if on_gpu else -1
            # Bigger detector on GPU (quality); smaller on CPU (speed).
            det = (640, 640) if on_gpu else (320, 320)

            # buffalo_l lives at models/buffalo_l, so root is models/'s parent.
            self._app = FaceAnalysis(
                name="buffalo_l", root=str(MODELS_DIR.parent), providers=providers,
            )
            self._app.prepare(ctx_id=ctx_id, det_size=det)
            model_path = best_inswapper()
            self._swapper = get_model(str(model_path), providers=providers)
            self._loaded = True
            log.info("Neural swap engine loaded (%s, providers=%s)",
                     model_path.name, providers)
            return True
        except Exception as exc:
            log.warning("Neural swap engine unavailable, falling back to CPU: %s", exc)
            self._loaded = False
            return False

    def set_enhancer(self, kind: str) -> str:
        """Select the ONNX face restorer: 'none' | 'gfpgan' | 'codeformer'.
        Runs on onnxruntime (CPU or GPU) — no PyTorch. Returns the active kind."""
        from app.engines.face.enhancer import face_enhancer

        if kind == "none" or not kind:
            self.enhancer_kind = "none"
            return "none"
        if face_enhancer.load(kind):
            self.enhancer_kind = kind
            return kind
        self.enhancer_kind = "none"
        return "none"

    def set_target(self, image_bgr: np.ndarray | None) -> bool:
        """Pick the largest face in the target image as the identity to wear."""
        if image_bgr is None or not self._loaded:
            self._source_face = None
            return False
        faces = self._app.get(image_bgr)
        if not faces:
            log.warning("Neural swap: no face found in target image")
            self._source_face = None
            return False
        self._source_face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        # Remember the photo's exact complexion so the live swap keeps it.
        self._compute_face_color(image_bgr, self._source_face.bbox)
        return True

    def swap_frame(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Detect every face in the live frame and swap identity in, preserving
        the driver's pose/expression/blink. Then optionally restore each face
        with the ONNX enhancer (GFPGAN/CodeFormer)."""
        if not self.ready:
            return frame_bgr
        try:
            from app.engines.face.enhancer import face_enhancer

            faces = self._app.get(frame_bgr)
            # Drop tiny/low-confidence detections (false positives that cause
            # extra/ghost faces). Keep only faces at least ~6% of the frame width.
            fw = frame_bgr.shape[1]
            faces = [f for f in faces if (f.bbox[2] - f.bbox[0]) > fw * 0.06
                     and getattr(f, "det_score", 1.0) > 0.5]
            for face in faces:
                box = self._clamp_box(face.bbox, frame_bgr.shape)
                frame_bgr = self._swapper.get(frame_bgr, face, self._source_face, paste_back=True)
                if self.skin_match > 0 and box and self._source_color is not None:
                    self._match_to_source(frame_bgr, box)
                    self._blend_neck(frame_bgr, box)
                if self.enhancer_kind != "none":
                    frame_bgr = face_enhancer.enhance(frame_bgr, face.kps)
        except Exception as exc:
            log.debug("neural swap frame skipped: %s", exc)
        return frame_bgr

    @staticmethod
    def _clamp_box(bbox, shape) -> tuple[int, int, int, int] | None:
        h, w = shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        return x1, y1, x2, y2

    def _match_to_source(self, frame: np.ndarray, box) -> None:
        """Recolour the swapped face to the SOURCE photo's exact complexion, so
        the result shows the photo's real skin tone/brightness — not darkened by
        the user's room lighting."""
        import cv2

        x1, y1, x2, y2 = box
        swapped = frame[y1:y2, x1:x2]
        h, w = swapped.shape[:2]
        mask = np.zeros((h, w), np.float32)
        cv2.ellipse(mask, (w // 2, h // 2), (int(w * 0.42), int(h * 0.5)), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(w, h) * 0.06)
        sel = mask > 0.3
        if int(sel.sum()) < 20:
            return
        lab_s = cv2.cvtColor(swapped, cv2.COLOR_BGR2LAB).astype(np.float32)
        strength = float(np.clip(self.skin_match, 0, 1))
        for c in range(3):
            shift = self._source_color[c] - lab_s[..., c][sel].mean()
            lab_s[..., c] += shift * strength * mask
        frame[y1:y2, x1:x2] = cv2.cvtColor(
            np.clip(lab_s, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    def _blend_neck(self, frame: np.ndarray, box) -> None:
        """Carry the swapped face's complexion down into the neck/upper-chest
        skin so there's no colour seam at the jaw — the face and body read as one
        person, even when the head turns. Targets skin only (leaves clothes),
        with a gradient that's strong at the jaw and fades downward."""
        import cv2

        x1, y1, x2, y2 = box
        fh, fw = y2 - y1, x2 - x1
        ny1 = max(0, y2 - int(fh * 0.12))
        ny2 = min(frame.shape[0], y2 + int(fh * 1.1))
        nx1 = max(0, x1 - int(fw * 0.2))
        nx2 = min(frame.shape[1], x2 + int(fw * 0.2))
        if ny2 - ny1 < 8 or nx2 - nx1 < 8:
            return
        roi = frame[ny1:ny2, nx1:nx2]
        h, w = roi.shape[:2]
        # Skin mask (YCrCb) so we don't recolour clothes/background.
        ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
        cr, cb = ycc[:, :, 1].astype(int), ycc[:, :, 2].astype(int)
        skin = ((cr > 133) & (cr < 180) & (cb > 77) & (cb < 130)).astype(np.float32)
        grad = np.linspace(1.0, 0.0, h)[:, None]          # strong at jaw, fade down
        mask = cv2.GaussianBlur(skin * grad, (0, 0), sigmaX=max(w, h) * 0.05)
        sel = mask > 0.12
        if int(sel.sum()) < 30:
            return
        lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
        strength = float(np.clip(self.skin_match, 0, 1)) * 0.85
        for c in range(3):
            shift = self._source_color[c] - lab[..., c][sel].mean()
            lab[..., c] += shift * strength * mask
        frame[ny1:ny2, nx1:nx2] = cv2.cvtColor(
            np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    def _compute_face_color(self, image_bgr: np.ndarray, bbox) -> None:
        """Cache the source photo's face-oval LAB mean (its complexion)."""
        import cv2

        box = self._clamp_box(bbox, image_bgr.shape)
        if not box:
            self._source_color = None
            return
        x1, y1, x2, y2 = box
        crop = image_bgr[y1:y2, x1:x2]
        h, w = crop.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        cv2.ellipse(mask, (w // 2, h // 2), (int(w * 0.42), int(h * 0.5)), 0, 0, 360, 255, -1)
        sel = mask > 0
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
        self._source_color = [float(lab[..., c][sel].mean()) for c in range(3)]

    @staticmethod
    def download_model() -> bool:
        """Fetch inswapper_128.onnx if missing. Returns True if present after."""
        if INSWAPPER_FILE.exists():
            return True
        try:
            import urllib.request

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            log.info("Downloading inswapper model from %s", INSWAPPER_URL)
            urllib.request.urlretrieve(INSWAPPER_URL, INSWAPPER_FILE)
            return INSWAPPER_FILE.exists()
        except Exception as exc:
            log.error("Failed to download inswapper model: %s", exc)
            return False
