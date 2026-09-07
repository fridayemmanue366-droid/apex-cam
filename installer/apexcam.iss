; Apex Cam - one-click installer (Inno Setup).
; Wraps the self-contained bundle (dist-bundle\ApexCam, produced by build-bundle.ps1)
; into a double-click setup.exe. The customer never sees Python, Node, or a script.
;
; Build it: build-installer.ps1  (installs Inno Setup if needed, then compiles this)

#define AppName "Apex Cam"
#define AppVer  "1.0.0"
#define AppExe  "ApexCam.exe"

[Setup]
AppId={{7E3A9C2D-APEX-CAM-0001-BUNDLEINSTALLER}}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Apex Cam
DefaultDirName={localappdata}\Apex Cam
DefaultGroupName=Apex Cam
DisableProgramGroupPage=yes
; Per-user install to LocalAppData so no admin is needed and models/data stay writable.
PrivilegesRequired=lowest
OutputDir=..\dist-bundle
OutputBaseFilename=ApexCam-Setup
; Default: maximum compression = smallest download for customers (slow to build).
; Pass /DFastBuild (build-installer.ps1 -Fast) for quick test builds instead.
#ifdef FastBuild
Compression=lzma2/fast
SolidCompression=no
#else
Compression=lzma2/max
SolidCompression=yes
#endif
WizardStyle=modern
LicenseFile=terms.txt
; Brand the installer itself + the Add/Remove Programs entry with the logo.
SetupIconFile=apexcam.ico
UninstallDisplayIcon={app}\apexcam.ico
; The bundle is ~1.2 GB; give the wizard room.
DiskSpanning=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "vcam"; Description: "Install the Apex Cam virtual camera (use your Apex Cam video in Zoom, WhatsApp, YouCam, Meet & more)"; GroupDescription: "Virtual camera:"
Name: "getmodels"; Description: "Download the AI models now (~2 GB, needs internet - recommended)"; GroupDescription: "AI models:"

[Files]
; The entire self-contained bundle -> {app}
Source: "..\dist-bundle\ApexCam\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\Apex Cam"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\apexcam.ico"
Name: "{group}\Uninstall Apex Cam"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Apex Cam"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\apexcam.ico"; Tasks: desktopicon

[Run]
; Register the Apex Cam virtual camera. The .bat self-elevates (one UAC prompt) and
; registers the MIT-licensed Unity Capture filter under the device name "Apex Cam".
Filename: "{app}\vcam\register-camera.bat"; \
  StatusMsg: "Setting up the Apex Cam virtual camera (please approve the prompt)..."; \
  Flags: runhidden waituntilterminated; Tasks: vcam
; Optional one-time model download (visible console so the user sees progress).
Filename: "{app}\python\python.exe"; Parameters: "backend\scripts\download_models.py --all"; \
  WorkingDir: "{app}"; StatusMsg: "Downloading AI models (~2 GB, one-time)..."; \
  Tasks: getmodels
; Offer to launch after install.
Filename: "{app}\{#AppExe}"; Description: "Launch Apex Cam"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the virtual camera on uninstall (self-elevates for one UAC prompt).
Filename: "{app}\vcam\unregister-camera.bat"; Flags: runhidden waituntilterminated; \
  RunOnceId: "unregvcam"

[UninstallDelete]
; Remove the downloaded MODELS on uninstall (multi-GB, re-downloadable, and not
; the customer's own work).
Type: filesandordirs; Name: "{app}\backend\models"
; NOTE: "{app}\backend\data" is deliberately NOT deleted. It holds the customer's
; OWN face library (data\profiles) - work they cannot get back. Many people
; uninstall/reinstall as their way of updating; wiping it would destroy their
; uploads for nothing. It is a few MB, and a fresh install simply reuses it.
