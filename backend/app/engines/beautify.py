"""Studio Beautify — make any webcam look cinematic and HD.

A real-time "look great" pass so every user appears polished even on a cheap or
grainy camera: balanced cinematic exposure, noise clean-up, smooth natural skin
(detail-preserving, not plastic), a warm grade and crisp sharpening. Targets the
face/skin for smoothing while keeping eyes, brows and lips sharp.

Pure OpenCV — fast, runs real-time on CPU, no model needed.
"""
from __future__ import annotations

import cv2
import numpy as np


class BeautifyEngine:
    def __init__(self) -> None:
        self.level = 0.0          # 0..1 overall strength
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def process(self, frame_bgr: np.ndarray, faces=None) -> np.ndarray:
        if self.level <= 0.01:
            return frame_bgr
        lv = float(np.clip(self.level, 0, 1))
        out = frame_bgr

        # 1) Cinematic auto-tone + studio fill-light: adaptive contrast, then lift
        #    shadows so harsh/dim lighting looks soft and evenly lit.
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l = self._clahe.apply(lab[:, :, 0])
        lab[:, :, 0] = cv2.addWeighted(lab[:, :, 0], 1 - 0.6 * lv, l, 0.6 * lv, 0)
        lf = lab[:, :, 0].astype(np.float32)
        fill = (255.0 - lf) / 255.0                 # how dark each pixel is
        lf = lf + (0.28 * lv) * fill * fill * 120   # lift shadows (studio fill)
        lab[:, :, 0] = np.clip(lf, 0, 255).astype(np.uint8)
        out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        if lv > 0:  # subtle warm grade
            out = out.astype(np.float32)
            out[:, :, 2] = np.clip(out[:, :, 2] * (1 + 0.04 * lv), 0, 255)   # R up
            out[:, :, 0] = np.clip(out[:, :, 0] * (1 - 0.03 * lv), 0, 255)   # B down
            out = out.astype(np.uint8)

        # 2) Skin smoothing (detail-preserving frequency separation) on faces.
        out = self._smooth_skin(out, faces, lv)

        # 3) Crisp HD sharpening on structure (eyes/edges pop).
        blur = cv2.GaussianBlur(out, (0, 0), sigmaX=1.1)
        out = cv2.addWeighted(out, 1 + 0.5 * lv, blur, -0.5 * lv, 0)
        return out

    def _smooth_skin(self, img: np.ndarray, faces, lv: float) -> np.ndarray:
        h, w = img.shape[:2]
        # Build a soft mask over face regions (or the whole frame if unknown).
        mask = np.zeros((h, w), np.float32)
        if faces:
            for f in faces:
                box = getattr(f, "box", None)
                if box is None and hasattr(f, "bbox"):
                    x1, y1, x2, y2 = [int(v) for v in f.bbox]
                    box = (x1, y1, x2 - x1, y2 - y1)
                if box is None:
                    continue
                x, y, bw, bh = box
                cx, cy = x + bw // 2, y + bh // 2
                cv2.ellipse(mask, (cx, cy), (int(bw * 0.7), int(bh * 0.85)),
                            0, 0, 360, 1.0, -1)
            mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(w, h) * 0.02)
        else:
            mask[:] = 0.5  # mild global smoothing if no face info

        if mask.max() <= 0:
            return img

        # Edge-preserving smooth (bilateral) = clean skin without blurring features.
        smooth = cv2.bilateralFilter(img, d=7, sigmaColor=45, sigmaSpace=7)
        # Add back high-frequency detail so it doesn't look plastic.
        detail = cv2.subtract(img, cv2.GaussianBlur(img, (0, 0), sigmaX=2))
        smooth = cv2.add(smooth, (detail * 0.4).astype(np.uint8))

        a = (mask * (0.75 * lv))[..., None]
        return (smooth.astype(np.float32) * a + img.astype(np.float32) * (1 - a)).astype(np.uint8)


# Singleton
beautify = BeautifyEngine()
