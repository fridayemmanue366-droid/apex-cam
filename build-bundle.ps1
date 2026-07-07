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

param([switch]$Gpu)
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
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
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

# --- 5) Backend code (no venv, no models, no caches) ------------------------
Say "Copy backend code"
$beOut = Join-Path $out "backend"
robocopy (Join-Path $root "backend") $beOut /E /NFL /NDL /NJH /NJS /NP /MT:16 `
  /XD ".venv311" "models" "__pycache__" ".pytest_cache" "data" | Out-Null

$sizeGB = [math]::Round((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum/1GB, 2)
Say "Bundle ready"
Write-Host "  $out  ($sizeGB GB)" -ForegroundColor Green
Write-Host "  Test it: run '$out\ApexCam.exe' (no system Python/Node needed)."
Write-Host "  Make the installer: build-installer.ps1 (needs Inno Setup)."
