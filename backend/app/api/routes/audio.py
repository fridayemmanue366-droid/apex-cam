"""Virtual microphone control (Phase 5).

Mirrors the video pipeline routes: list audio devices, start/stop the audio
pipeline that publishes processed voice to the virtual mic (VB-CABLE), report
status/level, and tune the voice params. Only processed audio is published; the
raw mic is never exposed to call apps.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.audio_pipeline import audio_pipeline

router = APIRouter(prefix="/audio", tags=["audio"])


class AudioDevice(BaseModel):
    index: int
    name: str


class AudioDevices(BaseModel):
    inputs: list[AudioDevice]
    outputs: list[AudioDevice]
    cable_output: int | None = None


class AudioStatus(BaseModel):
    running: bool
    level: float
    input_device: int | None = None
    output_device: int | None = None
    output_name: str | None = None
    error: str | None = None


class AudioStart(BaseModel):
    input_device: int | None = None
    output_device: int | None = None


class VoiceParams(BaseModel):
    enabled: bool = True
    noise_reduction: bool = True
    gain: float = 1.0
    pitch: float = 0.0


def _status() -> AudioStatus:
    return AudioStatus(**vars(audio_pipeline.stats()))


@router.get("/devices")
def devices() -> AudioDevices:
    try:
        d = audio_pipeline.list_devices()
        return AudioDevices(
            inputs=[AudioDevice(**x) for x in d["inputs"]],
            outputs=[AudioDevice(**x) for x in d["outputs"]],
            cable_output=audio_pipeline.find_cable_output(),
        )
    except Exception:
        return AudioDevices(inputs=[], outputs=[], cable_output=None)


@router.get("")
def status() -> AudioStatus:
    return _status()


@router.post("/start")
def start(req: AudioStart) -> AudioStatus:
    audio_pipeline.start(req.input_device, req.output_device)
    return _status()


@router.post("/stop")
def stop() -> AudioStatus:
    audio_pipeline.stop()
    return _status()


class RVCStatus(BaseModel):
    base_present: bool          # shared content + pitch models installed
    voices: list[str]           # available voice models
    enabled: bool
    voice: str | None = None
    pitch_shift: int = 0
    on_gpu: bool = False


class RVCSettings(BaseModel):
    enabled: bool = False
    voice: str | None = None
    pitch_shift: int = 0


@router.get("/rvc")
def rvc_status() -> RVCStatus:
    from app.engines.voice.rvc import rvc, base_models_present, list_voices

    return RVCStatus(
        base_present=base_models_present(),
        voices=list_voices(),
        enabled=rvc.enabled,
        voice=rvc.voice_name,
        pitch_shift=rvc.pitch_shift,
        on_gpu=rvc.on_gpu,
    )


@router.put("/rvc")
def set_rvc(s: RVCSettings) -> RVCStatus:
    from app.engines.voice.rvc import rvc

    if s.voice and s.voice != rvc.voice_name:
        rvc.set_voice(s.voice)
    rvc.pitch_shift = max(-12, min(s.pitch_shift, 12))
    rvc.enabled = s.enabled and rvc.ready
    return rvc_status()


@router.get("/params")
def get_params() -> VoiceParams:
    p = audio_pipeline.params
    return VoiceParams(enabled=p.enabled, noise_reduction=p.noise_reduction,
                       gain=p.gain, pitch=p.pitch)


@router.put("/params")
def set_params(p: VoiceParams) -> VoiceParams:
    ap = audio_pipeline.params
    ap.enabled = p.enabled
    ap.noise_reduction = p.noise_reduction
    ap.gain = max(0.0, min(p.gain, 4.0))
    ap.pitch = max(-12.0, min(p.pitch, 12.0))
    return get_params()
