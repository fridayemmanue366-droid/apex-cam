"""Model manager endpoints.

Lists the AI models EMY CAM supports and their install/load state. In Phase 2
this is a static registry so the UI's AI Models tab has real data to render;
download/load actions arrive with the engine phases (6+).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/models", tags=["models"])


class ModelInfo(BaseModel):
    id: str
    name: str
    kind: str  # "face" | "voice" | "lipsync" | "enhance"
    status: str  # "active" | "not_installed" | "planned"
    phase: int  # build phase in which it lands
    description: str


REGISTRY: list[ModelInfo] = [
    # --- Active engines (running now, CPU-friendly) ---
    ModelInfo(id="yunet", name="YuNet face tracker", kind="face", status="active",
              phase=6, description="Real-time face detection + 5 landmarks (CPU). Active."),
    ModelInfo(id="landmark-swap", name="Landmark face swap", kind="face", status="active",
              phase=7, description="Real-time landmark-based face swap with colour match. Active."),
    ModelInfo(id="pitch-vocoder", name="Phase-vocoder voice shift", kind="voice", status="active",
              phase=8, description="Real-time pitch/voice shifting (CPU). Active."),
    ModelInfo(id="proc-lipsync", name="Procedural lip-sync", kind="lipsync", status="active",
              phase=9, description="Audio-driven mouth animation (CPU). Active."),
    # --- GPU upgrades (higher fidelity, drop in on capable machines) ---
    ModelInfo(id="mediapipe", name="MediaPipe Face Mesh", kind="face", status="not_installed",
              phase=6, description="468-point dense landmarks + body pose (GPU upgrade)."),
    ModelInfo(id="insightface", name="InsightFace inswapper", kind="face", status="not_installed",
              phase=7, description="Neural face swap for higher fidelity (GPU upgrade)."),
    ModelInfo(id="gfpgan", name="GFPGAN", kind="enhance", status="not_installed",
              phase=7, description="Face restoration/enhancement after swapping (GPU upgrade)."),
    ModelInfo(id="codeformer", name="CodeFormer", kind="enhance", status="not_installed",
              phase=7, description="High-fidelity face restoration (GPU upgrade)."),
    ModelInfo(id="openvoice", name="OpenVoice", kind="voice", status="not_installed",
              phase=8, description="Neural voice cloning / tone-colour conversion (GPU upgrade)."),
    ModelInfo(id="xtts", name="XTTS", kind="voice", status="not_installed",
              phase=8, description="Multilingual voice cloning (GPU upgrade)."),
    ModelInfo(id="wav2lip", name="Wav2Lip", kind="lipsync", status="not_installed",
              phase=9, description="Phoneme-accurate neural lip-sync (GPU upgrade)."),
]


@router.get("")
def list_models() -> list[ModelInfo]:
    return REGISTRY
