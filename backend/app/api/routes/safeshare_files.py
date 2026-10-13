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

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/safeshare", tags=["safeshare"])

ROOT = Path.home() / "Downloads" / "SafeShare"
DOWNLOAD_URL = "https://apexcam-api.onrender.com/safeshare/download"
KINDS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif", ".avif"},
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
}
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
        raise HTTPException(400, "kind must be image or video")
    root = _root()
    if root is None:
        return {"installed": False, "folder": str(ROOT), "download_url": DOWNLOAD_URL, "files": []}
    out = []
    for p in root.rglob("*"):
        if len(out) >= MAX_FILES * 4:
            break
        name = p.name
        if name.startswith(".") or name.endswith((".part", ".partial", ".tmp")):
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
    if p.suffix.lower() not in KINDS["image"] | KINDS["video"]:
        raise HTTPException(403, "Only photos and videos can be used")
    return FileResponse(p.resolve(), filename=p.name)
