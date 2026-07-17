# Apex Cam - build the CONSUMER bundle (a self-contained app folder).
#
# Run on a BUILD machine (dev PC) that already has the working backend venv
# (backend\.venv311) and the desktop deps (desktop\node_modules). Produces:
#     dist-bundle\ApexCam\   -- a portable app the customer runs with NO Python,
#                               NO Node, NO PowerShell. Wrap it into a one-click
#                               setup.exe with build-installer.ps1 (Inno Setup).
#
#   powershell -ExecutionPolicy Bypass -File build-bundle.ps1          # CPU bundle
#   powershell -ExecutionPolicy Bypass -File build-bundle.ps1 -Gpu     # + onnxruntime-gpu
#
# The whole idea: Python + all AI libraries are baked in here at BUILD time (so
# the customer never installs Python or C++ build tools). Models are downloaded
# by the installer's post-install step (kept out of the bundle to stay small).

param([switch]$Gpu, [switch]$WithModels)   # -WithModels bakes the ~3GB AI models
                                            # into the bundle so customers never
                                            # download them (installer ~3GB).
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Say($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Die($m) { Write-Host $m -ForegroundColor Red; exit 1 }

$out    = Join-Path $root "dist-bundle\ApexCam"
$cache  = Join-Path $root "dist-bundle\_cache"
$venvSP = Join-Path $root "backend\.venv311\Lib\site-packages"
$node   = if (Get-Command node -ErrorAction SilentlyContinue) { (Get-Command node).Source }
          elseif (Test-Path "$env:ProgramFiles\nodejs\node.exe") { "$env:ProgramFiles\nodejs\node.exe" }
          else { $null }

if (-not (Test-Path $venvSP)) { Die "backend\.venv311 not found. Set up the dev backend first (install.ps1)." }
if (-not $node) { Die "Node.js not found (needed to build the UI on this build machine)." }

Say "Clean output"
if (Test-Path $out) {
  # Once the virtual camera is registered, browsers/YouCam LOAD its driver DLLs,
  # which locks those files and would fail the whole clean. Those DLLs never change
  # between builds, so keep the vcam folder and clear everything else.
  Get-ChildItem $out -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne "vcam" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force $out, $cache | Out-Null

# --- 1) Build the desktop UI (renderer + electron main/preload) -------------
Say "Build the desktop UI"
Push-Location (Join-Path $root "desktop")
& $node "node_modules\vite\bin\vite.js" build
if ($LASTEXITCODE -ne 0) { Pop-Location; Die "UI build failed." }
Pop-Location

# --- 2) Electron runtime -> out\ (electron.exe + resources) ------------------
Say "Copy the Electron runtime"
$edist = Join-Path $root "desktop\node_modules\electron\dist"
if (-not (Test-Path (Join-Path $edist "electron.exe"))) { Die "electron runtime missing (run npm install in desktop\)." }
robocopy $edist $out /E /NFL /NDL /NJH /NJS /NP /MT:16 | Out-Null
# Rename the launcher and drop Electron's built-in default app.
Rename-Item (Join-Path $out "electron.exe") "ApexCam.exe"
Remove-Item (Join-Path $out "resources\default_app.asar") -Force -ErrorAction SilentlyContinue

# --- Brand the exe with the Apex Cam icon (via rcedit) ---
$ico = Join-Path $root "desktop\build\apexcam.ico"
if (Test-Path $ico) {
  $rcedit = Join-Path $cache "rcedit-x64.exe"
  if (-not (Test-Path $rcedit)) {
    try {
      Invoke-WebRequest -Uri "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe" -OutFile $rcedit
    } catch { Write-Host "  (rcedit download failed; exe keeps default icon, shortcuts still branded)" }
  }
  if (Test-Path $rcedit) {
    & $rcedit (Join-Path $out "ApexCam.exe") --set-icon $ico
    Write-Host "  branded ApexCam.exe with the Apex Cam icon"
  }
  Copy-Item $ico (Join-Path $out "apexcam.ico") -Force        # for the installer shortcuts
}

# --- 3) Our app -> resources\app\ (main uses only node builtins + electron) --
Say "Stage the app"
$appdir = Join-Path $out "resources\app"
New-Item -ItemType Directory -Force $appdir | Out-Null
Copy-Item (Join-Path $root "desktop\dist") (Join-Path $appdir "dist") -Recurse
Copy-Item (Join-Path $root "desktop\dist-electron") (Join-Path $appdir "dist-electron") -Recurse
if (Test-Path (Join-Path $root "desktop\build\apexcam.ico")) {
  Copy-Item (Join-Path $root "desktop\build\apexcam.ico") (Join-Path $appdir "apexcam.ico")
}
@'
{ "name": "apexcam", "version": "1.0.0", "main": "dist-electron/main.js" }
'@ | Set-Content -Encoding utf8 (Join-Path $appdir "package.json")

# --- 4) Bundled Python (relocatable) + the AI stack -------------------------
Say "Bundle Python + AI libraries"
$pyOut = Join-Path $out "python"
$pyTar = Join-Path $cache "python-standalone.tar.gz"
if (-not (Test-Path $pyTar)) {
  $u = "https://github.com/astral-sh/python-build-standalone/releases/download/20240814/cpython-3.11.9+20240814-x86_64-pc-windows-msvc-install_only.tar.gz"
  Write-Host "Downloading relocatable Python 3.11.9..."
  Invoke-WebRequest -Uri $u -OutFile $pyTar
}
tar -xzf $pyTar -C $out            # extracts a 'python\' folder
if (-not (Test-Path (Join-Path $pyOut "python.exe"))) { Die "Python extraction failed." }
# Bake the working, already-compiled AI libraries in (same CPython 3.11 ABI, so
# the customer needs NO pip/build tools). Robocopy is fast + resumable.
Write-Host "Copying AI libraries into the bundle (~850 MB)..."
robocopy $venvSP (Join-Path $pyOut "Lib\site-packages") /E /NFL /NDL /NJH /NJS /NP /MT:16 /R:0 /W:0 | Out-Null
if ($Gpu) {
  Say "Adding GPU runtime (onnxruntime-gpu)"
  & (Join-Path $pyOut "python.exe") -m pip install --no-warn-script-location onnxruntime-gpu
}

# --- 5) Backend code (no venv, no models, no caches, NO SECRETS) ------------
# CRITICAL: the .*.local files hold OUR paid API keys (Decart/Flutterwave/fal).
# They must NEVER ship to customers — the keys live only on the cloud server.
# /XF excludes them by name so a consumer bundle can't leak them.
Say "Copy backend code (excluding secret key files)"
$beOut = Join-Path $out "backend"
robocopy (Join-Path $root "backend") $beOut /E /NFL /NDL /NJH /NJS /NP /MT:16 `
  /XD ".venv311" "models" "__pycache__" ".pytest_cache" "data" `
  /XF "*.local" ".env" "*.env" "*.db" "*.key" "*.pem" "*.log" | Out-Null
# Belt-and-suspenders: delete any secret that somehow slipped through.
Get-ChildItem $beOut -Recurse -File -Include "*.local", ".env", "*.env", "*.key", "*.pem", "*.db" `
  -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
$leak = Get-ChildItem $beOut -Recurse -File -Include "*.local", "*.key", "*.pem" -ErrorAction SilentlyContinue
if ($leak) { Die ("SECURITY: secret files still present in bundle: " + ($leak.Name -join ', ')) }
Write-Host "  verified: no secret key files in the bundle" -ForegroundColor Green

# --- 6) Apex Cam virtual camera (Unity Capture filter, MIT-licensed) --------
# Ship our OWN virtual-camera driver so customers never install OBS. The installer
# registers it (elevated, one UAC prompt) as the device "Apex Cam".
Say "Bundle the Apex Cam virtual camera (Unity Capture, MIT)"
$vcam = Join-Path $out "vcam"
New-Item -ItemType Directory -Force $vcam | Out-Null
$ucBase = "https://raw.githubusercontent.com/schellingb/UnityCapture/master/Install"
foreach ($f in @("UnityCaptureFilter64.dll", "UnityCaptureFilter32.dll")) {
  $cacheF = Join-Path $cache $f
  if (-not (Test-Path $cacheF) -or (Get-Item $cacheF).Length -lt 10000) {
    Write-Host "Downloading virtual camera driver: $f"
    try { Invoke-WebRequest -Uri "$ucBase/$f" -OutFile $cacheF }
    catch { Die "Could not download the virtual camera driver ($f). Check your connection and retry." }
  }
  # Skip re-copying if it's already there and the right size — the file may be
  # locked because the camera is registered and loaded by a browser/YouCam.
  $dst = Join-Path $vcam $f
  if ((Test-Path $dst) -and ((Get-Item $dst).Length -eq (Get-Item $cacheF).Length)) {
    Write-Host "  $f already staged (in use) - keeping it"
  } else {
    Copy-Item $cacheF $dst -Force
  }
}
# Self-elevating register/unregister scripts. ASCII (no BOM) so cmd.exe runs them.
@'
@echo off
>nul 2>&1 net session || (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b
)
cd /d "%~dp0"
regsvr32 /s "UnityCaptureFilter64.dll" "/i:UnityCaptureName=Apex Cam"
regsvr32 /s "UnityCaptureFilter32.dll" "/i:UnityCaptureName=Apex Cam"
exit /b
'@ | Set-Content -Encoding ascii (Join-Path $vcam "register-camera.bat")
@'
@echo off
>nul 2>&1 net session || (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b
)
cd /d "%~dp0"
regsvr32 /s /u "UnityCaptureFilter64.dll"
regsvr32 /s /u "UnityCaptureFilter32.dll"
exit /b
'@ | Set-Content -Encoding ascii (Join-Path $vcam "unregister-camera.bat")
@'
Apex Cam's virtual camera uses UnityCaptureFilter by Bernhard Schelling.
Licensed under the MIT License. https://github.com/schellingb/UnityCapture
'@ | Set-Content -Encoding ascii (Join-Path $vcam "LICENSE-UnityCapture.txt")
Write-Host "  virtual camera driver staged in $vcam" -ForegroundColor Green

# --- 7) Optionally bake the AI models in (so customers never download them) ----
if ($WithModels) {
  Say "Bundle the AI models (~3 GB) so no download is needed"
  $modelsSrc = Join-Path $root "backend\models"
  if (-not (Test-Path $modelsSrc)) { Die "backend\models not found — download them once with download_models.py first." }
  $modelsDst = Join-Path $beOut "models"
  robocopy $modelsSrc $modelsDst /E /NFL /NDL /NJH /NJS /NP /MT:16 | Out-Null
  $mGB = [math]::Round((Get-ChildItem $modelsDst -Recurse -File | Measure-Object Length -Sum).Sum/1GB, 2)
  Write-Host "  models baked in ($mGB GB) — installer will NOT need a download" -ForegroundColor Green
}

$sizeGB = [math]::Round((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum/1GB, 2)
Say "Bundle ready"
Write-Host "  $out  ($sizeGB GB)" -ForegroundColor Green
Write-Host "  Test it: run '$out\ApexCam.exe' (no system Python/Node needed)."
Write-Host "  Make the installer: build-installer.ps1 (needs Inno Setup)."
