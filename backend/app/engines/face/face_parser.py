"""Face parsing (BiSeNet, ONNX) — precise full-face mask.

Segments an aligned face into 19 regions (skin, brows, eyes, nose, mouth,
lips, …) and returns a soft mask of just the face — so the swap/enhancer blends
exactly around the real face shape, not a rough oval, and never bleeds onto hair
or background. Runs on onnxruntime (CPU or GPU).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger
from app.core.onnx_providers import get_providers as _providers

log = get_logger(__name__)

PARSER_FILE = Path("models") / "bisenet_resnet_34.onnx"

# BiSeNet/CelebAMask-HQ classes to KEEP as "face" (exclude hair 17, background 0,
# neck 14, cloth 16, hat 18, glasses 6, ears/earring 7/8/9).
FACE_CLASSES = (1, 2, 3, 4, 5, 10, 11, 12, 13)  # skin, brows, eyes, nose, mouth, lips
# Full head = face + ears + hair + hat (everything that is "the person's head").
HEAD_CLASSES = FACE_CLASSES + (7, 8, 9, 17, 18)
# Just the hair (and hat) — used to keep the USER's real hair on top of a swap so
# the swapped forehead can't cut into the hairline.
HAIR_CLASSES = (17, 18)

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def parser_available() -> bool:
    return PARSER_FILE.exists() and PARSER_FILE.stat().st_size > 1_000_000


class FaceParser:
    def __init__(self) -> None:
        self._session = None
        self._input = None

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def load(self) -> bool:
        if self._session is not None:
            return True
        if not parser_available():
            return False
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(str(PARSER_FILE), providers=_providers())
            self._input = self._session.get_inputs()[0].name
            log.info("Face parser loaded (bisenet)")
            return True
        except Exception as exc:
            log.warning("Face parser failed to load: %s", exc)
            self._session = None
            return False

    def mask_512(self, aligned_bgr: np.ndarray, include_hair: bool = False) -> np.ndarray | None:
        """Return a soft 512x512 float mask (0..1) of the face — or the whole head
        (face + hair + ears) when ``include_hair`` is set — for an aligned 512x512
        crop. Returns None if unavailable."""
        if self._session is None and not self.load():
            return None
        try:
            img = cv2.resize(aligned_bgr, (512, 512))
            blob = img[:, :, ::-1].astype(np.float32) / 255.0
            blob = (blob - _MEAN) / _STD
            blob = blob.transpose(2, 0, 1)[None]
            out = self._session.run(None, {self._input: blob})[0][0]  # [19,512,512]
            classes = np.argmax(out, axis=0)
            keep = HEAD_CLASSES if include_hair else FACE_CLASSES
            mask = np.isin(classes, keep).astype(np.float32)
            # tidy: close holes, pull in from the very edge, feather
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
            mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=6)
            mask = np.clip(mask, 0, 1)
            # Sanity: an aligned face should be a sensible fraction of the crop.
            # If parsing looks wrong (too little/too much), let the caller fall back.
            cov = float(mask.mean())
            if cov < 0.06 or cov > 0.95:
                return None
            return mask
        except Exception as exc:
            log.debug("face parse skipped: %s", exc)
            return None

    def hair_mask_512(self, aligned_bgr: np.ndarray) -> np.ndarray | None:
        """Soft 512x512 mask (0..1) of just the HAIR (and hat) in an aligned crop.
        Returns None if unavailable or essentially no hair is present."""
        if self._session is None and not self.load():
            return None
        try:
            img = cv2.resize(aligned_bgr, (512, 512))
            blob = img[:, :, ::-1].astype(np.float32) / 255.0
            blob = (blob - _MEAN) / _STD
            blob = blob.transpose(2, 0, 1)[None]
            out = self._session.run(None, {self._input: blob})[0][0]
            classes = np.argmax(out, axis=0)
            mask = np.isin(classes, HAIR_CLASSES).astype(np.float32)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=4)
            mask = np.clip(mask, 0, 1)
            if float(mask.mean()) < 0.004:   # basically no hair (e.g. bald)
                return None
            return mask
        except Exception as exc:
            log.debug("hair parse skipped: %s", exc)
            return None


# Singleton
face_parser = FaceParser()
