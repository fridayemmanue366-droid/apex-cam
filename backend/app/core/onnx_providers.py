"""Shared ONNX Runtime execution-provider selection.

Every engine (face swap, enhancer, face parser, background matting,
LivePortrait, RVC voice) picks its provider the same way — this is the one
place that decides, so every engine stays consistent and gets new hardware
support (DirectML) without editing each engine separately.

Priority: TensorRT > CUDA > DirectML > CPU.
  - TensorRT/CUDA are fastest but need an NVIDIA GPU + matching drivers.
  - DirectML runs on *any* DirectX 12 GPU — NVIDIA, AMD, and Intel integrated
    graphics alike — which is what most customer laptops actually have. It
    ships via the `onnxruntime-directml` package (see install.ps1 /
    build-bundle.ps1), which bundles the CPU provider too, so this list is
    valid whichever onnxruntime variant is installed.
  - CPU is the universal fallback.
"""
from __future__ import annotations

_ORDER = (
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)


def get_providers() -> list[str]:
    try:
        import onnxruntime as ort

        avail = ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]
    return [p for p in _ORDER if p in avail] or ["CPUExecutionProvider"]


def on_gpu(providers: list[str]) -> bool:
    """True if the selected providers put real GPU compute first (not CPU)."""
    return bool(providers) and providers[0] != "CPUExecutionProvider"
