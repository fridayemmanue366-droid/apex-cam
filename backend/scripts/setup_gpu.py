"""One-shot GPU setup helper for Apex Cam's realistic swap.

Run on a GPU machine (Python 3.10/3.11) after installing requirements-gpu.txt:

    python scripts/setup_gpu.py

It checks the CUDA provider, downloads the inswapper model, warms up InsightFace
(which downloads the buffalo_l detector/recogniser bundle on first use), and
reports whether the neural swap is ready.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.face.neural_swap import (  # noqa: E402
    NeuralFaceSwapEngine,
    gpu_available,
    neural_swap_available,
)


def main() -> int:
    print("== Apex Cam GPU setup ==")

    try:
        import onnxruntime as ort
        print("onnxruntime providers:", ort.get_available_providers())
    except Exception as exc:
        print("onnxruntime not installed:", exc)
        print("Install: pip install -r requirements-gpu.txt")
        return 1

    print("CUDA available:", gpu_available())
    if not gpu_available():
        print("WARNING: no CUDA provider. The neural swap will be very slow on CPU.")

    print("Downloading inswapper model (if missing)...")
    if not NeuralFaceSwapEngine.download_model():
        print("Could not download inswapper_128.onnx.")
        print("Set APEXCAM_INSWAPPER_URL to a reachable copy and retry, or place")
        print("the file at backend/models/inswapper_128.onnx manually.")
        return 1

    print("Warming up InsightFace (downloads buffalo_l on first run)...")
    engine = NeuralFaceSwapEngine()
    if not engine.load():
        print("InsightFace failed to load — check the install and CUDA setup.")
        return 1

    print("neural_swap_available:", neural_swap_available())
    print("SUCCESS: realistic neural swap is ready. Start the backend normally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
