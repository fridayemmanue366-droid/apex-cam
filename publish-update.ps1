# Apex Cam - publish an over-the-air update to installed apps.
#
# Two payloads travel this way, so a fix reaches customers WITHOUT them
# re-downloading the multi-GB installer:
#
#   -Frontend   the React UI            (~80 KB)   -> /update/app
#   -Backend    the Python source       (~500 KB)  -> /update/backend
#
# Neither payload ever contains the AI models or the customer's data. Apps stage
# the download in the background and apply it at their NEXT launch, always keeping
# a working copy to fall back to.
#
#   powershell -ExecutionPolicy Bypass -File publish-update.ps1              # both
#   powershell -ExecutionPolicy Bypass -File publish-update.ps1 -Backend     # one
#
# Then commit + push: Render redeploys and every installed app picks it up.
param([switch]$Frontend, [switch]$Backend)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $Frontend -and -not $Backend) { $Frontend = $true; $Backend = $true }

function Say($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Die($m) { Write-Host $m -ForegroundColor Red; exit 1 }
function ReadVer($p) { if (Test-Path $p) { [int]((Get-Content $p -Raw).Trim()) } else { 0 } }
function WriteVer($p, $v) { [IO.File]::WriteAllText($p, [string]$v) }

$bundleDir = Join-Path $root "server\app_bundle"
New-Item -ItemType Directory -Force $bundleDir | Out-Null
$node = if (Get-Command node -ErrorAction SilentlyContinue) { (Get-Command node).Source }
        elseif (Test-Path "$env:ProgramFiles\nodejs\node.exe") { "$env:ProgramFiles\nodejs\node.exe" }
        else { $null }

# --- Frontend (UI) ----------------------------------------------------------
if ($Frontend) {
  Say "Publish the frontend (UI)"
  if (-not $node) { Die "Node.js not found (needed to build the UI)." }

  $verFile = Join-Path $root "desktop\public\frontend-version.txt"
  $next = (ReadVer $verFile) + 1
  WriteVer $verFile $next          # baked into dist/ so a fresh install knows its own version

  Push-Location (Join-Path $root "desktop")
  & $node "node_modules\vite\bin\vite.js" build
  if ($LASTEXITCODE -ne 0) { Pop-Location; Die "UI build failed." }
  Pop-Location

  $dist = Join-Path $root "desktop\dist"
  if (-not (Test-Path (Join-Path $dist "index.html"))) { Die "desktop\dist\index.html missing after build." }
  $zip = Join-Path $bundleDir "frontend.zip"
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zip
  WriteVer (Join-Path $bundleDir "version.txt") $next
  $kb = [math]::Round((Get-Item $zip).Length / 1KB, 1)
  Write-Host "  frontend v$next  ($kb KB)" -ForegroundColor Green
}

# --- Backend (Python source) -------------------------------------------------
if ($Backend) {
  Say "Publish the backend (Python source)"
  $src = Join-Path $root "backend\app"
  if (-not (Test-Path (Join-Path $src "main.py"))) { Die "backend\app\main.py not found." }

  # Stage a clean copy: source only. No caches, no secrets, no data, no models.
  # Staged under the repo, not $env:TEMP: on some profiles TEMP is an 8.3 short
  # path ("C:\Users\THISPC~1\...") that Remove-Item refuses to act on.
  $stage = Join-Path $root (".publish-tmp\backend-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force (Join-Path $stage "app") | Out-Null
  robocopy $src (Join-Path $stage "app") /E /NFL /NDL /NJH /NJS /NP `
    /XD "__pycache__" ".pytest_cache" `
    /XF "*.local" ".env" "*.env" "*.db" "*.key" "*.pem" "*.log" "*.pyc" | Out-Null
  if ($LASTEXITCODE -ge 8) { Die "robocopy failed staging the backend source." }

  # Fail loudly rather than ship a secret to every customer.
  $leaked = Get-ChildItem $stage -Recurse -File -Force |
            Where-Object { $_.Name -match '\.(local|env|key|pem|db)$' }
  if ($leaked) { Die ("Refusing to publish - secret-looking files staged: " + ($leaked.Name -join ", ")) }

  $verFile = Join-Path $root "backend\backend-version.txt"
  $next = (ReadVer $verFile) + 1
  WriteVer $verFile $next          # the baseline a NEW installer will ship with

  $zip = Join-Path $bundleDir "backend.zip"
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Compress-Archive -Path (Join-Path $stage "app") -DestinationPath $zip
  WriteVer (Join-Path $bundleDir "backend-version.txt") $next
  Remove-Item (Join-Path $root ".publish-tmp") -Recurse -Force -ErrorAction SilentlyContinue
  $kb = [math]::Round((Get-Item $zip).Length / 1KB, 1)
  Write-Host "  backend v$next  ($kb KB)" -ForegroundColor Green
}

Say "Next step"
Write-Host "  git add -A; git commit -m 'Publish update'; git push" -ForegroundColor Yellow
Write-Host "  Render redeploys, and installed apps pick it up on their next launch."
