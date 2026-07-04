"""Pipeline control + live preview streaming.

The WebSocket at /ws/preview pushes JSON messages ~15x/sec while the pipeline
runs:

    {"type": "frame", "raw": "<base64 jpeg>", "out": "<base64 jpeg>",
     "fps": 29.7, "latency_ms": 12.3}

Both panes of the UI's dual preview come from here in AI mode. The ``out``
frame (enhanced + engines + AI-GENERATED badge) is the only stream external
apps will ever get via the virtual devices.
"""
from __future__ import annotations

import asyncio
import base64

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.pipeline import EnhanceSettings, pipeline

log = get_logger(__name__)
router = APIRouter(tags=["pipeline"])

PREVIEW_FPS = 15


class PipelineStatus(BaseModel):
    running: bool
    fps: float
    latency_ms: float
    frames: int
    camera_index: int
    proc_width: int
    error: str | None = None
    vcam_active: bool = False
    vcam_device: str | None = None
    vcam_error: str | None = None
    faces_detected: int = 0


class TrackingSettings(BaseModel):
    enabled: bool = True
    show_overlay: bool = True
    available: bool = True
    lip_sync: bool = True


class PerformanceSettings(BaseModel):
    # Processing width: quality/speed dial. 1280+ recommended for GPU machines;
    # 960 keeps CPU-only PCs at 30fps.
    proc_width: int = 960


class StartRequest(BaseModel):
    camera_index: int = 0


class Enhance(BaseModel):
    brightness: float = 1.12
    contrast: float = 1.1
    saturation: float = 1.08
    sharpen: float = 0.0


def _status() -> PipelineStatus:
    return PipelineStatus(**vars(pipeline.stats()))


@router.get("/pipeline")
def status() -> PipelineStatus:
    return _status()


@router.post("/pipeline/start")
def start(req: StartRequest) -> PipelineStatus:
    pipeline.start(req.camera_index)
    return _status()


@router.post("/pipeline/stop")
def stop() -> PipelineStatus:
    pipeline.stop()
    return _status()


@router.post("/reset")
def reset() -> PipelineStatus:
    """Emergency stop: force-stop the video pipeline + virtual camera, stop the
    audio pipeline + virtual microphone, and release the physical devices. Safe
    to call anytime — used by the UI's Reset button so users can recover from
    any stuck state themselves."""
    from app.core.audio_pipeline import audio_pipeline

    try:
        pipeline.disable_vcam()
        audio_pipeline.stop()
    finally:
        pipeline.stop()
    return _status()


@router.post("/vcam/start")
def vcam_start() -> PipelineStatus:
    """Go live: publish the AI output as a system camera device. Starts the
    pipeline too if it isn't running — apps only ever see the processed feed."""
    if not pipeline.stats().running:
        pipeline.start()
    pipeline.enable_vcam()
    return _status()


@router.post("/vcam/stop")
def vcam_stop() -> PipelineStatus:
    pipeline.disable_vcam()
    return _status()


@router.get("/tracking")
def get_tracking() -> TrackingSettings:
    return TrackingSettings(
        enabled=pipeline.tracking_enabled,
        show_overlay=pipeline.show_overlay,
        available=pipeline.tracker.available,
        lip_sync=pipeline.lip_sync_enabled,
    )


@router.put("/tracking")
def set_tracking(t: TrackingSettings) -> TrackingSettings:
    pipeline.tracking_enabled = t.enabled
    pipeline.show_overlay = t.show_overlay
    pipeline.lip_sync_enabled = t.lip_sync
    return get_tracking()


@router.get("/performance")
def get_performance() -> PerformanceSettings:
    return PerformanceSettings(proc_width=pipeline.proc_width)


@router.put("/performance")
def set_performance(p: PerformanceSettings) -> PerformanceSettings:
    pipeline.proc_width = max(320, min(p.proc_width, 1920))
    return PerformanceSettings(proc_width=pipeline.proc_width)


@router.get("/enhance")
def get_enhance() -> Enhance:
    e = pipeline.enhance
    return Enhance(brightness=e.brightness, contrast=e.contrast,
                   saturation=e.saturation, sharpen=pipeline.sharpen)


@router.put("/enhance")
def set_enhance(e: Enhance) -> Enhance:
    pipeline.enhance = EnhanceSettings(
        brightness=e.brightness, contrast=e.contrast, saturation=e.saturation)
    pipeline.sharpen = max(0.0, min(e.sharpen, 1.5))
    return e


@router.websocket("/ws/preview")
async def ws_preview(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            stats = pipeline.stats()
            raw, out = pipeline.previews()
            if stats.running and raw and out:
                await ws.send_json({
                    "type": "frame",
                    "raw": base64.b64encode(raw).decode(),
                    "out": base64.b64encode(out).decode(),
                    "fps": round(stats.fps, 1),
                    "latency_ms": round(stats.latency_ms, 1),
                    "faces": stats.faces_detected,
                })
            else:
                await ws.send_json({
                    "type": "idle",
                    "running": stats.running,
                    "error": stats.error,
                })
            await asyncio.sleep(1 / PREVIEW_FPS)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # client gone mid-send etc.
        log.debug("preview ws closed: %s", exc)
