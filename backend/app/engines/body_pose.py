"""Full-body pose tracking (MediaPipe BlazePose) — 33 body landmarks.

Tracks your whole body (shoulders, arms, hands, hips, legs) in real time and can
draw a skeleton overlay. This is the "full body cam" foundation — real-time on
CPU (~12 fps), faster on GPU. Also the groundwork for full-body avatar work.

Model: models/mediapipe/pose_landmarker_full.task (get via download_models.py).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

POSE_TASK = Path("models") / "mediapipe" / "pose_landmarker_full.task"

# BlazePose 33-point skeleton connections.
CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 23), (12, 24), (23, 24),
    (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (23, 25), (25, 27), (27, 29), (29, 31), (27, 31),
    (24, 26), (26, 28), (28, 30), (30, 32), (28, 32),
]


def pose_available() -> bool:
    return POSE_TASK.exists() and POSE_TASK.stat().st_size > 500_000


class BodyPoseEngine:
    def __init__(self) -> None:
        self._landmarker = None
        self.enabled = False
        self.show_skeleton = True
        self.people = 0

    @property
    def available(self) -> bool:
        return pose_available()

    def load(self) -> bool:
        if self._landmarker is not None:
            return True
        if not pose_available():
            return False
        try:
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            opts = vision.PoseLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(POSE_TASK)),
                running_mode=vision.RunningMode.IMAGE,
                num_poses=1,
            )
            self._landmarker = vision.PoseLandmarker.create_from_options(opts)
            self._mp = mp
            log.info("Body pose (MediaPipe BlazePose) loaded")
            return True
        except Exception as exc:
            log.warning("Body pose failed to load: %s", exc)
            return False

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        if not self.enabled:
            return frame_bgr
        if self._landmarker is None and not self.load():
            return frame_bgr
        try:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
            result = self._landmarker.detect(image)
            self.people = len(result.pose_landmarks)
            if self.show_skeleton:
                h, w = frame_bgr.shape[:2]
                for person in result.pose_landmarks:
                    pts = [(int(p.x * w), int(p.y * h)) for p in person]
                    for a, b in CONNECTIONS:
                        if a < len(pts) and b < len(pts):
                            cv2.line(frame_bgr, pts[a], pts[b], (80, 220, 100), 2, cv2.LINE_AA)
                    for x, y in pts:
                        cv2.circle(frame_bgr, (x, y), 3, (60, 160, 240), -1)
        except Exception as exc:
            log.debug("pose skipped: %s", exc)
        return frame_bgr


# Singleton
body_pose = BodyPoseEngine()
