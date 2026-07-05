"""Face enhancer — GFPGAN / CodeFormer via ONNX Runtime.

Restores and sharpens the swapped face so it looks crisp and natural — the final
polish that makes a swap look professional. Runs on onnxruntime, so it works on
**CPU and GPU** alike (no PyTorch needed). This is the same recipe FaceFusion
uses: align the face to a 512 template, run the restorer, paste it back.

Models (place in models/, get them with scripts/download_models.py):
  gfpgan_1.4.onnx    — GFPGAN v1.4 restorer
  codeformer.onnx    — CodeFormer restorer (very natural)
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

MODELS_DIR = Path("models")

# Standard FFHQ 512 five-point template (arcface-style) the restorers expect.
FFHQ_512_TEMPLATE = np.array([
    [192.98138, 239.94708],
    [318.90277, 240.19360],
    [256.63416, 314.01935],
    [201.26117, 371.41043],
    [313.08905, 371.15118],
], dtype=np.float32)

MODEL_FILES = {
    "gfpgan": MODELS_DIR / "gfpgan_1.4.onnx",
    "codeformer": MODELS_DIR / "codeformer.onnx",
}


def enhancer_models_available() -> list[str]:
    return [k for k, p in MODEL_FILES.items() if p.exists() and p.stat().st_size > 1_000_000]


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


class FaceEnhancer:
    def __init__(self) -> None:
        self._session = None
        self._kind = None            # "gfpgan" | "codeformer"
        self._input_names: list[str] = []
        self.strength = 0.8          # blend of enhanced vs original (0..1)

    @property
    def loaded(self) -> bool:
        return self._session is not None

    @property
    def kind(self) -> str | None:
        return self._kind

    def load(self, kind: str) -> bool:
        if self._kind == kind and self._session is not None:
            return True
        path = MODEL_FILES.get(kind)
        if not path or not path.exists():
            return False
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(str(path), providers=_providers())
            self._input_names = [i.name for i in self._session.get_inputs()]
            self._kind = kind
            log.info("Face enhancer loaded: %s (%s)", kind, _providers()[0])
            return True
        except Exception as exc:
            log.warning("Face enhancer failed to load (%s): %s", kind, exc)
            self._session = None
            self._kind = None
            return False

    def unload(self) -> None:
        self._session = None
        self._kind = None

    def enhance(self, frame_bgr: np.ndarray, kps: np.ndarray) -> np.ndarray:
        """Enhance one face given its 5 keypoints (insightface face.kps)."""
        if self._session is None:
            return frame_bgr
        try:
            return self._enhance_one(frame_bgr, np.asarray(kps, dtype=np.float32))
        except Exception as exc:
            log.debug("enhance skipped: %s", exc)
            return frame_bgr

    def _enhance_one(self, frame: np.ndarray, kps: np.ndarray) -> np.ndarray:
        matrix = cv2.estimateAffinePartial2D(kps, FFHQ_512_TEMPLATE, method=cv2.LMEDS)[0]
        if matrix is None:
            return frame
        aligned = cv2.warpAffine(frame, matrix, (512, 512), borderMode=cv2.BORDER_REPLICATE)

        # Preprocess: BGR->RGB, [0,1] -> [-1,1], NCHW
        blob = aligned[:, :, ::-1].astype(np.float32) / 255.0
        blob = (blob - 0.5) / 0.5
        blob = blob.transpose(2, 0, 1)[None]

        feeds = {self._input_names[0]: blob}
        # CodeFormer takes a fidelity weight as a second input.
        if len(self._input_names) > 1:
            feeds[self._input_names[1]] = np.array([0.8], dtype=np.float64)
        out = self._session.run(None, feeds)[0][0]

        # Postprocess back to BGR uint8
        out = np.clip((out.transpose(1, 2, 0) + 1.0) / 2.0, 0, 1) * 255.0
        restored = out.astype(np.uint8)[:, :, ::-1]

        # Paste back with inverse transform. Use the precise face-parsing mask
        # when available (follows the real face shape — no hair/edge bleed);
        # otherwise a soft oval.
        inv = cv2.invertAffineTransform(matrix)
        h, w = frame.shape[:2]
        pasted = cv2.warpAffine(restored, inv, (w, h), borderMode=cv2.BORDER_TRANSPARENT,
                                dst=frame.copy())
        from app.engines.face.face_parser import face_parser
        pmask = face_parser.mask_512(restored)
        if pmask is not None:
            mask512 = (pmask * 255).astype(np.uint8)
        else:
            mask512 = np.zeros((512, 512), dtype=np.uint8)
            cv2.ellipse(mask512, (256, 256), (208, 248), 0, 0, 360, 255, -1)
        mask = cv2.warpAffine(mask512, inv, (w, h))
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=3).astype(np.float32) / 255.0
        alpha = (mask * self.strength)[..., None]
        blended = pasted.astype(np.float32) * alpha + frame.astype(np.float32) * (1 - alpha)
        return blended.astype(np.uint8)


# Singleton
face_enhancer = FaceEnhancer()
