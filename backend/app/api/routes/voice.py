"""Voice engine control endpoints (expanded in Phase 8+)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/voice", tags=["voice"])


class VoiceState(BaseModel):
    enabled: bool = False
    model: str = "passthrough"
    pitch: float = 0.0
    noise_reduction: bool = True
    profile_id: str | None = None


_state = VoiceState()


@router.get("")
def get_state() -> VoiceState:
    return _state


@router.put("")
def set_state(new: VoiceState) -> VoiceState:
    global _state
    _state = new
    return _state
