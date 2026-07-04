"""Face (and body) tracking engine — Phase 6.

Detects faces per frame and exposes their bounding boxes + 5 key landmarks
(both eyes, nose tip, both mouth corners). Backed by OpenCV's YuNet DNN
detector, which is fast on CPU and needs no GPU — so it runs on the build
machine and on modest end-user laptops.

On capable machines this is where richer models slot in later (MediaPipe face
mesh for 468 landmarks, YOLO for detection, and a body-pose model for full-body
tracking). The pipeline only depends on the ``detect``/``draw_overlay``
interface here, so upgrading the backend model won't touch the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

YUNET_PATH = Path("models/face_detection_yunet_2023mar.onnx")


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h
    landmarks: list[tuple[int, int]] = field(default_factory=list)
    score: float = 0.0


class FaceTracker:
    def __init__(self) -> None:
        self._det: cv2.FaceDetectorYN | None = None
        self._size = (0, 0)
        self.available = YUNET_PATH.exists()

    def load(self) -> None:
        if self._det is not None or not self.available:
            return
        self._det = cv2.FaceDetectorYN.create(
            str(YUNET_PATH), "", (320, 320),
            score_threshold=0.7, nms_threshold=0.3, top_k=50,
        )
        log.info("Face tracker (YuNet) loaded")

    def detect(self, frame_bgr: np.ndarray) -> list[Face]:
        if not self.available:
            return []
        if self._det is None:
            self.load()
        h, w = frame_bgr.shape[:2]
        if (w, h) != self._size:
            self._det.setInputSize((w, h))
            self._size = (w, h)

        _, raw = self._det.detect(frame_bgr)
        if raw is None:
            return []

        faces: list[Face] = []
        for r in raw:
            x, y, bw, bh = (int(v) for v in r[:4])
            lms = [(int(r[4 + i * 2]), int(r[5 + i * 2])) for i in range(5)]
            faces.append(Face(box=(x, y, bw, bh), landmarks=lms, score=float(r[-1])))
        return faces

    @staticmethod
    def draw_overlay(frame_bgr: np.ndarray, faces: list[Face]) -> None:
        """Draw tracking boxes + landmarks in place (debug/preview overlay)."""
        for f in faces:
            x, y, w, h = f.box
            cv2.rectangle(frame_bgr, (x, y), (x + w, y + h), (91, 200, 91), 2)
            cv2.putText(frame_bgr, f"{f.score:.0%}", (x, max(0, y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (91, 200, 91), 1, cv2.LINE_AA)
            for i, (px, py) in enumerate(f.landmarks):
                color = (80, 80, 240) if i < 2 else (240, 160, 60)
                cv2.circle(frame_bgr, (px, py), 2, color, -1)
