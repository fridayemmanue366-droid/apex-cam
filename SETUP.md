# Installing Apex Cam on a PC (e.g. your gaming laptop)

Apex Cam is a desktop app (Electron) + an AI backend (Python) + AI models.

## Two ways to install

**A) For customers — the one-click installer (recommended).**
Ship them **`ApexCam-Setup.exe`** (built by `build-bundle.ps1` then
`build-installer.ps1`). They double-click it → agree to terms → Next → done.
**No Python, no Node, no PowerShell, no build tools** — a private Python with all
AI libraries is baked inside. See "Building the installer" at the bottom.

**B) For developers / your own testing — the script below.**
`install.ps1` provisions everything from source on the machine (installs Python
3.11, Node, the AI libraries, models, drivers). Use this on your dev/GPU box.

---

The script (B) provisions **everything** on the target machine — best on a PC
with an **NVIDIA GPU** for smooth real-time AI.

## Steps

1. **Copy this whole folder** to the target PC (USB, network, or `git clone`).
   Include `backend/`, `desktop/`, `install.ps1`, `Apex Cam.bat`.
   *(You don't need to copy `backend/.venv311` — the installer rebuilds it. You
   can copy `backend/models/` to skip the ~2 GB model download.)*

2. **Run the installer** (PowerShell, in this folder):
   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```
   It will:
   - ask you to **agree to the terms** (consent),
   - detect your **GPU** and install GPU acceleration if present,
   - warn about **Smart App Control** (turn it off if models fail to load),
   - install **Python 3.11**, the backend + **all AI libraries**,
   - download **all models** (~2 GB — use **WiFi**, not mobile data),
   - build the **desktop app**,
   - offer to install the **virtual camera/mic drivers** (OBS + VB-CABLE),
   - **verify** the install at the end.

   The installer is **safe to re-run** — it detects and skips finished steps, so
   if something fails (e.g. no internet), just fix it and run it again.

3. **Launch:** double-click **`Apex Cam.bat`**.

4. In **Zoom / Google Meet / Microsoft Teams / Discord / OBS**, pick
   **"OBS Virtual Camera"** as the camera and **"CABLE Output"** as the microphone.

## Requirements
- Windows 10/11.
- **winget** (the "App Installer", preinstalled on current Windows) so the script
  can auto-install Python and Node. If it's missing, install Python 3.11 and
  Node.js LTS yourself first, then run the script.
- **NVIDIA GPU + current driver** for real-time neural swap / avatar (works on
  CPU too, but those features are slow).
- Internet for the first install (Python, libraries, models).

## Notes
- **C++ Build Tools:** the face engine (`insightface`) may need Microsoft's free
  **C++ Build Tools** to install. If the installer stops and asks for them, get
  them from <https://visualstudio.microsoft.com/visual-cpp-build-tools/> (tick
  "Desktop development with C++"), then re-run the installer.
- **Smart App Control** (Windows 11) can block the unsigned AI libraries. If the
  app can't load the AI models, turn it off: *Windows Security -> App & browser
  control -> Smart App Control -> Off* (this is a one-way change).
- **WhatsApp Desktop:** it currently does **not** list virtual cameras (it only
  shows physical cameras), so Apex Cam won't appear there yet. It works in Zoom,
  Meet, Teams, Discord and OBS. (A WhatsApp-compatible camera is planned later.)
- Everything runs **locally** — no video/audio leaves the PC (the optional
  Apex Pro cloud tier is separate and opt-in).
- All output is labelled **AI-GENERATED** (see `docs/LEGAL.md`).

## Building the installer (for you, to ship to customers)

Do this on a **build PC** that already has the dev backend set up
(`backend\.venv311` from `install.ps1`) and `desktop\node_modules` (from
`npm install`):

```powershell
powershell -ExecutionPolicy Bypass -File build-bundle.ps1       # or -Gpu for GPU accel
powershell -ExecutionPolicy Bypass -File build-installer.ps1
```

- `build-bundle.ps1` bakes a private Python 3.11 + all AI libraries + the built
  app into `dist-bundle\ApexCam\` (~1.2 GB).
- `build-installer.ps1` compiles that into **`dist-bundle\ApexCam-Setup.exe`**
  (~415 MB) — the single file you send to customers.
- Models are **not** in the installer; it offers to download them (~2 GB) at the
  end of install, so the download is one-time and on the customer's machine.
