"""Resumable, verified download of the AI model files Apex Cam needs.

Why this exists (2026-09-21): the models (~3 GB) are NOT inside the installer;
they are fetched after install. The old fetch was one-shot `urlretrieve`: no
retry, no resume, no timeout, and any file over 1 KB counted as "already
downloaded" -- so on a weak network a dropped connection left a truncated model
that was then trusted forever, and there was no way inside the app to finish
the job. Customers got stuck at exactly this step.

Now:
  * every file downloads to `<name>.part`, resumes with HTTP Range after a drop,
    retries with backoff, and is verified against the server's reported size
    before being moved into place -- a truncated file can never masquerade as a
    finished model;
  * "installed" means the file exists AND is within 5% of its known size;
  * HuggingFace files go through huggingface_hub (their Xet CDN truncates plain
    HTTP downloads), also with retries;
  * the same code backs the installer step, the CLI script, and the in-app
    "finish downloading" button/banner (routes/setup.py), so a customer can
    always recover by opening the app.
"""
from __future__ import annotations

import http.client
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.logging import get_logger

log = get_logger(__name__)

MODELS = Path(__file__).resolve().parents[2] / "models"   # backend/models
MIN_RATIO = 0.95     # a file smaller than this fraction of its known size is not "installed"
MAX_TRIES = 8        # attempts per file, each resuming where the last stopped
READ_TIMEOUT = 30    # seconds without data before an attempt is abandoned and retried

_FF = "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0"
_LP = "liveportrait"


@dataclass(frozen=True)
class Model:
    name: str                       # file name (or zip name) as downloaded
    url: str
    size: int                       # bytes; download size, used for progress + verification
    tier: str                       # "core" = needed for the swap, "extra" = optional features
    why: str
    dest: str = ""                  # subfolder of MODELS ("" = MODELS itself)
    files: tuple[tuple[str, int], ...] = ()   # (relative path, min size) that must exist; default = the file itself
    unzip_to: str | None = None     # zip models: extract into this subfolder of MODELS

    def required_files(self) -> tuple[tuple[str, int], ...]:
        if self.files:
            return self.files
        rel = f"{self.dest}/{self.name}" if self.dest else self.name
        return ((rel, self.size),)


CATALOG: tuple[Model, ...] = (
    # ---- core: the face swap ------------------------------------------------
    Model("inswapper_128.onnx",
          "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx",
          554_000_000, "core", "The face swap (realistic identity transfer)."),
    Model("inswapper_128_fp16.onnx",
          "https://huggingface.co/hacksider/deep-live-cam/resolve/main/inswapper_128_fp16.onnx",
          277_000_000, "core", "Faster half-precision swap, better on weak PCs."),
    Model("gfpgan_1.4.onnx", f"{_FF}/gfpgan_1.4.onnx",
          340_000_000, "core", "Sharpness pass after the swap."),
    Model("bisenet_resnet_34.onnx", f"{_FF}/bisenet_resnet_34.onnx",
          93_000_000, "core", "Face-shaped mask for a seamless swap."),
    # insightface would otherwise fetch this itself on first run, with no retry.
    Model("buffalo_l.zip",
          "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
          288_621_354, "core", "Face detector + recogniser.",
          unzip_to="buffalo_l",
          files=(("buffalo_l/det_10g.onnx", 16_000_000),
                 ("buffalo_l/w600k_r50.onnx", 165_000_000),
                 ("buffalo_l/1k3d68.onnx", 135_000_000),
                 ("buffalo_l/2d106det.onnx", 4_500_000),
                 ("buffalo_l/genderage.onnx", 1_200_000))),
    Model("face_detection_yunet_2023mar.onnx",
          "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
          232_589, "core", "Fast face tracker (was missing from fresh installs)."),
    # ---- extras -------------------------------------------------------------
    Model("codeformer.onnx", f"{_FF}/codeformer.onnx",
          376_951_650, "extra", "CodeFormer face restorer (very natural)."),
    Model("2dfan4.onnx", f"{_FF}/2dfan4.onnx",
          97_000_000, "extra", "Face landmarks for better alignment."),
    Model("rvm_mobilenetv3_fp32.onnx",
          "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx",
          14_500_000, "extra", "Background blur / replace."),
    Model("pose_landmarker_full.task",
          "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
          9_398_198, "extra", "Full-body pose.", dest="mediapipe"),
    Model("live_portrait_feature_extractor.onnx", f"{_FF}/live_portrait_feature_extractor.onnx",
          3_300_000, "extra", "Avatar mode: appearance.", dest=_LP),
    Model("live_portrait_motion_extractor.onnx", f"{_FF}/live_portrait_motion_extractor.onnx",
          112_000_000, "extra", "Avatar mode: motion.", dest=_LP),
    Model("live_portrait_generator.onnx", f"{_FF}/live_portrait_generator.onnx",
          222_000_000, "extra", "Avatar mode: animated face.", dest=_LP),
    Model("content_vec.onnx",
          "https://huggingface.co/DogManTC/test-rvc-onnx/resolve/main/vec-768-layer-12.onnx",
          377_000_000, "extra", "Voice cloning base (content encoder).", dest="rvc"),
    Model("rmvpe.onnx",
          "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.onnx",
          361_000_000, "extra", "Voice cloning base (pitch).", dest="rvc"),
)


