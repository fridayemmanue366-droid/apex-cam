# 📥 Download at work (free network) — EMY CAM upgrades

Everything here is **free / open-source** and makes the local swap better, sharper,
and faster. Grab it on a fast, unmetered connection. Nothing here is required for
the app to run — it already works — these are quality/speed upgrades.

> Not included (and can't be): MorphCam's "Lucy" model. That's **Decart's cloud**
> model — proprietary, server-only, not downloadable at any price.

## ✅ The one-command way

From `backend/` with the app's Python:

```powershell
.\.venv311\Scripts\python.exe scripts\download_models.py --all
```

It downloads everything below into `backend/models/`, **skips what's already
there**, and can be re-run anytime to resume. Then for the enhancer engine:

```powershell
# GPU machine (real-time + GFPGAN):
.\.venv311\Scripts\python.exe -m pip install onnxruntime-gpu torch --index-url https://download.pytorch.org/whl/cu121
.\.venv311\Scripts\python.exe -m pip install gfpgan basicsr facexlib

# CPU-only machine (skip torch — use the ONNX enhancer instead, see list):
# nothing extra needed; the *.onnx enhancers run on onnxruntime you already have
```

Or just click **"⤓ Set up realistic GPU mode"** on the AI Models tab in the app.

## 📋 The pile (what & why)

### Priority 1 — makes the swap look/run clearly better
| File | Size | What it does | Status |
|---|---|---|---|
| `inswapper_128_fp16.onnx` | 277 MB | **Faster swap** (half precision) — better on weak PCs | ⏳ extract from your `deep-live-cam.zip` failed; download instead |
| `gfpgan_1.4.onnx` (GFPGAN as ONNX) | ~333 MB | **Sharpens/cleans the swapped face** — runs on onnxruntime, **no PyTorch needed** (best for this laptop) | ❌ need download |
| `xseg_1.onnx` (face segmentation) | ~50 MB | **Full-face/head mask** — even cleaner edges than the geometric mask | ❌ need download |
| `2dfan4.onnx` (68-pt landmarks) | ~90 MB | Better face alignment → less wobble | ❌ need download |

### Priority 2 — higher quality options
| File | Size | What it does | Status |
|---|---|---|---|
| `codeformer.onnx` | ~360 MB | Alternative face restorer — very natural results | ❌ need download |
| `GFPGANv1.4.pth` + PyTorch | 333 MB + ~2.5 GB | The classic GFPGAN enhancer (needs torch; **GPU recommended**) | ⚠️ weights partly on disk, torch failed earlier — big download |
| `onnxruntime-gpu` | ~250 MB | GPU acceleration (real-time on NVIDIA cards) | ⚠️ for GPU machines |

### Already have (no action) ✅
- `inswapper_128.onnx` (529 MB) — the realistic swap, working now
- `buffalo_l/` face detect+recognise pack — working now
- `face_detection_yunet` — fast CPU tracker

## 💡 Recommended for *this* laptop (no GPU)
Get the **ONNX versions** — they run on the onnxruntime you already have, **no
2.5 GB PyTorch needed**:
1. `inswapper_128_fp16.onnx` (speed)
2. `gfpgan_1.4.onnx` (sharper faces, CPU-friendly)
3. `xseg_1.onnx` (best mask/coverage)

That trio, ~660 MB total, gives you "double-to-triple better" quality **without**
the heavy torch install — and it's exactly what makes the local swap rival what
you saw. The `download_models.py --all` command grabs them.

## Note
If the app's ONNX enhancer path isn't wired yet when you get these, tell me and
I'll wire `gfpgan_1.4.onnx` / `xseg_1.onnx` into the pipeline (they run on
onnxruntime, so they'll work on this laptop too — no PyTorch).
