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
# Override with EMYCAM_INSWAPPER_URL if the default source is unavailable.
INSWAPPER_URL = os.environ.get(
    "EMYCAM_INSWAPPER_URL",
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
        self._enhancer = None     # GFPGAN (optional)
        self._source_face = None  # target identity embedding/face
        self._loaded = False
        self.use_enhancer = use_enhancer
        self.strength = 1.0

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
            self._swapper = get_model(str(INSWAPPER_FILE), providers=providers)

            if self.use_enhancer:
                self._load_enhancer(providers)

            self._loaded = True
            log.info("Neural swap engine loaded (providers=%s)", providers)
            return True
        except Exception as exc:
            log.warning("Neural swap engine unavailable, falling back to CPU: %s", exc)
            self._loaded = False
            return False

    def _load_enhancer(self, providers: list[str]) -> None:
        try:
            from gfpgan import GFPGANer  # type: ignore

            if GFPGAN_FILE.exists():
                self._enhancer = GFPGANer(
                    model_path=str(GFPGAN_FILE), upscale=1, arch="clean",
                    channel_multiplier=2, bg_upsampler=None,
                )
                log.info("GFPGAN enhancer loaded")
        except Exception as exc:
            log.info("GFPGAN enhancer not loaded (optional): %s", exc)
            self._enhancer = None

    def set_enhancer(self, enabled: bool) -> bool:
        """Turn the GFPGAN face restorer on/off at runtime. Returns whether the
        enhancer is active afterwards."""
        self.use_enhancer = enabled
        if not enabled:
            self._enhancer = None
            return False
        if self._loaded and self._enhancer is None:
            self._load_enhancer(_providers())
        return self._enhancer is not None

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
        return True

    def swap_frame(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Detect every face in the live frame and swap identity in, preserving
        the driver's pose/expression/blink."""
        if not self.ready:
            return frame_bgr
        try:
            faces = self._app.get(frame_bgr)
            for face in faces:
                frame_bgr = self._swapper.get(frame_bgr, face, self._source_face, paste_back=True)
                if self._enhancer is not None:
                    frame_bgr = self._enhance(frame_bgr)
        except Exception as exc:
            log.debug("neural swap frame skipped: %s", exc)
        return frame_bgr

    def _enhance(self, frame_bgr: np.ndarray) -> np.ndarray:
        try:
            _, _, output = self._enhancer.enhance(
                frame_bgr, has_aligned=False, only_center_face=False, paste_back=True,
            )
            return output if output is not None else frame_bgr
        except Exception:
            return frame_bgr

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
