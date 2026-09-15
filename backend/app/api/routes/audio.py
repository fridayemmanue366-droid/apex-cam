"""Virtual microphone control (Phase 5).

Mirrors the video pipeline routes: list audio devices, start/stop the audio
pipeline that publishes processed voice to the virtual mic (VB-CABLE), report
status/level, and tune the voice params. Only processed audio is published; the
raw mic is never exposed to call apps.
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.audio_pipeline import SAMPLE_RATE, audio_pipeline
from app.core.logging import get_logger

log = get_logger(__name__)

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
    if rvc.enabled:
        # Local RVC and cloud voice never run together — same rule as Pro vs
        # the local face swap elsewhere in this app.
        _cloud_tick_stop.set()
        from app.engines.voice.cloud_rvc import cloud_rvc
        cloud_rvc.enabled = False
        cloud_rvc.stop()
    return rvc_status()


@router.post("/rvc/voice")
async def import_voice(file: UploadFile) -> RVCStatus:
    """Add a trained RVC voice model (.onnx). It's saved into models/rvc/voices/
    and becomes selectable. (You train/source one .onnx per target voice.)"""
    from app.engines.voice.rvc import VOICES_DIR

    name = Path(file.filename or "voice.onnx").name
    if not name.lower().endswith(".onnx"):
        raise HTTPException(400, "Voice model must be an .onnx file")
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    (VOICES_DIR / name).write_bytes(await file.read())
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


# --- Cloud voice (Apex Pro) --------------------------------------------------
# Same split as pro.py's live/cloud: the desktop app calls OUR SERVER
# (/studio/voice/cloud/start) with the account's bearer token to mint a
# short-lived Modal token + get the wallet session id, then hands the result
# here. This machine never sees the account's real auth secret beyond the one
# bearer token it was given for the heartbeat, and never talks to the server
# except to heartbeat/stop this one session.
class CloudVoiceStart(BaseModel):
    ws_url: str
    token: str            # short-lived Modal token, from /studio/voice/cloud/start
    voice: str = "default"
    pitch_shift: int = 0
    session_id: str
    cloud_url: str        # e.g. https://apexcam-api.onrender.com
    auth: str              # this account's bearer token, for the heartbeat only


class CloudVoiceStatus(BaseModel):
    enabled: bool
    connected: bool
    error: str | None = None


_cloud_tick_stop = threading.Event()
_cloud_tick_thread: threading.Thread | None = None


def _cloud_heartbeat(session_id: str, cloud_url: str, auth: str) -> None:
    """Ping /studio/voice/cloud/tick every ~2s — mirrors pro.py's _heartbeat.
    Reports `streaming` only for audio that actually flowed since the last
    tick (cloud_rvc.consume_live()), so connecting/idle time is never billed."""
    import requests

    from app.engines.voice.cloud_rvc import cloud_rvc

    url = cloud_url.rstrip("/") + "/studio/voice/cloud/tick"
    headers = {"Authorization": f"Bearer {auth}"}
    while not _cloud_tick_stop.wait(2.0):
        if not cloud_rvc.enabled:
            break
        try:
            r = requests.post(url, headers=headers, timeout=8,
                              data={"session_id": session_id,
                                    "streaming": "true" if cloud_rvc.consume_live() else "false"})
            if r.status_code == 200 and r.json().get("stopped"):
                cloud_rvc.enabled = False
                cloud_rvc.stop()
                break
        except Exception:
            pass   # a dropped beat just isn't billed


def _cloud_status() -> CloudVoiceStatus:
    from app.engines.voice.cloud_rvc import cloud_rvc

    return CloudVoiceStatus(enabled=cloud_rvc.enabled, connected=cloud_rvc.ready,
                            error=cloud_rvc.error)


@router.get("/cloud")
def cloud_status() -> CloudVoiceStatus:
    return _cloud_status()


@router.post("/cloud/start")
def cloud_start(r: CloudVoiceStart) -> CloudVoiceStatus:
    global _cloud_tick_thread
    from app.engines.voice.cloud_rvc import cloud_rvc
    from app.engines.voice.rvc import rvc

    # Local RVC and cloud voice never run together — same rule as Pro vs the
    # local face swap elsewhere in this app.
    rvc.enabled = False
    cloud_rvc.start(r.ws_url, r.token, r.voice, max(-12, min(r.pitch_shift, 12)),
                    sample_rate=SAMPLE_RATE)
    cloud_rvc.enabled = True

    _cloud_tick_stop.set()
    if _cloud_tick_thread and _cloud_tick_thread.is_alive():
        _cloud_tick_thread.join(timeout=3.0)
    _cloud_tick_stop.clear()
    _cloud_tick_thread = threading.Thread(
        target=_cloud_heartbeat, args=(r.session_id, r.cloud_url, r.auth), daemon=True)
    _cloud_tick_thread.start()

    try:
        if not audio_pipeline.stats().running:
            audio_pipeline.start()
    except Exception:
        log.exception("cloud voice start: audio pipeline start failed")
    return _cloud_status()


@router.post("/cloud/stop")
def cloud_stop() -> CloudVoiceStatus:
    from app.engines.voice.cloud_rvc import cloud_rvc

    _cloud_tick_stop.set()
    cloud_rvc.enabled = False
    cloud_rvc.stop()
    return _cloud_status()
