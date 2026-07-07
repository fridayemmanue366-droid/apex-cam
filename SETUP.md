# Installing Apex Cam on a PC (e.g. your gaming laptop)

Apex Cam is a desktop app (Electron) + an AI backend (Python) + AI models. The
installer below provisions **everything** on the target machine — best on a PC
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
