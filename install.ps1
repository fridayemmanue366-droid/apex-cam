# Apex Cam - one-command installer for Windows (GPU or CPU).
#
# Copy this whole folder to the target PC and run in PowerShell:
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# It provisions EVERYTHING: Python 3.11, the backend venv + AI libraries,
# GPU acceleration (if an NVIDIA card is found), all models, the desktop app,
# and the virtual camera/mic drivers. Then launch with "Apex Cam.bat".
#
# Safe to re-run: existing steps are detected and skipped.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Say($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Die($m) { Write-Host $m -ForegroundColor Red; Read-Host "Press Enter to exit"; exit 1 }

Say "Apex Cam installer"
Write-Host "This installs the app and its AI models on this PC."

# --- consent ---
$ok = Read-Host "Do you agree to the Terms (only swap faces/voices you have permission to use; output is labelled AI-generated)? [y/N]"
if ($ok -notmatch '^(y|yes)$') { Write-Host "Cancelled."; exit }

# --- winget present? (needed to auto-install Python/Node/OBS) ---
$haveWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
if (-not $haveWinget) {
  Warn "winget (App Installer) not found. I can't auto-install prerequisites."
  Warn "Install 'App Installer' from the Microsoft Store, or install Python 3.11"
  Warn "and Node.js LTS manually, then re-run this script."
}

# --- detect GPU ---
$gpu = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1 -ExpandProperty Name)
if ($gpu) { Say "NVIDIA GPU detected: $gpu (real-time AI enabled)" }
else { Say "No NVIDIA GPU - the app runs on CPU (heavy features will be slow)" }

# --- Smart App Control note ---
Say "Windows Smart App Control"
Write-Host "The AI libraries are unsigned and Smart App Control (Win 11) may block them."
Write-Host "If install fails to load models, turn it OFF:"
Write-Host "  Windows Security -> App & browser control -> Smart App Control -> Off"
Read-Host "Press Enter to continue"

# --- locate a REAL Python 3.11 (never the Windows Store stub) ---
function Find-Py311 {
  $candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:ProgramFiles\Python311\python.exe",
    "${env:ProgramFiles(x86)}\Python311\python.exe",
    "C:\Python311\python.exe"
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  # py launcher is the most reliable way to pin 3.11
  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    try { $p = (& py -3.11 -c "import sys;print(sys.executable)" 2>$null); if ($p -and (Test-Path $p)) { return $p } } catch {}
  }
  return $null
}

Say "Python 3.11"
$py = Find-Py311
if (-not $py) {
  if ($haveWinget) {
    Write-Host "Installing Python 3.11 via winget..."
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
    $py = Find-Py311
  }
}
if (-not $py) { Die "Python 3.11 not found after install. Install it from python.org (3.11.x), tick 'Add to PATH', then re-run." }
& $py --version
if ($LASTEXITCODE -ne 0) { Die "The Python at '$py' didn't run (possibly the Windows Store stub). Install real Python 3.11 from python.org and re-run." }

# --- backend venv + deps ---
Say "Backend environment (AI libraries)"
Set-Location "$root\backend"
$venv = "$root\backend\.venv311\Scripts\python.exe"
if (-not (Test-Path $venv)) {
  & $py -m venv .venv311
}
if (-not (Test-Path $venv)) { Die "Failed to create the Python virtual environment." }
& $venv -m pip install --upgrade pip -q

# Pinned, known-good all-ONNX stack (no PyTorch needed). numpy<2 keeps insightface
# + onnxruntime happy. huggingface_hub[hf_xet] so HuggingFace's Xet CDN downloads
# don't truncate.
& $venv -m pip install `
    "numpy==1.26.4" `
    fastapi "uvicorn[standard]" pydantic-settings python-multipart `
    sounddevice pyvirtualcam websockets `
    "huggingface_hub[hf_xet]" `
    aiortc msgpack livekit requests `
    "opencv-python==4.10.0.84" mediapipe