# --- state -------------------------------------------------------------------
def _file_ok(rel: str, min_size: int) -> bool:
    p = MODELS / rel
    try:
        return p.is_file() and p.stat().st_size >= int(min_size * MIN_RATIO)
    except OSError:
        return False


def is_installed(m: Model) -> bool:
    return all(_file_ok(rel, size) for rel, size in m.required_files())


def _part_path(m: Model) -> Path:
    return MODELS / m.dest / (m.name + ".part")


def list_models(include_extra: bool = True) -> list[dict]:
    out = []
    for m in CATALOG:
        if m.tier == "extra" and not include_extra:
            continue
        if is_installed(m):
            st = "installed"
        elif _part_path(m).exists():
            st = "partial"
        else:
            st = "missing"
        out.append({"name": m.name, "tier": m.tier, "mb": round(m.size / 1e6),
                    "status": st, "why": m.why})
    return out


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.current = ""
        self.done_bytes = 0
        self.total_bytes = 0
        self.log: deque[str] = deque(maxlen=40)
        self.failed: list[str] = []
        self.finished = False


STATE = _State()


def _say(msg: str, printer: Callable[[str], None] | None = None) -> None:
    STATE.log.append(msg)
    log.info("models: %s", msg)
    if printer:
        printer(msg)


def snapshot() -> dict:
    """Progress + per-model status, for the UI/API."""
    models = list_models()
    core_missing = [m["name"] for m in models if m["tier"] == "core" and m["status"] != "installed"]
    return {
        "running": STATE.running,
        "current": STATE.current,
        "progress": (STATE.done_bytes / STATE.total_bytes) if STATE.total_bytes else 0.0,
        "core_missing": core_missing,
        "all_installed": all(m["status"] == "installed" for m in models),
        "failed": list(STATE.failed),
        "log": list(STATE.log)[-8:],
        "models": models,
    }


# --- downloading -------------------------------------------------------------
class _Fatal(Exception):
    """A failure retrying will not fix (e.g. 404)."""


def _total_from(resp, have: int) -> int | None:
    cr = resp.headers.get("Content-Range")
    if cr and "/" in cr:
        tail = cr.rsplit("/", 1)[1]
        return int(tail) if tail.isdigit() else None
    cl = resp.headers.get("Content-Length")
    if cl and cl.isdigit():
        return have + int(cl) if getattr(resp, "status", 200) == 206 else int(cl)
    return None


