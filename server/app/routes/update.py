"""Auto-update manifest — the app asks this what the latest version is.

Chrome-style: the desktop app polls this, and if a newer version exists it
downloads the installer in the background and installs it on restart. Point
APEXCAM_LATEST_* at whatever hosts your ApexCam-Setup.exe (your server, S3,
GitHub releases…). Update the values and every installed app upgrades itself.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/update", tags=["update"])

# Optional JSON file so you can publish a release without redeploying the server.
MANIFEST_FILE = Path(os.environ.get("APEXCAM_MANIFEST", "release.json"))


@router.get("/latest")
def latest(platform: str = "win") -> dict:
    """Return {version, url, notes, mandatory}. The app compares `version` with
    its own and updates if newer."""
    if MANIFEST_FILE.exists():
        try:
            data = json.loads(MANIFEST_FILE.read_text())
            return data.get(platform, data)
        except Exception:
            pass
    return {
        "version": os.environ.get("APEXCAM_LATEST_VERSION", "1.0.0"),
        "url": os.environ.get("APEXCAM_LATEST_URL", ""),
        "notes": os.environ.get("APEXCAM_LATEST_NOTES", ""),
        "mandatory": os.environ.get("APEXCAM_LATEST_MANDATORY", "0") == "1",
    }