if ($LASTEXITCODE -ne 0) { Die "Core Python dependencies failed to install. Check your internet connection and re-run." }

# insightface needs a C++ compiler to build on Windows. Try it; if it fails, tell
# the user exactly what to install rather than dying with a cryptic error.
Write-Host "Installing insightface (face engine)..."
& $venv -m pip install insightface
if ($LASTEXITCODE -ne 0) {
  Warn "insightface failed to build. It needs the free 'Microsoft C++ Build Tools'."
  Warn "  1) Install from https://visualstudio.microsoft.com/visual-cpp-build-tools/"
  Warn "     (select 'Desktop development with C++'), then"
  Warn "  2) re-run this installer."
  Die "Stopping so you can install the C++ Build Tools."
}

if ($gpu) {
  Say "Installing GPU acceleration (onnxruntime-gpu)"
  & $venv -m pip install onnxruntime-gpu
} else {
  & $venv -m pip install onnxruntime
}
if ($LASTEXITCODE -ne 0) { Die "onnxruntime failed to install." }

# --- models ---
Say "AI models"
if (Test-Path "$root\backend\models\inswapper_128.onnx") {
  Write-Host "Models already present."
} else {
  Write-Host "Downloading models (~2 GB) - use WiFi, not mobile data..."
  & $venv scripts\download_models.py --all
}

# --- desktop app ---
Say "Desktop app"
function Find-Node {
  if (Get-Command node -ErrorAction SilentlyContinue) { return (Get-Command node).Source }
  foreach ($c in @("$env:ProgramFiles\nodejs\node.exe",
                   "${env:ProgramFiles(x86)}\nodejs\node.exe",
                   "$env:LOCALAPPDATA\Programs\nodejs\node.exe")) {
    if (Test-Path $c) { return $c }
  }
  return $null
}
$node = Find-Node
if (-not $node) {
  if ($haveWinget) {
    Write-Host "Installing Node.js LTS via winget..."
    winget install --id OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements
    $node = Find-Node
  }
}
if (-not $node) { Die "Node.js not found after install. Install Node.js LTS from nodejs.org and re-run." }
$npm = Join-Path (Split-Path $node) "npm.cmd"

Set-Location "$root\desktop"
& $npm install --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { Die "npm install failed. Check your internet connection and re-run." }
# Build only the RUNNABLE app (renderer + electron main/preload). We deliberately
# skip electron-builder packaging: it needs symlink privilege (Developer Mode) and
# isn't required - the app runs from this folder via "Apex Cam.bat".
& $node "node_modules\vite\bin\vite.js" build
if ($LASTEXITCODE -ne 0) { Die "Building the desktop app failed." }

# --- virtual devices ---
Say "Virtual camera + microphone drivers"
Write-Host "Apex Cam needs OBS Virtual Camera + VB-CABLE to appear as a camera/mic in call apps."
$dev = Read-Host "Install them now (free)? [y/N]"
if ($dev -match '^(y|yes)$') {
  if ($haveWinget) {
    winget install --id OBSProject.OBSStudio -e --silent --accept-package-agreements --accept-source-agreements
  } else {
    Warn "winget missing - install OBS Studio manually from https://obsproject.com (gives the OBS Virtual Camera)."
  }
  Write-Host "Install VB-CABLE from https://vb-audio.com/Cable (run the installer as admin) for the virtual mic."
}

# --- verify ---
Say "Verifying the install"
& $venv -c "import cv2, numpy, insightface, onnxruntime; print('AI libraries import OK')"
if ($LASTEXITCODE -ne 0) { Warn "Some AI libraries didn't import cleanly - see the message above (Smart App Control?)." }
if (Test-Path "$root\desktop\dist-electron\main.js") { Write-Host "Desktop app built OK." }
else { Warn "Desktop app build output missing - try re-running." }

Say "Done!"
Write-Host "Launch Apex Cam with:  Apex Cam.bat" -ForegroundColor Green
Set-Location $root
