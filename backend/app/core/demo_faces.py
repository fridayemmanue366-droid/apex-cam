"""Built-in demo face profiles.

Ships a few synthetic avatar faces so every user can try face swapping for free
without uploading anything (and so we can test the pipeline). Generated
programmatically — no real person's likeness involved. Created once on startup;
users can add their own faces alongside but cannot delete the built-ins.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

BUILTIN_DIR = Path("data/builtin_profiles")

# (id, name, background BGR, skin BGR, hair BGR)
_DEMOS = [
    ("builtin-nova", "Nova (demo)", (196, 160, 96), (150, 190, 235), (40, 35, 30)),
    ("builtin-zed", "Zed (demo)", (120, 110, 220), (120, 165, 215), (25, 60, 110)),
    ("builtin-aria", "Aria (demo)", (150, 200, 140), (135, 175, 225), (60, 40, 130)),
]

SIZE = 512
_C = SIZE // 2

# Known landmark geometry for the drawn faces, in YuNet's 5-point order:
# [right-eye, left-eye, nose, right-mouth-corner, left-mouth-corner] (image
# coords). Because we draw the faces we know these exactly, so they work as swap
# targets without the DNN detector needing to recognise a stylised drawing.
DEMO_LANDMARKS = [
    (_C - 60, _C - 20),  # right eye (left side of image)
    (_C + 60, _C - 20),  # left eye
    (_C - 6, _C + 20),   # nose tip
    (_C - 52, _C + 85),  # right mouth corner
    (_C + 52, _C + 85),  # left mouth corner
]


def _draw_face(bg: tuple, skin: tuple, hair: tuple) -> np.ndarray:
    img = np.full((SIZE, SIZE, 3), bg, dtype=np.uint8)
    c = _C

    # soft vertical shading on the skin for a little depth
    face = np.zeros_like(img)
    cv2.ellipse(face, (c, c), (160, 195), 0, 0, 360, skin, -1)
    shade = np.linspace(0.82, 1.08, SIZE)[:, None, None]
    face = np.clip(face.astype(np.float32) * shade, 0, 255).astype(np.uint8)

    cv2.ellipse(img, (c, c - 30), (185, 205), 0, 0, 360, hair, -1)   # hair
    mask = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY) > 0
    img[mask] = face[mask]
    cv2.ellipse(img, (c, c - 135), (165, 95), 0, 180, 360, hair, -1)  # fringe

    for dx in (-60, 60):  # eyes
        cv2.ellipse(img, (c + dx, c - 20), (30, 20), 0, 0, 360, (250, 250, 250), -1)
        cv2.circle(img, (c + dx, c - 18), 11, (70, 50, 40), -1)
        cv2.circle(img, (c + dx, c - 18), 5, (20, 15, 12), -1)
        cv2.circle(img, (c + dx + 3, c - 22), 3, (255, 255, 255), -1)
        cv2.ellipse(img, (c + dx, c - 52), (30, 12), 0, 200, 340, hair, 5)  # brow
    # nose
    cv2.line(img, (c, c - 10), (c - 8, c + 18), tuple(int(v * 0.82) for v in skin), 4)
    cv2.ellipse(img, (c - 3, c + 20), (12, 7), 0, 200, 340,
                tuple(int(v * 0.75) for v in skin), -1)
    # lips
    cv2.ellipse(img, (c, c + 85), (52, 22), 0, 0, 360, (95, 75, 165), -1)
    cv2.line(img, (c - 52, c + 85), (c + 52, c + 85), (70, 55, 130), 2)
    return img


def ensure_demo_faces() -> None:
    BUILTIN_DIR.mkdir(parents=True, exist_ok=True)
    for pid, name, bg, skin, hair in _DEMOS:
        img_path = BUILTIN_DIR / f"{pid}.png"
        meta_path = BUILTIN_DIR / f"{pid}.json"
        # Always (re)write so upgrades to the artwork/landmarks take effect.
        cv2.imwrite(str(img_path), _draw_face(bg, skin, hair))
        meta_path.write_text(json.dumps({
            "id": pid, "name": name, "created": 0, "builtin": True,
            "landmarks": DEMO_LANDMARKS,
        }))
