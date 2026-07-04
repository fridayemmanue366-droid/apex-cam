"""Procedural lip-sync — Phase 9.

Drives mouth movement from live audio loudness: when you speak, the jaw/mouth
region of each tracked face opens proportionally to the sound level, smoothed
over time so it looks natural. Runs in real time on CPU using the tracker's
mouth landmarks and a local vertical warp — no GPU, no model download.

This is the CPU lip-sync path. On GPU machines the same slot upgrades to Wav2Lip
for phoneme-accurate lips; the pipeline only depends on the ``apply`` interface.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.engines.face.tracker import Face


class ProceduralLipSync:
    def __init__(self) -> None:
        self._open = 0.0  # smoothed openness 0..1

    def update_level(self, level: float) -> None:
        # Attack fast, release slower — mimics speech envelope.
        target = float(np.clip(level * 2.2, 0.0, 1.0))
        a = 0.6 if target > self._open else 0.25
        self._open = (1 - a) * self._open + a * target

    def apply(self, frame_bgr: np.ndarray, faces: list[Face]) -> np.ndarray:
        if self._open < 0.03:
            return frame_bgr
        for f in faces:
            if len(f.landmarks) >= 5:
                try:
                    self._animate(frame_bgr, f)
                except Exception:
                    pass
        return frame_bgr

    def _animate(self, frame: np.ndarray, face: Face) -> None:
        h, w = frame.shape[:2]
        nose = face.landmarks[2]
        m_r, m_l = face.landmarks[3], face.landmarks[4]
        mcx = (m_r[0] + m_l[0]) // 2
        mcy = (m_r[1] + m_l[1]) // 2
        mouth_w = max(abs(m_l[0] - m_r[0]), face.box[2] // 4)

        # ROI covering the lower face (nose → chin).
        rw = int(mouth_w * 1.8)
        rh = int(abs(mcy - nose[1]) * 2 + face.box[3] * 0.28)
        x0, y0 = max(0, mcx - rw // 2), max(0, mcy - rh // 2)
        x1, y1 = min(w, mcx + rw // 2), min(h, mcy + rh // 2)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        roi = frame[y0:y1, x0:x1]
        rh2, rw2 = roi.shape[:2]

        # Max jaw drop in pixels, scaled by face size and current openness.
        max_shift = face.box[3] * 0.20 * self._open

        # Vertical displacement map: samples below the mouth line are pulled up
        # from lower rows (so the chin/lower lip appears to drop), with a smooth
        # Gaussian falloff around the mouth centre.
        ys, xs = np.mgrid[0:rh2, 0:rw2].astype(np.float32)
        mouth_y = (mcy - y0)
        below = np.clip((ys - mouth_y) / max(rh2 - mouth_y, 1), 0, 1)
        xfall = np.exp(-(((xs - (mcx - x0)) / (rw2 * 0.35)) ** 2))
        shift = max_shift * below * xfall
        map_y = (ys + shift).astype(np.float32)
        map_x = xs
        warped = cv2.remap(roi, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)

        # Darken the inner-mouth opening for depth (a visible open mouth).
        inner = (below > 0.12).astype(np.float32) * xfall * self._open * 0.55
        warped = (warped.astype(np.float32) * (1 - inner[..., None])).astype(np.uint8)
        frame[y0:y1, x0:x1] = warped
