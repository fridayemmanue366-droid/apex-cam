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
   - download **all models** (or use the ones you copied),
   - build the **desktop app**,
   - offer to install the **virtual camera/mic drivers** (OBS + VB-CABLE).

3. **Launch:** double-click **`Apex Cam.bat`**.

4. In Zoom / WhatsApp / Teams / etc., pick **"OBS Virtual Camera"** as the camera
   and **"CABLE Output"** as the microphone.

## Requirements
- Windows 10/11.
- **NVIDIA GPU + current driver** for real-time neural swap / avatar (works on
  CPU too, but those features are slow).
- Internet for the first install (Python, libraries, models).

## Notes
- **Smart App Control** (Windows 11) can block the unsigned AI libraries. If the
  app can't load the AI models, turn it off: *Windows Security → App & browser
  control → Smart App Control → Off* (this is a one-way change).
- Everything runs **locally** — no video/audio leaves the PC (the optional
  Apex Pro cloud tier is separate and opt-in).
- All output is labelled **AI-GENERATED** (see `docs/LEGAL.md`).
