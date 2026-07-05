# Apex Cam — Realistic (GPU) Swap Setup

The realistic face swap — which keeps **your** expressions, **blinking**, mouth
movement and head turns and only changes the identity — uses neural models that
need an **NVIDIA GPU (CUDA)**. This is the mode that looks lifelike.

> The app ships two swap engines you pick between on the **Face** tab:
> **Fast (smooth)** — the CPU landmark paste, and **Realistic (neural)** — the
> lifelike inswapper model. Realistic runs on any machine that has the models +
> `insightface`/`onnxruntime` installed, but is only **smooth** on a GPU. On an
> integrated GPU / CPU it works at a few seconds per frame (fine for a proof, a
> slideshow live). This guide makes it real-time.
>
> Status on the build laptop: the realistic engine **is installed and working**
> (Python 3.11 venv at `backend/.venv311`, models in `backend/models/`), just
> slow (~7 s/frame on Intel HD 620). Everything below is the same, minus the
> `-gpu` runtime, when you move to an NVIDIA machine.

## Requirements

- Windows/Linux with an **NVIDIA GPU** and current drivers.
- **CUDA** runtime compatible with your `onnxruntime-gpu` / `torch` build.
- **Python 3.10 or 3.11** — `insightface`, `onnxruntime(-gpu)` and `torch` do
  **not** publish wheels for Python 3.14, so the backend runs on a 3.11 venv
  (`backend/.venv311`). Windows **Smart App Control** must be off (or the AI DLLs
  signed/whitelisted) or it blocks the native libraries from loading.

## Steps

1. Create a clean Python 3.10/3.11 environment and install the base app:
   ```bash
   cd backend
   python -m venv .venv && . .venv/Scripts/activate   # PowerShell: .venv\Scripts\Activate.ps1
   pip install -e .
   ```

2. Install the GPU dependencies:
   ```bash
   pip install insightface==0.7.3 onnxruntime-gpu onnx gfpgan basicsr facexlib
   # torch matching your CUDA, e.g. CUDA 12.1:
   pip install torch --index-url https://download.pytorch.org/whl/cu121
   ```

3. Download the models and warm up:
   ```bash
   python scripts/setup_gpu.py
   ```
   This fetches `inswapper_128.onnx` into `backend/models/` and triggers the
   first-run download of InsightFace's `buffalo_l` bundle. For the optional
   sharpener, place `GFPGANv1.4.pth` in `backend/models/`.

   If the inswapper download URL is blocked, set an alternate source:
   ```bash
   set APEXCAM_INSWAPPER_URL=https://your-mirror/inswapper_128.onnx   # Windows
   ```

4. Start the backend as usual (`uvicorn app.main:app --port 8790`). On startup
   the log prints `Swap backend: neural` when everything is in place.

## What each model does

| Model                | Role                                                                 |
| -------------------- | -------------------------------------------------------------------- |
| InsightFace buffalo_l | Detects + encodes faces in the live frame and the target photo       |
| inswapper_128        | Swaps identity while preserving the driver's pose/expression/blink   |
| GFPGAN (optional)    | Sharpens/restores the swapped face for higher fidelity               |
| Wav2Lip (optional)   | Only for lips driven by *separate* audio; not needed for live talking |

## Performance

- Raise the processing resolution in the **Performance** tab (up to 1080p) for
  best quality on a strong GPU.
- TensorRT is used automatically if its ONNX Runtime provider is present.
- "Full body" here means the swapped face on **your own body** in frame (the
  webcam already captures your body); the neural swap makes the face move and
  emote naturally with you.

## Responsible use

The realistic swap makes impersonation far more convincing. The consent
acknowledgment, the always-on "AI-GENERATED" label and the audit log remain
mandatory (see docs/LEGAL.md). Only swap to faces you have permission to use.
