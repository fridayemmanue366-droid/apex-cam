"""System + telemetry endpoints: health, capabilities, and live metrics.

The Performance tab polls / subscribes here for FPS, latency, and GPU stats. In
Phase 1 metrics are placeholders wired to the real pipeline in later phases.
"""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "env": settings.env}


@router.get("/capabilities")
def capabilities() -> dict[str, object]:
    """What this machine/build can do — drives which UI options are enabled."""
    gpu = _detect_gpu()
    from app.core.pipeline import pipeline

    return {
        "gpu": gpu,
        "target": {
            "width": settings.target_width,
            "height": settings.target_height,
            "fps": settings.target_fps,
        },
        "responsible_use": {
            "label_output": settings.label_output,
            "audit_log": settings.audit_log,
        },
        "swap_backend": pipeline.swap_backend,
    }


@router.get("/metrics")
def metrics() -> dict[str, float]:
    """Live pipeline telemetry + GPU stats (zeros on machines without NVIDIA)."""
    from app.core.pipeline import pipeline

    stats = pipeline.stats()
    gpu_util, gpu_mem = _gpu_stats()
    return {
        "fps": round(stats.fps, 1),
        "latency_ms": round(stats.latency_ms, 1),
        "gpu_util": gpu_util,
        "gpu_mem_mb": gpu_mem,
    }


_NVIDIA_SMI = __import__("shutil").which("nvidia-smi")


def _gpu_stats() -> tuple[float, float]:
    """Query NVIDIA GPU utilization/memory. Cheap no-op on machines without one;
    on end-user GPU laptops this feeds the Performance tab live."""
    if not _NVIDIA_SMI:
        return 0.0, 0.0
    import subprocess

    try:
        out = subprocess.run(
            [_NVIDIA_SMI, "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip().splitlines()
        util, mem = out[0].split(",")
        return float(util), float(mem)
    except Exception:
        return 0.0, 0.0


def _detect_gpu() -> dict[str, object]:
    try:
        import torch  # type: ignore

        available = bool(torch.cuda.is_available())
        name = torch.cuda.get_device_name(0) if available else None
        return {"available": available, "name": name}
    except Exception:
        return {"available": False, "name": None}
