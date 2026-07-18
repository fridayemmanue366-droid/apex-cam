"""Download the open-source AI models Apex Cam can use, into backend/models/.

All of these are FREE / open-source (the same ones Deep-Live-Cam, Roop and
FaceFusion use). None are proprietary. Run this on a fast connection (e.g. your
work network); it skips files already present, so you can resume anytime:

    python scripts/download_models.py            # core + enhancer
    python scripts/download_models.py --all      # also optional extras

Proprietary cloud models (e.g. Decart "Lucy" used by MorphCam) are NOT here and
cannot be downloaded — they only run on the vendor's servers.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

MODELS = Path(__file__).resolve().parent.parent / "models"
GFPGAN_WEIGHTS = Path(__file__).resolve().parent.parent / "gfpgan" / "weights"

# name -> (dest_dir, url, approx MB, what it improves)
CORE = {
    "inswapper_128.onnx": (MODELS,
        "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx",
        529, "The face swap (realistic identity transfer)."),
    "inswapper_128_fp16.onnx": (MODELS,
        "https://huggingface.co/hacksider/deep-live-cam/resolve/main/inswapper_128_fp16.onnx",
        277, "Faster half-precision swap — better on weak PCs."),
    # The swap relies on the ONNX restorer/parser below (no PyTorch). The old
    # torch-based GFPGANv1.4.pth / facexlib .pth weights were removed — they were
    # never used at runtime and only caused a needless (often stalling) download.
    "gfpgan_1.4.onnx": (MODELS,
        "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/gfpgan_1.4.onnx",
        340, "GFPGAN restorer (ONNX) — default sharpness pass after the swap."),
    "bisenet_resnet_34.onnx": (MODELS,
        "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/bisenet_resnet_34.onnx",
        37, "Face parsing — face-shaped mask for a seamless (no-box) swap."),
}

# ONNX enhancers/masks — run on onnxruntime (CPU or GPU), NO PyTorch needed.
_FF = "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0"
EXTRA = {
    "codeformer.onnx": (MODELS, f"{_FF}/codeformer.onnx",
        377, "CodeFormer face restorer (ONNX) — very natural."),
    "2dfan4.onnx": (MODELS, f"{_FF}/2dfan4.onnx",
        98, "68-point face landmarks — better alignment/masking."),
    "rvm_mobilenetv3_fp32.onnx": (MODELS,
        "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx",
        15, "Background matting — blur/green-screen/replace, real-time on CPU."),
}

# MediaPipe task models (full-body pose). Needs: pip install mediapipe (py<=3.11).
MEDIAPIPE = {
    "pose_landmarker_full.task": (MODELS / "mediapipe",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
        "pose_landmarker_full/float16/latest/pose_landmarker_full.task",
        9, "Full-body pose — 33 landmarks, real-time on CPU."),
}

# LivePortrait — animate a photo with your motion (avatar mode). ONNX, CPU/GPU.
_LP = MODELS / "liveportrait"
LIVEPORTRAIT = {
    "live_portrait_feature_extractor.onnx": (_LP, f"{_FF}/live_portrait_feature_extractor.onnx",
        3, "LivePortrait appearance features."),
    "live_portrait_motion_extractor.onnx": (_LP, f"{_FF}/live_portrait_motion_extractor.onnx",
        113, "LivePortrait motion (pose/expression)."),
    "live_portrait_generator.onnx": (_LP, f"{_FF}/live_portrait_generator.onnx",
        222, "LivePortrait generator — the animated face."),
}
# buffalo_l (detector+recogniser) auto-downloads via insightface on first run.

# RVC voice-cloning BASE models (shared across all voices). A trained voice model
# (.onnx) still has to be added per voice into models/rvc/voices/.
_RVC = MODELS / "rvc"
RVC = {
    "content_vec.onnx": (_RVC,
        "https://huggingface.co/DogManTC/test-rvc-onnx/resolve/main/vec-768-layer-12.onnx",
        378, "RVC content encoder (voice-cloning base, shared)."),
    "rmvpe.onnx": (_RVC,
        "https://huggingface.co/lj1995/VoiceConversionWebUI/resolve/main/rmvpe.onnx",
        362, "RVC pitch (F0) extractor (voice-cloning base, shared)."),
}


def _hf_repo_file(url: str):
    """Parse a HuggingFace resolve URL -> (repo_id, filename), else None.
    e.g. https://huggingface.co/<repo>/resolve/main/<path> -> (<repo>, <path>)."""
    import re

    m = re.match(r"https?://huggingface\.co/(.+?)/resolve/[^/]+/(.+)$", url)
    return (m.group(1), m.group(2)) if m else None


def fetch(name: str, dest: Path, url: str, mb: int, why: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name
    if out.exists() and out.stat().st_size > 1024:
        print(f"  ✓ {name} already present ({out.stat().st_size/1e6:.0f} MB)")
        return
    print(f"  ↓ {name}  (~{mb} MB) — {why}")
    # HuggingFace repos use the Xet CDN, which TRUNCATES plain urllib/curl
    # downloads. Use huggingface_hub (handles Xet) for hf.co URLs; fall back to
    # urllib for everything else.
    hf = _hf_repo_file(url)
    if hf:
        try:
            import shutil

            from huggingface_hub import hf_hub_download

            cached = hf_hub_download(repo_id=hf[0], filename=hf[1])
            shutil.copy(cached, out)
            print(f"    done: {out.stat().st_size/1e6:.0f} MB")
            return
        except Exception as exc:
            print(f"    huggingface_hub failed ({exc}); trying direct download...")
    try:
        urllib.request.urlretrieve(url, out)
        print(f"    done: {out.stat().st_size/1e6:.0f} MB")
    except Exception as exc:
        print(f"    FAILED: {exc}  (retry later; url: {url})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="also download optional extras")
    args = ap.parse_args()

    print("Core models -> backend/models/ :")
    for name, (dest, url, mb, why) in CORE.items():
        fetch(name, dest, url, mb, why)
    if args.all:
        print("\nOptional extra models :")
        for name, (dest, url, mb, why) in EXTRA.items():
            fetch(name, dest, url, mb, why)
        print("\nLivePortrait (avatar mode) :")
        for name, (dest, url, mb, why) in LIVEPORTRAIT.items():
            fetch(name, dest, url, mb, why)
        print("\nMediaPipe (full-body pose) :")
        for name, (dest, url, mb, why) in MEDIAPIPE.items():
            fetch(name, dest, url, mb, why)
        print("\nRVC voice-cloning base models :")
        for name, (dest, url, mb, why) in RVC.items():
            fetch(name, dest, url, mb, why)
    print("\nDone. Missing files can be re-run anytime (existing ones are skipped).")


if __name__ == "__main__":
    sys.exit(main())
