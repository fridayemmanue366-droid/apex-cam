# Apex Cam - start BOTH servers for local development, then the app.
#
#   powershell -ExecutionPolicy Bypass -File run-dev.ps1
#
# Apex Cam needs two services running:
#   127.0.0.1:8790  local AI backend (camera, swap, voice, local Pro engine)
#   127.0.0.1:8900  cloud server     (accounts, credits, payments, Decart proxy)
# Sign-in talks to 8900 - if it's not running you get "failed to fetch".

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root "backend\.venv311\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "Run install.ps1 first." -ForegroundColor Red; exit 1 }

function Alive($url) {
  try { Invoke-RestMethod $url -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

# --- local AI backend (8790) ---
if (Alive "http://127.0.0.1:8790/health") {
  Write-Host "backend 8790 already running" -ForegroundColor DarkGray
} else {
  Write-Host "starting backend on 8790..." -ForegroundColor Cyan
  Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--port","8790" `
    -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
}

# --- cloud server (8900) ---
if (Alive "http://127.0.0.1:8900/health") {
  Write-Host "cloud 8900 already running" -ForegroundColor DarkGray
} else {
  Write-Host "starting cloud server on 8900..." -ForegroundColor Cyan
  # Secrets stay out of the repo: read the gitignored dev key files.
  $decart = (Select-String -Path (Join-Path $root "backend\.decart.local") -Pattern "APEXCAM_DECART_KEY=(.*)").Matches.Groups[1].Value
  $flw = ""
  $flwFile = Join-Path $root "backend\.flutterwave.local"
  if (Test-Path $flwFile) {
    $flw = (Select-String -Path $flwFile -Pattern "FLW_SECRET=(.*)").Matches.Groups[1].Value
  }
  $env:APEXCAM_SECRET = "apexcam-dev-secret-please-change"
  $env:APEXCAM_DB = Join-Path $root "server\apexcam-dev.db"
  $env:APEXCAM_DECART_KEY = $decart
  $env:FLW_SECRET = $flw
  $env:APEXCAM_PUBLIC_URL = "http://127.0.0.1:8900"
  Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--port","8900","--host","127.0.0.1" `
    -WorkingDirectory (Join-Path $root "server") -WindowStyle Hidden
}

Start-Sleep -Seconds 7
$b = Alive "http://127.0.0.1:8790/health"
$c = Alive "http://127.0.0.1:8900/health"
Write-Host ("backend 8790: " + $(if ($b) { "OK" } else { "DOWN" })) -ForegroundColor $(if ($b) { "Green" } else { "Red" })
Write-Host ("cloud   8900: " + $(if ($c) { "OK" } else { "DOWN" })) -ForegroundColor $(if ($c) { "Green" } else { "Red" })
if (-not ($b -and $c)) { Write-Host "One service failed to start." -ForegroundColor Red; exit 1 }

Write-Host "`nLaunching Apex Cam..." -ForegroundColor Cyan
& (Join-Path $root "Apex Cam.bat")
