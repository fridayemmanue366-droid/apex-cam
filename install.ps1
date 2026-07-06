# Apex Cam — one-command installer for Windows (GPU or CPU).
#
# Copy this whole folder to the target PC and run in PowerShell:
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# It provisions EVERYTHING: Python 3.11, the backend venv + AI libraries,
# GPU acceleration (if an NVIDIA card is found), all models, the desktop app,
# and the virtual camera/mic drivers. Then launch with "Apex Cam.bat".

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Say($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }

Say "Apex Cam installer"
Write-Host "This installs the app and its AI models on this PC."

# --- consent ---
$ok = Read-Host "Do you agree to the Terms (only swap faces/voices you have permission to use; output is labelled AI-generated)? [y/N]"
if ($ok -ne "y") { Write-Host "Cancelled."; exit }

# --- detect GPU ---
$gpu = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1 -ExpandProperty Name)
if ($gpu) { Say "NVIDIA GPU detected: $gpu (real-time AI enabled)" }
else { Say "No NVIDIA GPU — the app runs on CPU (heavy features will be slow)" }

# --- Smart App Control note ---
Say "Windows Smart App Control"
Write-Host "The AI libraries are unsigned and Smart App Control (Win 11) may block them."
Write-Host "If install fails to load models, turn it OFF:"
Write-Host "  Windows Security -> App & browser control -> Smart App Control -> Off"
Read-Host "Press Enter to continue"

# --- Python 3.11 ---
Say "Python 3.11"
$py = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $py)) {
  Write-Host "Installing Python 3.11 via winget..."
  winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
}
if (-not (Test-Path $py)) { $py = (Get-Command python).Source }
& $py --version

# --- backend venv + deps ---
Say "Backend environment (AI libraries)"
Set-Location "$root\backend"
& $py -m venv .venv311
$venv = "$root\backend\.venv311\Scripts\python.exe"
& $venv -m pip install --upgrade pip -q
& $venv -m pip install fastapi "uvicorn[standard]" pydantic-settings python-multipart `
    sounddevice pyvirtualcam websockets numpy opencv-python insightface mediapipe
if ($gpu) {
  Say "Installing GPU acceleration (onnxruntime-gpu)"
  & $venv -m pip install onnxruntime-gpu
} else {
  & $venv -m pip install onnxruntime
}

# --- models ---
Say "AI models"
if (Test-Path "$root\backend\models\inswapper_128.onnx") {
  Write-Host "Models already present."
} else {
  Write-Host "Downloading models (~2 GB)..."
  & $venv scripts\download_models.py --all
}

# --- desktop app ---
Say "Desktop app"
$node = (Get-Command node -ErrorAction SilentlyContinue)
if (-not $node) { winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements }
Set-Location "$root\desktop"
npm install --no-fund --no-audit
npm run build 2>$null; if ($LASTEXITCODE -ne 0) { npx vite build }

# --- virtual devices ---
Say "Virtual camera + microphone drivers"
Write-Host "Apex Cam needs OBS Virtual Camera + VB-CABLE to appear as a camera/mic in call apps."
$dev = Read-Host "Install them now (free)? [y/N]"
if ($dev -eq "y") {
  winget install --id OBSProject.OBSStudio -e --silent --accept-package-agreements --accept-source-agreements
  Write-Host "Install VB-CABLE from https://vb-audio.com/Cable (run the installer as admin)."
}

Say "Done!"
Write-Host "Launch Apex Cam with:  Apex Cam.bat" -ForegroundColor Green
Set-Location $root
