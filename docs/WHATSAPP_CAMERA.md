# WhatsApp-compatible virtual camera (Media Foundation) — build plan

**Status:** planned, to build on the GPU laptop (native component; can't be
compiled/registered on the CPU dev box).

## Problem
WhatsApp Desktop (and Chromium/UWP apps) only list cameras that register as
**physical devices** via the Windows **Media Foundation** Camera Frame Server.
Our current output is **OBS Virtual Camera via pyvirtualcam = DirectShow**, which
WhatsApp does **not** see. YouCam works because it ships a Media Foundation
virtual camera.

## Solution
Register our own virtual camera with the Windows 11 **`MFCreateVirtualCamera`**
API (Win 11 build 22000+). A camera created this way appears as a real device, so
WhatsApp, Chromium, Teams, Zoom, etc. all see it — fully ours, no third-party dep.

## Reference implementations
- `smourier/VCamNetSample` — **.NET 9 AOT** (compiles to a single self-contained
  .exe, no runtime). **Preferred** — closest to our stack, easiest to ship.
- `smourier/VCamSample` — C++ reference.
- `qing-wang/MFVCamSource` — MF-based C++.

## Architecture
1. Native helper (`apexcam-vcam.exe`, .NET AOT) that:
   - calls `MFCreateVirtualCamera` to create/register "Apex Cam" as a virtual camera;
   - implements the frame source and reads processed frames from the Python
     pipeline over **shared memory** (or a named pipe) — the same BGR frames we
     currently push to pyvirtualcam.
2. Python side: add an `mf` backend to `app/streaming/virtual_camera.py` that,
   instead of pyvirtualcam, writes each frame into the shared-memory buffer the
   helper reads. Pick backend by OS/availability; keep pyvirtualcam as fallback.
3. Launcher/installer: build the helper (`dotnet publish -c Release`), and
   register the camera on first run (may need admin once).

## Product rule (unchanged)
Only the processed, "AI-generated"-labeled output flows through the vcam — never
the raw camera.

## Fast fallback (if ever needed)
Route frames into **DroidCam Virtual Output** (drivers already in
`C:\full body cam`) — WhatsApp sees it — but that depends on DroidCam being
installed, so it's a stopgap, not the shipped solution.

## Sources
- OBS forum: WhatsApp Desktop doesn't work with OBS Virtual Camera
- smourier/VCamNetSample, smourier/VCamSample, qing-wang/MFVCamSource (GitHub)
