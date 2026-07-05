"""Download the open-source AI models EMY CAM can use, into backend/models/.

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
    "GFPGANv1.4.pth": (MODELS,
        "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
        333, "Face restorer — sharpens/cleans the swapped face."),
    "parsing_parsenet.pth": (GFPGAN_WEIGHTS,
        "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth",
        85, "Face parsing — precise full-face mask (fixes edges/coverage)."),
    "detection_Resnet50_Final.pth": (GFPGAN_WEIGHTS,
        "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
        109, "Face detector used by GFPGAN."),
}

# ONNX enhancers/masks — run on onnxruntime (CPU or GPU), NO PyTorch needed.
_FF = "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0"
EXTRA = {
    "gfpgan_1.4.onnx": (MODELS, f"{_FF}/gfpgan_1.4.onnx",
        340, "GFPGAN face restorer (ONNX) — sharp, clean. CPU or GPU, no torch."),
    "codeformer.onnx": (MODELS, f"{_FF}/codeformer.onnx",
        377, "CodeFormer face restorer (ONNX) — very natural."),
    "2dfan4.onnx": (MODELS, f"{_FF}/2dfan4.onnx",
        98, "68-point face landmarks — better alignment/masking."),
    "bisenet_resnet_34.onnx": (MODELS, f"{_FF}/bisenet_resnet_34.onnx",
        37, "Face parsing — precise full-face mask (ONNX)."),
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


def fetch(name: str, dest: Path, url: str, mb: int, why: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name
    if out.exists() and out.stat().st_size > 1024:
        print(f"  ✓ {name} already present ({out.stat().st_size/1e6:.0f} MB)")
        return
    print(f"  ↓ {name}  (~{mb} MB) — {why}")
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
    print("\nDone. Missing files can be re-run anytime (existing ones are skipped).")


if __name__ == "__main__":
    sys.exit(main())
