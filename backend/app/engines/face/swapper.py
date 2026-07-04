"""Face swap engine — Phase 7.

A landmark-based swapper that runs in real time on CPU (no GPU, no InsightFace,
which has no Python 3.14 wheels):

    1. The target face (a profile photo) is detected once and its 5 key
       landmarks + an elliptical face mask are cached.
    2. For each face the tracker finds in the live frame, a similarity transform
       maps the target's landmarks onto the live face's landmarks.
    3. The target face is warped through that transform, colour-matched to the
       live face, and seamlessly blended in.

This produces a genuine, recognisable swap. It is intentionally behind the same
interface a neural swapper (inswapper / SimSwap) would use, so on GPU machines
the high-fidelity model drops in here without touching the pipeline.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.core.logging import get_logger
from app.engines.face.tracker import Face, FaceTracker

log = get_logger(__name__)


class FaceSwapEngine:
    def __init__(self, tracker: FaceTracker) -> None:
        self._tracker = tracker
        self._target_img: np.ndarray | None = None
        self._target_lms: np.ndarray | None = None
        self.strength: float = 0.85  # blend opacity 0..1

    @property
    def ready(self) -> bool:
        return self._target_img is not None and self._target_lms is not None

    def set_target(
        self,
        image_bgr: np.ndarray | None,
        landmarks: list[tuple[int, int]] | None = None,
    ) -> bool:
        """Cache a target face from a profile image. ``landmarks`` may be given
        directly (e.g. for built-in demo faces the detector can't read);
        otherwise the face is detected. Returns True if a usable face is ready."""
        if image_bgr is None:
            self._target_img = None
            self._target_lms = None
            return False
        if landmarks is not None and len(landmarks) >= 5:
            self._target_img = image_bgr
            self._target_lms = np.array(landmarks[:5], dtype=np.float32)
            log.info("Swap target set from explicit landmarks")
            return True
        faces = self._tracker.detect(image_bgr)
        if not faces:
            log.warning("Swap target image has no detectable face")
            self._target_img = None
            self._target_lms = None
            return False
        face = max(faces, key=lambda f: f.box[2] * f.box[3])
        self._target_img = image_bgr
        self._target_lms = np.array(face.landmarks, dtype=np.float32)
        log.info("Swap target set (%dx%d)", image_bgr.shape[1], image_bgr.shape[0])
        return True

    def swap(self, frame_bgr: np.ndarray, faces: list[Face]) -> np.ndarray:
        if not self.ready:
            return frame_bgr
        for face in faces:
            if len(face.landmarks) < 5:
                continue
            try:
                frame_bgr = self._swap_one(frame_bgr, face)
            except Exception as exc:  # never let a bad frame crash the pipeline
                log.debug("swap skipped: %s", exc)
        return frame_bgr

    def _swap_one(self, frame: np.ndarray, face: Face) -> np.ndarray:
        dst_lms = np.array(face.landmarks, dtype=np.float32)
        matrix, _ = cv2.estimateAffinePartial2D(self._target_lms, dst_lms, method=cv2.LMEDS)
        if matrix is None:
            return frame

        h, w = frame.shape[:2]
        warped = cv2.warpAffine(self._target_img, matrix, (w, h),
                                borderMode=cv2.BORDER_REFLECT)

        # Build a face-shaped mask in the TARGET image and warp it with the SAME
        # transform, so the blended area follows the swapped face precisely —
        # no hair/background/corner bleed like a fixed ellipse on the user's box.
        th, tw = self._target_img.shape[:2]
        tl = self._target_lms
        eye_dist = float(np.linalg.norm(tl[0] - tl[1])) + 1e-3
        tcx, tcy = tl.mean(axis=0)
        tmask = np.zeros((th, tw), dtype=np.uint8)
        axes = (int(eye_dist * 1.5), int(eye_dist * 2.05))  # covers forehead+jaw
        cv2.ellipse(tmask, (int(tcx), int(tcy - eye_dist * 0.25)), axes,
                    0, 0, 360, 255, -1)
        mask = cv2.warpAffine(tmask, matrix, (w, h))
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=eye_dist * 0.14 + 1)

        warped = self._match_color(warped, frame, mask)

        # Feathered alpha blend, scaled by strength.
        alpha = (mask.astype(np.float32) / 255.0)[..., None] * self.strength
        out = (warped.astype(np.float32) * alpha + frame.astype(np.float32) * (1 - alpha))
        return out.astype(np.uint8)

    @staticmethod
    def _match_color(src: np.ndarray, ref: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Shift src's mean/std toward ref within the mask (simple colour transfer)."""
        m = mask > 20
        if m.sum() < 50:
            return src
        out = src.astype(np.float32)
        ref_f = ref.astype(np.float32)
        for c in range(3):
            s_mean, s_std = out[..., c][m].mean(), out[..., c][m].std() + 1e-5
            r_mean, r_std = ref_f[..., c][m].mean(), ref_f[..., c][m].std() + 1e-5
            out[..., c] = (out[..., c] - s_mean) * (r_std / s_std) + r_mean
        return np.clip(out, 0, 255).astype(np.uint8)