def _http_download(m: Model, dest: Path, on_bytes: Callable[[int], None],
                   printer: Callable[[str], None] | None) -> None:
    """Resumable download of m.url to `dest` (via dest.part), verified."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    floor = int(m.size * MIN_RATIO)
    last_err = "unknown error"
    for attempt in range(1, MAX_TRIES + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "ApexCam/1.0"}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(m.url, headers=headers)
            with urllib.request.urlopen(req, timeout=READ_TIMEOUT) as r:
                status = getattr(r, "status", 200)
                if have and status != 206:      # server ignored Range: start over
                    have = 0
                mode = "ab" if have else "wb"
                total = _total_from(r, have)
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        on_bytes(len(chunk))
            size = part.stat().st_size
            if total is not None and size != total:
                raise OSError(f"connection dropped at {size} of {total} bytes")
            if size < floor:
                raise OSError(f"file too small ({size} bytes, expected about {m.size})")
            os.replace(part, dest)
            return
        except urllib.error.HTTPError as e:
            if e.code == 416:               # asked past the end: partial is complete, or junk
                if part.exists() and part.stat().st_size >= floor:
                    os.replace(part, dest)
                    return
                part.unlink(missing_ok=True)
                last_err = "server rejected resume; restarting"
            elif e.code in (401, 403, 404, 410):
                raise _Fatal(f"server answered {e.code} for {m.url}")
            else:
                last_err = f"server error {e.code}"
        except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException) as e:
            # HTTPException covers IncompleteRead (connection cut mid-body).
            last_err = str(getattr(e, "reason", None) or e or type(e).__name__)
        wait = min(2 * attempt, 20)
        _say(f"  {m.name}: {last_err}; retry {attempt}/{MAX_TRIES} in {wait}s (resuming)", printer)
        time.sleep(wait)
    raise RuntimeError(f"gave up after {MAX_TRIES} tries: {last_err}")


def _hf_repo_file(url: str) -> tuple[str, str] | None:
    import re
    m = re.match(r"https?://huggingface\.co/(.+?)/resolve/[^/]+/(.+)$", url)
    return (m.group(1), m.group(2)) if m else None


def _hf_download(m: Model, dest: Path, printer: Callable[[str], None] | None) -> None:
    """HuggingFace via huggingface_hub (handles the Xet CDN), with retries."""
    from huggingface_hub import hf_hub_download

    repo, fname = _hf_repo_file(m.url)  # type: ignore[misc]
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODELS / "_hf_tmp"
    last_err = "unknown error"
    for attempt in range(1, MAX_TRIES + 1):
        try:
            got = Path(hf_hub_download(repo_id=repo, filename=fname, local_dir=str(tmp)))
            if got.stat().st_size < int(m.size * MIN_RATIO):
                got.unlink(missing_ok=True)
                raise OSError(f"file too small ({got.stat().st_size} bytes)")
            shutil.move(str(got), str(dest))
            shutil.rmtree(tmp, ignore_errors=True)
            return
        except Exception as e:  # noqa: BLE001 - network stacks raise many types
            last_err = str(e)[:160]
        wait = min(2 * attempt, 20)
        _say(f"  {m.name}: {last_err}; retry {attempt}/{MAX_TRIES} in {wait}s", printer)
        time.sleep(wait)
    shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError(f"gave up after {MAX_TRIES} tries: {last_err}")


def download_model(m: Model, printer: Callable[[str], None] | None = None) -> None:
    """Download (and unpack, for zips) one model. Raises on failure."""
    dest = MODELS / m.dest / m.name
    base = STATE.done_bytes

    def on_bytes(n: int) -> None:
        STATE.done_bytes += n

    if _hf_repo_file(m.url):
        try:
            _hf_download(m, dest, printer)
            STATE.done_bytes = base + m.size
            return
        except ImportError:
            _say("  huggingface_hub not available; using direct download", printer)
    _http_download(m, dest, on_bytes, printer)
    if m.unzip_to:
        target = MODELS / m.unzip_to
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest) as z:
            z.extractall(target)
        dest.unlink(missing_ok=True)
        if not is_installed(m):
            raise RuntimeError(f"{m.name} unpacked but expected files are missing")


def run(include_extra: bool = True, printer: Callable[[str], None] | None = None) -> bool:
    """Download everything not yet installed. Returns True if all CORE models are
    installed at the end. Safe to call repeatedly -- it resumes and skips finished files."""
    with STATE.lock:
        if STATE.running:
            return False
        STATE.running = True
        STATE.finished = False
        STATE.failed = []
        STATE.done_bytes = 0
    try:
        todo = [m for m in CATALOG
                if (include_extra or m.tier == "core") and not is_installed(m)]
        STATE.total_bytes = sum(m.size for m in todo)
        _say(f"{len(todo)} model file(s) to download (~{STATE.total_bytes // 1_000_000} MB)", printer)
        for m in todo:
            STATE.current = m.name
            _say(f"Downloading {m.name} (~{m.size // 1_000_000} MB) - {m.why}", printer)
            try:
                download_model(m, printer)
                _say(f"  done: {m.name}", printer)
            except Exception as e:  # noqa: BLE001 - keep going; report at the end
                STATE.failed.append(m.name)
                _say(f"  FAILED: {m.name}: {e}", printer)
        core_ok = all(is_installed(m) for m in CATALOG if m.tier == "core")
        if STATE.failed:
            _say("Some models did not finish (network problem?). Open Apex Cam and press "
                 "'Download missing models' to continue where it stopped.", printer)
        else:
            _say("All models installed.", printer)
        return core_ok
    finally:
        STATE.current = ""
        STATE.running = False
        STATE.finished = True


def start_background(include_extra: bool = True) -> bool:
    """Run the download on a background thread. False if one is already running."""
    if STATE.running:
        return False
    threading.Thread(target=run, args=(include_extra,), daemon=True).start()
    return True
