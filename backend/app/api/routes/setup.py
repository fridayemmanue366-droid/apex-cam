"""GPU self-setup — makes Apex Cam provision the realistic stack on the user's
own machine.

On a machine with an NVIDIA GPU, the user clicks "Set up realistic GPU mode" and
the app installs (into the backend's own Python) the GPU runtime + GFPGAN and
downloads the enhancer weights, then auto-uses CUDA. On this laptop it detects no
GPU and stays on the fast/CPU path — everything is wired, just dormant.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.logging import get_logger
from app.engines.face.enhancer import enhancer_models_available
from app.engines.face.neural_swap import (
    GFPGAN_FILE,
    enhancer_available,
    neural_swap_available,
)

log = get_logger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

GFPGAN_URL = (
    "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth"
)

_install: dict = {"running": False, "done": False, "ok": False, "log": []}


class SetupStatus(BaseModel):
    python: str
    has_nvidia_gpu: bool
    gpu_name: str | None
    onnx_providers: list[str]
    on_gpu: bool
    neural_available: bool
    enhancer_available: bool
    installing: bool
    install_done: bool
    install_ok: bool
    install_tail: list[str]


class EnhancerToggle(BaseModel):
    kind: str = "none"  # "none" | "gfpgan" | "codeformer"


def _nvidia_gpu_name() -> str | None:
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=4,
            ).stdout.strip()
            if out:
                return out.splitlines()[0]
        except Exception:
            pass
    # Fallback: WMIC / PowerShell video controller
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | "
             "Where-Object { $_.Name -match 'NVIDIA' } | "
             "Select-Object -First 1 -ExpandProperty Name"],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip()
        return out or None
    except Exception:
        return None


def _providers() -> list[str]:
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except Exception:
        return []


@router.get("")
def status() -> SetupStatus:
    from app.core.pipeline import pipeline

    provs = _providers()
    return SetupStatus(
        python=sys.version.split()[0],
        has_nvidia_gpu=_nvidia_gpu_name() is not None,
        gpu_name=_nvidia_gpu_name(),
        onnx_providers=provs,
        on_gpu=any("CUDA" in p or "Tensorrt" in p or "ROCM" in p for p in provs),
        neural_available=neural_swap_available(),
        enhancer_available=bool(enhancer_models_available()) or enhancer_available(),
        installing=_install["running"],
        install_done=_install["done"],
        install_ok=_install["ok"],
        install_tail=_install["log"][-12:],
    )


def _run(cmd: list[str]) -> bool:
    _install["log"].append("$ " + " ".join(cmd[-3:]))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        tail = (p.stdout + p.stderr).strip().splitlines()[-4:]
        _install["log"].extend(tail)
        return p.returncode == 0
    except Exception as exc:
        _install["log"].append(str(exc))
        return False


def _install_gpu_stack() -> None:
    """Switch the ONNX runtime to the CUDA build. Because the whole AI stack
    (inswapper swap + GFPGAN/CodeFormer enhancers) runs on ONNX, this single
    package GPU-accelerates everything — no PyTorch needed."""
    _install.update(running=True, done=False, ok=False,
                    log=["Enabling GPU acceleration (onnxruntime-gpu)…"])
    py = sys.executable
    # Replace the CPU runtime with the GPU build.
    _run([py, "-m", "pip", "uninstall", "-y", "onnxruntime"])
    ok = _run([py, "-m", "pip", "install", "--upgrade", "onnxruntime-gpu"])
    if ok:
        _install["log"].append(
            "Done. Restart the AI engine — the swap and enhancers now run on your GPU. "
            "Make sure your NVIDIA driver + CUDA are installed (see docs/GPU_SETUP.md)."
        )
    else:
        _install["log"].append("Setup failed (see log). The app keeps working on CPU.")
    _install.update(running=False, done=True, ok=ok)


@router.post("/gpu")
def install_gpu() -> dict:
    if _install["running"]:
        return {"started": False, "reason": "already running"}
    threading.Thread(target=_install_gpu_stack, daemon=True).start()
    return {"started": True}


@router.get("/enhancers")
def enhancers() -> dict:
    """Which ONNX restorers are installed (work on CPU or GPU)."""
    return {"available": enhancer_models_available()}


@router.put("/enhancer")
def set_enhancer(t: EnhancerToggle) -> dict:
    from app.core.pipeline import pipeline

    active = pipeline.set_enhancer(t.kind)
    return {"kind": t.kind, "active": active, "available": enhancer_models_available()}
