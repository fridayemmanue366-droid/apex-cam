"""Face engine control + face (avatar) profile management.

A face profile is a reference photo the swap engine will use as the target
identity (Phase 7). Profiles are stored locally under ``data/profiles``.
Per the responsible-use policy, users must only upload faces they have
permission to use; every processed output stays labeled AI-generated.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/face", tags=["face"])

PROFILES_DIR = Path("data/profiles")
BUILTIN_DIR = Path("data/builtin_profiles")
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


class FaceState(BaseModel):
    enabled: bool = False
    model: str = "passthrough"
    swap_enabled: bool = False
    profile_id: str | None = None


class FaceProfile(BaseModel):
    id: str
    name: str
    created: float
    builtin: bool = False


_state = FaceState()


@router.get("")
def get_state() -> FaceState:
    return _state


@router.put("")
def set_state(new: FaceState) -> FaceState:
    global _state
    _state = new
    _apply_swap()
    return _state


def deactivate_local() -> None:
    """Fully turn OFF the local face swap and DESELECT its face. Called when Apex
    Pro (Lucy) goes live so the local engine and Pro never run together and the
    local persona never lingers. To use the local swap again the user must go to
    the Face tab, re-select a face, and re-enable it."""
    global _state
    _state = FaceState()   # enabled=False, swap_enabled=False, profile_id=None
    _apply_swap()          # pushes swap OFF into the live pipeline


class SwapStrength(BaseModel):
    strength: float = 0.85


def _apply_swap() -> None:
    """Push the current face state into the live pipeline's swap engine.

    Never raises — a bad/undetectable target image just leaves the swap not
    ready; the UI toggle must not fail because of it.
    """
    from app.core.pipeline import pipeline

    try:
        path = None
        landmarks = None
        if _state.profile_id:
            p = _image_path(_state.profile_id)
            path = str(p) if p else None
            # Built-in demo faces ship known landmarks (the DNN can't read drawings).
            for base in (BUILTIN_DIR, PROFILES_DIR):
                meta = base / f"{_state.profile_id}.json"
                if meta.exists():
                    try:
                        landmarks = json.loads(meta.read_text()).get("landmarks")
                    except Exception:
                        landmarks = None
                    break
        pipeline.set_swap(_state.swap_enabled and _state.enabled, path, landmarks)
    except Exception:
        log.exception("Failed to apply swap target")


class SwapMode(BaseModel):
    mode: str = "fast"              # "fast" | "neural" | "avatar"
    neural_available: bool = False
    avatar_available: bool = False
    backend: str = "landmark"      # effective backend in use


@router.get("/swap/mode")
def get_swap_mode() -> SwapMode:
    from app.core.pipeline import pipeline

    return SwapMode(
        mode=pipeline.swap_mode,
        neural_available=pipeline.neural_available,
        avatar_available=pipeline.avatar_available,
        backend=pipeline.swap_backend,
    )


@router.put("/swap/mode")
def set_swap_mode(m: SwapMode) -> SwapMode:
    from app.core.pipeline import pipeline

    pipeline.set_swap_mode(m.mode)
    # Re-apply the current target on the newly selected backend.
    _apply_swap()
    return get_swap_mode()


class SkinMatch(BaseModel):
    strength: float = 0.9


@router.get("/swap/skin-match")
def get_skin_match() -> SkinMatch:
    from app.core.pipeline import pipeline

    return SkinMatch(strength=pipeline.neural_swapper.skin_match)


@router.put("/swap/skin-match")
def set_skin_match(s: SkinMatch) -> SkinMatch:
    from app.core.pipeline import pipeline

    pipeline.neural_swapper.skin_match = max(0.0, min(s.strength, 1.0))
    return SkinMatch(strength=pipeline.neural_swapper.skin_match)


@router.get("/swap/strength")
def get_swap_strength() -> SwapStrength:
    from app.core.pipeline import pipeline

    return SwapStrength(strength=pipeline.swapper.strength)


@router.put("/swap/strength")
def set_swap_strength(s: SwapStrength) -> SwapStrength:
    from app.core.pipeline import pipeline

    pipeline.swapper.strength = max(0.0, min(s.strength, 1.0))
    return SwapStrength(strength=pipeline.swapper.strength)


class SwapRealism(BaseModel):
    # Neural-swap tuning. Defaults = clean DLC core; protect_hair keeps your hair.
    protect_hair: bool = True   # keep your real hair on top (no hairline cut)
    poisson: bool = False       # gradient-domain blend (can add a bright disc)
    mouth_mask: bool = False     # keep your real mouth/teeth (natural talking)
    swap_hair: bool = False      # carry the SOURCE photo's hair/baldness onto you


def _realism(ns) -> "SwapRealism":
    return SwapRealism(protect_hair=ns.protect_hair, poisson=ns.poisson,
                       mouth_mask=ns.mouth_mask, swap_hair=ns.swap_hair)


@router.get("/swap/realism")
def get_swap_realism() -> SwapRealism:
    from app.core.pipeline import pipeline

    return _realism(pipeline.neural_swapper)


@router.put("/swap/realism")
def set_swap_realism(r: SwapRealism) -> SwapRealism:
    from app.core.pipeline import pipeline

    ns = pipeline.neural_swapper
    ns.protect_hair = bool(r.protect_hair)
    ns.poisson = bool(r.poisson)
    ns.mouth_mask = bool(r.mouth_mask)
    was_hair = ns.swap_hair
    ns.swap_hair = bool(r.swap_hair)
    # Building the source head is skipped while off — build it when turned on.
    if ns.swap_hair and not was_hair:
        try:
            ns.refresh_head()
        except Exception:
            log.exception("Failed to prepare source head for hair transfer")
    return _realism(ns)


def _meta_path(profile_id: str) -> Path:
    return PROFILES_DIR / f"{profile_id}.json"


def _image_path(profile_id: str) -> Path | None:
    for base in (PROFILES_DIR, BUILTIN_DIR):
        for ext in ALLOWED_EXT:
            p = base / f"{profile_id}{ext}"
            if p.exists():
                return p
    return None


@router.get("/profiles")
def list_profiles() -> list[FaceProfile]:
    profiles: list[FaceProfile] = []
    for base in (BUILTIN_DIR, PROFILES_DIR):
        if not base.exists():
            continue
        for meta in sorted(base.glob("*.json")):
            try:
                profiles.append(FaceProfile(**json.loads(meta.read_text())))
            except Exception:
                continue
    return profiles


@router.post("/profiles")
async def create_profile(file: UploadFile) -> FaceProfile:
    ext = Path(file.filename or "face.png").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported image type '{ext}'")

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_id = f"p{int(time.time() * 1000):x}"
    name = Path(file.filename or "Face").stem

    (PROFILES_DIR / f"{profile_id}{ext}").write_bytes(await file.read())
    profile = FaceProfile(id=profile_id, name=name, created=time.time())
    _meta_path(profile_id).write_text(profile.model_dump_json())
    return profile


@router.get("/profiles/{profile_id}/image")
def profile_image(profile_id: str) -> FileResponse:
    path = _image_path(profile_id)
    if not path:
        raise HTTPException(404, "Profile image not found")
    return FileResponse(path)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str) -> dict[str, bool]:
    global _state
    if profile_id.startswith("builtin-"):
        raise HTTPException(403, "Built-in demo faces cannot be deleted")
    found = False
    for p in [_meta_path(profile_id), _image_path(profile_id)]:
        if p and p.exists():
            p.unlink()
            found = True
    if not found:
        raise HTTPException(404, "Profile not found")
    if _state.profile_id == profile_id:
        _state = _state.model_copy(update={"profile_id": None})
    return {"deleted": True}
