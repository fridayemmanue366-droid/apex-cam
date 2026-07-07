# Apex Cam - build the one-click setup.exe from the bundle.
#
#   powershell -ExecutionPolicy Bypass -File build-installer.ps1
#
# Prereqs: run build-bundle.ps1 first (produces dist-bundle\ApexCam). This script
# installs Inno Setup if needed, then compiles installer\apexcam.iss into
# dist-bundle\ApexCam-Setup.exe.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
function Say($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Die($m) { Write-Host $m -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $root "dist-bundle\ApexCam\ApexCam.exe"))) {
  Die "Bundle not found. Run build-bundle.ps1 first."
}

Say "Locate Inno Setup"
function Find-ISCC {
  foreach ($c in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                   "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                   "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
    if (Test-Path $c) { return $c }
  }
  return $null
}
$iscc = Find-ISCC
if (-not $iscc) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Installing Inno Setup via winget..."
    winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
    $iscc = Find-ISCC
  }
}
if (-not $iscc) { Die "Inno Setup not found. Install it from https://jrsoftware.org/isdl.php and re-run." }

Say "Compile the installer"
& $iscc (Join-Path $root "installer\apexcam.iss")
if ($LASTEXITCODE -ne 0) { Die "Inno Setup compile failed." }

$setup = Join-Path $root "dist-bundle\ApexCam-Setup.exe"
if (Test-Path $setup) {
  $mb = [math]::Round((Get-Item $setup).Length/1MB, 0)
  Say "Installer ready"
  Write-Host "  $setup  ($mb MB)" -ForegroundColor Green
  Write-Host "  Ship this single file. Customers double-click it -> Next -> done."
} else {
  Die "Compile finished but setup.exe not found."
}
