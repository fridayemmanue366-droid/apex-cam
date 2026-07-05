# Apex Cam

Real-time AI face transformation and AI voice transformation for the desktop.

Apex Cam processes your webcam and microphone in real time and exposes a
**virtual camera** and **virtual microphone** using standard operating-system
device interfaces, so any app that lets you pick a camera/mic (Zoom, Discord,
Google Meet, Microsoft Teams, OBS, etc.) can use the processed stream.

> Compatibility comes only from standard OS camera/microphone interfaces.
> Apex Cam does **not** reverse-engineer or hook into any specific messaging
> platform.

## Architecture at a glance

```text
+-------------------------------------------------------------+
|  Desktop app (Electron + React + TypeScript)                |
|  - Tabbed UI, previews, model manager, monitors             |
|  - Talks to backend over local HTTP + WebSocket             |
+-----------------------------+-------------------------------+
                              |  ws:// + http://127.0.0.1
+-----------------------------v-------------------------------+
|  AI backend (Python, FastAPI, PyTorch)                      |
|  - Face engine / Voice engine / Lip-sync engine             |
|  - Frame + audio pipelines (GPU accelerated)                |
+------------+----------------------------+-------------------+
             |                            |
   +---------v---------+        +---------v---------+
   | Virtual Camera    |        | Virtual Microphone|
   | (OS device)       |        | (OS device)       |
   +-------------------+        +-------------------+
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design and the
phase-by-phase build plan.

## Responsible use

Apex Cam can alter how you look and sound. Using it to impersonate a real
person without their permission may be illegal. Every output is labeled as
AI-generated and use is governed by [docs/LEGAL.md](docs/LEGAL.md). Reports of
misuse can lead to blocking/restriction.

## Status

Phase 1 (project architecture & scaffolding) — in progress.

## Development

Requirements: Python 3.11+, Node 20+, and (recommended) an NVIDIA GPU with CUDA
for real-time performance.

```bash
# Backend
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[dev]
uvicorn app.main:app --reload --port 8790

# Desktop (in a second terminal)
cd desktop
npm install
npm run dev
```

Or use the Docker dev environment: `docker compose up`.
