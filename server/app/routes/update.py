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

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/update", tags=["update"])

# Frontend-only ("Level 2") update: the app hot-swaps just its UI (~230KB) with no
# full reinstall. Publish by rebuilding the frontend, zipping the CONTENTS of dist/
# into app_bundle/frontend.zip and bumping app_bundle/version.txt, then pushing.
APP_DIR = Path(os.environ.get("APEXCAM_APP_DIR", "app_bundle"))


@router.get("/app")
def app_update() -> dict:
    """{version, url} for the frontend bundle. version is an integer in
    app_bundle/version.txt; the app downloads when it's higher than what it has."""
    vf = APP_DIR / "version.txt"
    ver = vf.read_text().strip() if vf.exists() else "0"
    base = os.environ.get("APEXCAM_PUBLIC_URL", "")
    return {"version": ver, "url": f"{base}/update/app-bundle"}


@router.get("/app-bundle")
def app_bundle() -> FileResponse:
    z = APP_DIR / "frontend.zip"
    if not z.exists():
        raise HTTPException(404, "no app bundle published")
    return FileResponse(z, media_type="application/zip", filename="frontend.zip")


# Level 3: the PYTHON BACKEND source (~500 KB), same idea as the frontend above.
# Before this existed, a backend fix could only travel inside the 3 GB installer, so
# in practice it never reached anyone. The payload is backend/app/** only — never
# the customer's data/ or the 3 GB models/. The app stages it in the background and
# swaps it in at the next launch, keeping the old copy to roll back to.
@router.get("/backend")
def backend_update() -> dict:
    """{version, url} for the backend source. version is an integer in
    app_bundle/backend-version.txt; the app downloads when it is higher than the
    version recorded in its own install."""
    vf = APP_DIR / "backend-version.txt"
    ver = vf.read_text().strip() if vf.exists() else "0"
    base = os.environ.get("APEXCAM_PUBLIC_URL", "")
    return {"version": ver, "url": f"{base}/update/backend-bundle"}


@router.get("/backend-bundle")
def backend_bundle() -> FileResponse:
    z = APP_DIR / "backend.zip"
    if not z.exists():
        raise HTTPException(404, "no backend bundle published")
    return FileResponse(z, media_type="application/zip", filename="backend.zip")

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
