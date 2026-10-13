"""Apex Pro uploads come ONLY from SafeShare's folder (owner decision 2026-10-13).

Apex Cam must not see the rest of the PC when a customer picks a photo or video
for Lucy — only the files they keep in SafeShare (which also markets SafeShare,
the owner's second app). SafeShare stores everything in Downloads\\SafeShare
(see safeshare.py: --folder default = ~/Downloads/SafeShare).

These two endpoints can only ever list/read files INSIDE that folder: every path
is resolved and checked to still be under it, so "..", absolute paths, symlinks
or junctions pointing elsewhere are refused.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter(prefix="/safeshare", tags=["safeshare"])

ROOT = Path.home() / "Downloads" / "SafeShare"
DOWNLOAD_URL = "https://apexcam-api.onrender.com/safeshare/download"
KINDS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif", ".avif"},
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
    # Desktop voice-clone clips (owner 2026-10-13: on the laptop, voice audio comes from
    # SafeShare too; only the mobile web page uses the phone's own library).
    "audio": {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac"},
}
ALL_EXTS = set().union(*KINDS.values())
MAX_FILES = 500


def _root() -> Path | None:
    try:
        r = ROOT.resolve()
        return r if r.is_dir() else None
    except OSError:
        return None


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


@router.get("/files")
def list_files(kind: str = "image") -> dict:
    """Newest first. `installed` False -> the UI offers the SafeShare download."""
    exts = KINDS.get(kind)
    if exts is None:
        raise HTTPException(400, "kind must be image, video or audio")
    root = _root()
    if root is None:
        return {"installed": False, "folder": str(ROOT), "download_url": DOWNLOAD_URL, "files": []}
    out = []
    for p in root.rglob("*"):
        if len(out) >= MAX_FILES * 4:
            break
        name = p.name
        if name.startswith(".") or name.endswith((".part", ".partial", ".tmp", ".safeshare-part")):
            continue
        if p.suffix.lower() in exts and p.is_file() and _inside(root, p):
            st = p.stat()
            out.append({"path": p.relative_to(root).as_posix(), "name": name,
                        "folder": p.parent.relative_to(root).as_posix() if p.parent != root else "",
                        "size": st.st_size, "modified": st.st_mtime})
    out.sort(key=lambda f: f["modified"], reverse=True)
    return {"installed": True, "folder": str(root), "download_url": DOWNLOAD_URL, "files": out[:MAX_FILES]}


@router.get("/file")
def get_file(path: str) -> FileResponse:
    root = _root()
    if root is None:
        raise HTTPException(404, "SafeShare folder not found")
    p = (root / path)
    if not _inside(root, p) or not p.resolve().is_file():
        raise HTTPException(403, "Only files inside SafeShare can be used")
    if p.suffix.lower() not in ALL_EXTS:
        raise HTTPException(403, "Only photos, videos and audio can be used")
    return FileResponse(p.resolve(), filename=p.name)


@router.post("/import")
async def import_file(file: UploadFile) -> dict:
    """"Add from this PC…" (@safeshare's idea, owner liked it): the customer picks a
    file in the normal Windows dialog and we COPY it into SafeShare's Inbox, so
    every file still goes through SafeShare and nobody gets stuck."""
    root = _root()
    if root is None:
        raise HTTPException(404, "Install SafeShare first")
    name = _safe_name(file.filename or "file")
    if Path(name).suffix.lower() not in ALL_EXTS:
        raise HTTPException(400, "Only photos, videos and audio can be added")
    inbox = root / "Inbox"
    inbox.mkdir(exist_ok=True)
    dest = _unique_path(inbox, name)
    # @safeshare's rule: write under a ".safeshare-part" name (SafeShare hides
    # those) and rename when complete, so nobody ever sees a half-written file.
    part = dest.with_name(dest.name + PART)
    try:
        with part.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                fh.write(chunk)
        part.replace(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise HTTPException(500, "Couldn't copy that file into SafeShare")
    return {"path": dest.relative_to(root).as_posix(), "name": dest.name}


# --- same naming rules as SafeShare itself (safeshare.py safe_name / unique_path) ---
PART = ".safeshare-part"
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def _safe_name(name: str) -> str:
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c not in '<>:"/\\|?*' and ord(c) >= 32).strip().strip(".")
    if name.endswith(PART):
        name = name[:-len(PART)]
    if not name or name.split(".")[0].upper() in _RESERVED:
        name = f"file_{int(time.time())}" + (f"_{name}" if name else "")
    return name[:200]


def _unique_path(directory: Path, name: str) -> Path:
    """Adds ' (1)', ' (2)' … exactly like SafeShare; also avoids names still being written."""
    p = Path(name)
    i = 0
    while True:
        cand = directory / (f"{p.stem} ({i}){p.suffix}" if i else name)
        if not cand.exists() and not cand.with_name(cand.name + PART).exists():
            return cand
        i += 1
