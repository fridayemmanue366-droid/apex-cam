# EMY CAM — Architecture & Build Plan

This document is the source of truth for how EMY CAM is structured and the
order in which we build it. It is written so each phase produces a working,
testable increment rather than one giant drop of code.

## 1. High-level design

EMY CAM is split into two processes that run on the same machine and talk over
loopback (`127.0.0.1`):

| Process        | Tech                                   | Responsibility                                                                 |
| -------------- | -------------------------------------- | ----------------------------------------------------------------------------- |
| **Desktop app** | Electron + React + TypeScript          | UI, previews, device selection, monitors, model manager, settings, recording  |
| **AI backend**  | Python + FastAPI + PyTorch              | Face/voice/lip-sync engines, real-time media pipelines, virtual device output  |

Why two processes:

- The AI stack (PyTorch, CUDA, ONNX Runtime, TensorRT) is Python-native.
- Electron gives a fast, cross-platform native UI without fighting Python GUIs.
- A clean HTTP/WebSocket boundary keeps the UI responsive while the GPU works,
  and lets us swap either side independently.

### Transport

- **HTTP (REST)** for control: list devices, load models, change settings,
  start/stop pipelines, query status.
- **WebSocket** for streaming control + telemetry: FPS, latency, GPU stats, and
  preview frames (downscaled JPEG/RGBA) pushed to the UI.
- The heavy media path (camera → AI → virtual camera) stays **inside the backend
  process** for latency. We do *not* ship full-res frames over the socket; the UI
  only receives small preview frames.

## 2. Media pipeline

```
Webcam  ──► Capture ──► Face Engine ──► Lip-Sync ──► Composite ──► Virtual Camera
                            ▲                                          │
Mic ────► Capture ──► Voice Engine ─────────────────────────────► Virtual Microphone
                            ▲
                    (A/V sync clock)
```

- Each stage is a module with a defined interface (see `backend/app/engines`).
- Stages run on worker threads/processes; frames flow through bounded queues so a
  slow stage drops frames instead of growing latency without bound.
- A shared monotonic clock keeps audio and video aligned for lip-sync.

## 3. Backend module map

```
backend/app/
├── main.py                 # FastAPI app factory + lifespan
├── config.py               # Typed settings (env + defaults)
├── core/
│   ├── logging.py          # Structured logging setup
│   ├── clock.py            # Shared A/V timing (later phase)
│   └── pipeline.py         # Stage/queue orchestration (later phase)
├── api/routes/
│   ├── system.py           # Health, GPU/FPS/latency telemetry, devices
│   ├── face.py             # Face engine control
│   ├── voice.py            # Voice engine control
│   └── models.py           # Model manager
├── engines/
│   ├── base.py             # FrameEngine / AudioEngine abstract interfaces
│   ├── face/               # InsightFace, MediaPipe, YOLO, CodeFormer, GFPGAN
│   ├── voice/              # OpenVoice, XTTS, Fish Speech
│   └── lipsync/            # Wav2Lip
└── streaming/
    ├── virtual_camera.py   # OS virtual camera output
    └── virtual_mic.py      # OS virtual microphone output
```

## 4. Desktop module map

```
desktop/
├── electron/
│   ├── main.ts             # Electron main process; spawns/monitors backend
│   └── preload.ts          # Safe IPC bridge to renderer
└── src/
    ├── main.tsx            # React entry
    ├── App.tsx             # Tab shell
    ├── api/                # Typed backend client (REST + WS)
    ├── components/         # Shared UI (previews, monitors, controls)
    └── tabs/               # Home, Face, Voice, Camera, Audio, AIModels,
                            # Performance, Recording, Streaming, Settings
```

## 5. Virtual devices (the compatibility layer)

Compatibility with Zoom/Discord/Meet/Teams/WhatsApp Desktop/Telegram Desktop/OBS
(and any other desktop app with a camera/mic picker) is achieved **only** through
standard OS device interfaces — no per-app hooks or reverse engineering.

- **Windows (first target):**
  - Virtual camera: a DirectShow/Media Foundation virtual camera. We plan to
    build on an existing, well-supported virtual-camera backend (e.g. the OBS
    virtual camera driver / `pyvirtualcam`) rather than shipping our own kernel
    driver initially.
  - Virtual microphone: a virtual audio device (e.g. VB-CABLE style loopback) the
    backend writes processed audio into.
- **macOS / Linux (later):** CoreMediaIO / v4l2loopback + virtual audio.

This is isolated behind `streaming/virtual_camera.py` and `streaming/virtual_mic.py`
so the rest of the app never cares which OS backend is in use.

**The virtual devices carry only the processed pipeline output** (enhanced +
engines + AI-GENERATED label). The raw camera/mic feed never leaves the
pipeline process — calls on WhatsApp/Telegram/Zoom/etc. see the EMY CAM output,
never the user's real unprocessed camera.

## 6. Performance strategy

- GPU acceleration via CUDA; inference through ONNX Runtime and TensorRT where a
  model supports it.
- Multi-threaded capture/inference/output with bounded queues.
- Resolution/FPS are adaptive: target 1080p @ 30–60 FPS, degrade gracefully.
- Continuous latency + FPS + GPU telemetry surfaced in the Performance tab.
- **Full-body tracking** (stand, move, jump) uses a body-pose model alongside the
  face tracker; keeping this real-time without lag requires a CUDA GPU — the
  pipeline falls back to face-only on weaker hardware rather than dropping FPS.

## 7. Responsible-use hooks (built into the product, kept lightweight)

Per product direction we are **not** gating access behind identity verification.
Instead:

1. A one-time **consent & terms acknowledgment** on first run (see `docs/LEGAL.md`).
2. Every processed output carries an **"AI-generated" label** (on-screen badge +
   metadata tag on recordings).
3. **Local audit log** of sessions (what profile/model was active, when) so abuse
   reports can be investigated and offenders blocked/restricted.

These are implemented as ordinary app features, not access barriers.

## 8. Phase-by-phase build plan

| Phase | Deliverable                                   | Status   |
| ----- | --------------------------------------------- | -------- |
| 1     | Project architecture & scaffolding            | current  |
| 2     | Desktop UI shell (all tabs, previews, monitors) | next   |
| 3     | AI backend skeleton (engine interfaces, pipeline) |      |
| 4     | Virtual camera output                         | done     |
| 5     | Virtual microphone output                     | done     |
| 6     | Face & body tracking (detection, landmarks, head pose, full-body pose) |  |
| 7     | Face swapping / reenactment (full-body aware) | done     |
| 8     | Voice conversion / cloning                    | done     |
| 9     | Lip sync                                       | done     |
| 10    | Optimization (TensorRT/ONNX) & packaging      |          |

Each phase ends with something runnable and tests for the new surface.
