"""Neural face swap engine (GPU) — the realistic path.

Uses InsightFace's detector/recogniser (buffalo_l) + the **inswapper_128** model.
Unlike the CPU landmark swapper, inswapper transfers only the *identity* of the
target face while preserving the driver's (the user's) pose, expression, mouth
movement and eye blinks — so the result talks, blinks and moves naturally on the
user's real body. Optionally sharpened with GFPGAN.

IMPORTANT: this requires a machine that can install `insightface`,
`onnxruntime-gpu` (CUDA) and download the models — see docs/GPU_SETUP.md. All
heavy imports are guarded so the app still runs (on the CPU landmark swapper)
when these aren't available. This module has been written to the standard
InsightFace API but must be verified on a GPU machine; the build machine here
cannot run it.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from app.core.logging import get_logger

log = get_logger(__name__)

MODELS_DIR = Path("models")
INSWAPPER_FILE = MODELS_DIR / "inswapper_128.onnx"
INSWAPPER_FP16 = MODELS_DIR / "inswapper_128_fp16.onnx"


def best_inswapper() -> Path:
    """Prefer the smaller/faster fp16 model when present (better on weak PCs)."""
    if INSWAPPER_FP16.exists() and INSWAPPER_FP16.stat().st_size > 1_000_000:
        return INSWAPPER_FP16
    return INSWAPPER_FILE
# Override with APEXCAM_INSWAPPER_URL if the default source is unavailable.
INSWAPPER_URL = os.environ.get(
    "APEXCAM_INSWAPPER_URL",
    "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx",
)


def gpu_available() -> bool:
    """True if an ONNX Runtime CUDA provider is present."""
    try:
        import onnxruntime as ort

        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def neural_swap_available() -> bool:
    """True if insightface + onnxruntime import and the swapper model exists."""
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    return INSWAPPER_FILE.exists()


GFPGAN_FILE = MODELS_DIR / "GFPGANv1.4.pth"


def enhancer_available() -> bool:
    """True if GFPGAN + torch import and the weights exist (GPU setup done)."""
    try:
        import gfpgan  # noqa: F401
        import torch  # noqa: F401
    except Exception:
        return False
    return GFPGAN_FILE.exists()


def _providers() -> list[str]:
    try:
        import onnxruntime as ort

        avail = ort.get_available_providers()
    except Exception:
        return ["CPUExecutionProvider"]
    order = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    return [p for p in order if p in avail] or ["CPUExecutionProvider"]


# --- Head/hair transfer alignment ---------------------------------------------
# ArcFace 5-point template (eyes, nose, mouth corners) in a 112px crop. We build
# a bigger canonical "head" canvas from it so there's room ABOVE the face for the
# source photo's hair, then map the source head onto the user via a similarity
# transform from the 5 keypoints (same idea inswapper uses to align faces).
HEAD_SIZE = 512
_ARC5 = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


def _head_dst() -> np.ndarray:
    """5-point destination template inside the HEAD_SIZE canvas: face centred
    horizontally and pushed down so ~42% of the canvas above it is free for hair."""
    s = 3.2
    dst = _ARC5 * s
    dst[:, 0] += (HEAD_SIZE - (dst[:, 0].min() + dst[:, 0].max())) / 2.0
    dst[:, 1] += 0.42 * HEAD_SIZE - dst[:, 1].min()
    return dst


class NeuralFaceSwapEngine:
    """InsightFace inswapper wrapper. Self-contained: it runs its own detector,
    so it doesn't share the CPU tracker."""

    def __init__(self, use_enhancer: bool = False) -> None:
        self._app = None          # FaceAnalysis
        self._swapper = None      # INSwapper
        self._source_face = None  # target identity embedding/face
        self._loaded = False
        self.enhancer_kind = "none"  # "none" | "gfpgan" | "codeformer"
        self.strength = 1.0
        # Lock the swapped face to the SOURCE PHOTO's exact complexion/brightness
        # (0 = as-is, may darken to room light; 1 = fully the photo's skin tone).
        # Fixes the face coming out darker/off from the chosen photo.
        self.skin_match = 0.9
        self._source_color = None  # cached LAB mean of the source face
        # Blend the swapped face using a BiSeNet mask built from the USER'S REAL
        # face (hair EXCLUDED), instead of inswapper's rectangular paste. This
        # gives the seamless Deep-Live-Cam/FaceFusion look AND — critically —
        # protects the user's own hair: the swap never reaches into hair, so a
        # bald source photo can't make the user bald. (roop keeps hair the same
        # way, by never pasting over the hair region.) Falls back to inswapper's
        # native paste when the parser model is absent.
        self.use_parse = True
        # Poisson (gradient-domain) blend via cv2.seamlessClone — the Deep-Live-Cam
        # realism secret: it matches the swapped face's lighting/tone into the
        # surrounding skin so there's no seam when you turn or the light changes.
        self.poisson = True
        # Mouth mask: keep the user's REAL mouth/lips/teeth (cut from the pre-swap
        # frame, feathered back over the swap) so talking looks natural. Opt-in —
        # it trades the identity's mouth for the user's own. 0..1 expansion.
        self.mouth_mask = False
        self.mouth_expand = 0.5
        # Head/hair transfer: composite the SOURCE photo's hair (or bald scalp)
        # onto the user, so the source's hairstyle/baldness carries over — the one
        # thing inswapper (roop/DLC) can't do on its own. Best-effort, opt-in.
        self.swap_hair = False
        self._source_image = None   # cached source photo (to (re)build the head)
        self._head_canon = None     # source head aligned into the HEAD_SIZE canvas
        self._head_mask = None      # soft mask of hair+scalp+ears (NOT the face)

    @property
    def ready(self) -> bool:
        return self._loaded and self._source_face is not None

    def load(self) -> bool:
        if self._loaded:
            return True
        try:
            from insightface.app import FaceAnalysis
            from insightface.model_zoo import get_model

            providers = _providers()
            on_gpu = any("CUDA" in p or "Tensorrt" in p for p in providers)
            ctx_id = 0 if on_gpu else -1
            # Bigger detector on GPU (quality); smaller on CPU (speed).
            det = (640, 640) if on_gpu else (320, 320)

            # buffalo_l lives at models/buffalo_l, so root is models/'s parent.
            self._app = FaceAnalysis(
                name="buffalo_l", root=str(MODELS_DIR.parent), providers=providers,
            )
            self._app.prepare(ctx_id=ctx_id, det_size=det)
            model_path = best_inswapper()
            self._swapper = get_model(str(model_path), providers=providers)
            self._loaded = True
            # Sharpness: enable GFPGAN by default (the Roop/Deep-Live-Cam recipe —
            # a GFPGAN pass after the swap is what makes it look crisp/real).
            if self.enhancer_kind == "none":
                from app.engines.face.enhancer import enhancer_models_available
                avail = enhancer_models_available()
                if "gfpgan" in avail:
                    self.set_enhancer("gfpgan")
            log.info("Neural swap engine loaded (%s, providers=%s, enhancer=%s)",
                     model_path.name, providers, self.enhancer_kind)
            return True
        except Exception as exc:
            log.warning("Neural swap engine unavailable, falling back to CPU: %s", exc)
            self._loaded = False
            return False

    def set_enhancer(self, kind: str) -> str:
        """Select the ONNX face restorer: 'none' | 'gfpgan' | 'codeformer'.
        Runs on onnxruntime (CPU or GPU) — no PyTorch. Returns the active kind."""
        from app.engines.face.enhancer import face_enhancer

        if kind == "none" or not kind:
            self.enhancer_kind = "none"
            return "none"
        if face_enhancer.load(kind):
            self.enhancer_kind = kind
            return kind
        self.enhancer_kind = "none"
        return "none"

    def set_target(self, image_bgr: np.ndarray | None) -> bool:
        """Pick the largest face in the target image as the identity to wear."""
        if image_bgr is None or not self._loaded:
            self._source_face = None
            return False
        faces = self._app.get(image_bgr)
        if not faces:
            log.warning("Neural swap: no face found in target image")
            self._source_face = None
            return False
        self._source_face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
        # Remember the photo's exact complexion so the live swap keeps it.
        self._compute_face_color(image_bgr, self._source_face.bbox)
        # Cache the photo + build the source head (hair/scalp) for hair transfer.
        self._source_image = image_bgr
        self._prepare_head()
        return True

    def refresh_head(self) -> None:
        """Rebuild the cached source head (call after toggling swap_hair on)."""
        self._prepare_head()

    def swap_frame(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Detect every face in the live frame and swap identity in, preserving
        the driver's pose/expression/blink. Then optionally restore each face
        with the ONNX enhancer (GFPGAN/CodeFormer)."""
        if not self.ready:
            return frame_bgr
        try:
            from app.engines.face.enhancer import face_enhancer

            faces = self._app.get(frame_bgr)
            # Drop tiny/low-confidence detections (false positives that cause
            # extra/ghost faces). Keep only faces at least ~6% of the frame width.
            fw = frame_bgr.shape[1]
            faces = [f for f in faces if (f.bbox[2] - f.bbox[0]) > fw * 0.06
                     and getattr(f, "det_score", 1.0) > 0.5]
            # Keep the genuine pre-swap frame for mouth-mask cutout / Poisson.
            need_orig = self.poisson or self.mouth_mask
            original = frame_bgr.copy() if need_orig else frame_bgr
            for face in faces:
                # Carry the source photo's hair/scalp onto the user first, so the
                # swapped face sits cleanly on top at the hairline.
                if self.swap_hair:
                    frame_bgr = self._transfer_head(frame_bgr, face)
                box = self._clamp_box(face.bbox, frame_bgr.shape)
                # Swap into an aligned crop, then paste back with a face-shaped
                # (BiSeNet) feathered mask for a seamless, no-box result.
                fake, M = self._swapper.get(
                    frame_bgr, face, self._source_face, paste_back=False)
                frame_bgr = self._paste(frame_bgr, fake, M)
                if self.skin_match > 0 and box and self._source_color is not None:
                    self._match_to_source(frame_bgr, box)
                    self._blend_neck(frame_bgr, box)
                # Keep the user's real mouth/teeth (natural talking), then
                # gradient-blend the face into the scene (DLC realism).
                if self.mouth_mask:
                    frame_bgr = self._apply_mouth(frame_bgr, original, face)
                if self.poisson:
                    frame_bgr = self._poisson_blend(frame_bgr, original, M, fake)
                if self.enhancer_kind != "none":
                    frame_bgr = face_enhancer.enhance(frame_bgr, face.kps)
        except Exception as exc:
            log.debug("neural swap frame skipped: %s", exc)
        return frame_bgr

    def _paste(self, frame: np.ndarray, fake: np.ndarray, M) -> np.ndarray:
        """Paste the swapped aligned crop (``fake``, with affine ``M``) back onto
        the frame. Prefers a BiSeNet face-shaped, hairline-reaching feathered mask
        (the Deep-Live-Cam look — no visible box). Falls back to inswapper's own
        eroded/blurred rectangular mask when the parser isn't available."""
        import cv2

        h, w = frame.shape[:2]
        IM = cv2.invertAffineTransform(M)
        fake_full = cv2.warpAffine(fake, IM, (w, h), borderValue=0.0)

        mask = None
        if self.use_parse:
            try:
                from app.engines.face.face_parser import face_parser, parser_available

                if parser_available():
                    # Parse the USER'S REAL aligned face (not the swap). BiSeNet's
                    # FACE_CLASSES exclude hair, so wherever the user has hair the
                    # mask is 0 → their hair is never overwritten (bald source can't
                    # bald the user). include_hair stays False by design.
                    real_aligned = cv2.warpAffine(
                        frame, M, (fake.shape[1], fake.shape[0]))
                    pm = face_parser.mask_512(real_aligned, include_hair=False)
                    if pm is not None:
                        pm = cv2.resize(pm, (fake.shape[1], fake.shape[0]))
                        mask = cv2.warpAffine(pm, IM, (w, h), borderValue=0.0)
            except Exception as exc:
                log.debug("parse mask skipped: %s", exc)

        if mask is None:
            # Faithful fallback: inswapper's rectangular mask, eroded + feathered
            # by a size proportional to the face (same recipe as InsightFace).
            white = np.ones(fake.shape[:2], np.float32)
            mask = cv2.warpAffine(white, IM, (w, h), borderValue=0.0)
            ys, xs = np.where(mask > 0.5)
            if len(ys) == 0:
                return frame
            mh, mw = int(ys.max() - ys.min()), int(xs.max() - xs.min())
            ms = int(np.sqrt(max(mh * mw, 1)))
            k = max(ms // 10, 10)
            mask = cv2.erode(mask, np.ones((k, k), np.float32))
            k = max(ms // 20, 5)
            mask = cv2.GaussianBlur(mask, (k * 2 + 1, k * 2 + 1), 0)

        mask = np.clip(mask, 0, 1)[:, :, None]
        out = mask * fake_full.astype(np.float32) + (1 - mask) * frame.astype(np.float32)
        return out.astype(np.uint8)

    def _poisson_blend(self, swapped: np.ndarray, original: np.ndarray,
                       M, fake: np.ndarray) -> np.ndarray:
        """Gradient-domain (Poisson) blend of the swapped face into the original
        frame via cv2.seamlessClone — the Deep-Live-Cam realism step. The mask is
        an eroded ellipse warped from the swap's own affine, so it tracks the
        swapped face exactly and sits on solidly-swapped pixels (no seam, matches
        surrounding lighting). Composites only the face region back."""
        import cv2

        try:
            h, w = swapped.shape[:2]
            fh, fw = fake.shape[:2]
            inv = cv2.invertAffineTransform(M)
            em = np.zeros((fh, fw), np.uint8)
            cv2.ellipse(em, (fw // 2, fh // 2),
                        (int(fw * 0.44), int(fh * 0.44)), 0, 0, 360, 255, -1)
            full = cv2.warpAffine(em, inv, (w, h),
                                  flags=cv2.INTER_NEAREST, borderValue=0)
            ys, xs = np.where(full > 127)
            if len(ys) < 20:
                return swapped
            side = min(int(xs.max() - xs.min()), int(ys.max() - ys.min()))
            k = max(3, (side // 20) | 1)
            full = cv2.erode(full, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
            ys, xs = np.where(full > 127)
            if len(ys) < 20:
                return swapped
            x1, x2, y1, y2 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
            # seamlessClone needs the cloned region strictly off the frame border.
            if x1 <= 0 or y1 <= 0 or x2 >= w - 1 or y2 >= h - 1:
                return swapped
            center = ((x1 + x2) // 2, (y1 + y2) // 2)
            blended = cv2.seamlessClone(swapped, original, full, center, cv2.NORMAL_CLONE)
            np.copyto(swapped[y1:y2 + 1, x1:x2 + 1], blended[y1:y2 + 1, x1:x2 + 1],
                      where=full[y1:y2 + 1, x1:x2 + 1, None].astype(bool))
            return swapped
        except Exception as exc:
            log.debug("poisson blend skipped: %s", exc)
            return swapped

    def _apply_mouth(self, frame: np.ndarray, original: np.ndarray, face) -> np.ndarray:
        """Paste the user's REAL lower mouth (from the pre-swap frame) back over
        the swapped face, feathered — so talking/teeth look natural (Deep-Live-Cam
        mouth mask). Uses insightface 2d-106 lip landmarks (52..63)."""
        import cv2

        try:
            lm = getattr(face, "landmark_2d_106", None)
            if lm is None or not isinstance(lm, np.ndarray) or lm.shape[0] < 64:
                return frame
            pts = lm[52:64].astype(np.float32)
            if not np.all(np.isfinite(pts)):
                return frame
            center = pts.mean(axis=0)
            s = float(np.clip(self.mouth_expand, 0, 1))
            ef = 1.0 + s * 2.0
            off = pts - center
            exp = pts.copy()
            exp[:, 0] = center[0] + off[:, 0] * ef
            exp[:, 1] = center[1] + off[:, 1] * np.where(off[:, 1] > 0, ef * (1.0 + s), ef)
            exp = exp.astype(np.int32)
            H, W = frame.shape[:2]
            minx, miny = exp.min(axis=0)
            maxx, maxy = exp.max(axis=0)
            px, py = int((maxx - minx) * 0.1), int((maxy - miny) * 0.1)
            minx, miny = max(0, minx - px), max(0, miny - py)
            maxx, maxy = min(W, maxx + px), min(H, maxy + py)
            if maxx - minx < 4 or maxy - miny < 4:
                return frame
            cut = original[miny:maxy, minx:maxx]
            poly = (exp - [minx, miny]).astype(np.int32)
            m = np.zeros((maxy - miny, maxx - minx), np.uint8)
            cv2.fillPoly(m, [poly], 255)
            feather = max(1, min(30, min(maxx - minx, maxy - miny) // 8))
            m = cv2.GaussianBlur(m.astype(np.float32), (2 * feather + 1, 2 * feather + 1), 0)
            mx = float(m.max())
            if mx < 1e-6:
                return frame
            m = (m / mx)[:, :, None]
            roi = frame[miny:maxy, minx:maxx].astype(np.float32)
            frame[miny:maxy, minx:maxx] = np.clip(
                cut.astype(np.float32) * m + roi * (1 - m), 0, 255).astype(np.uint8)
            return frame
        except Exception as exc:
            log.debug("mouth mask skipped: %s", exc)
            return frame

    @staticmethod
    def _head_M(kps):
        """Similarity transform mapping a face's 5 keypoints onto the canonical
        head template. Returns a 2x3 affine, or None."""
        import cv2

        try:
            kps = np.asarray(kps, np.float32)
            if kps.shape != (5, 2) or not np.all(np.isfinite(kps)):
                return None
            M, _ = cv2.estimateAffinePartial2D(kps, _head_dst(), method=cv2.LMEDS)
            return M
        except Exception:
            return None

    def _prepare_head(self) -> None:
        """Align the source photo's head into the canonical canvas and cache the
        hair+scalp+ears mask (everything that ISN'T the face — the part inswapper
        can't transfer). Needs BiSeNet; no-op otherwise."""
        self._head_canon = None
        self._head_mask = None
        if not self.swap_hair or self._source_image is None or self._source_face is None:
            return
        try:
            import cv2

            from app.engines.face.face_parser import face_parser, parser_available

            if not parser_available():
                log.info("Hair transfer needs the BiSeNet parser model; skipping.")
                return
            M = self._head_M(self._source_face.kps)
            if M is None:
                return
            canon = cv2.warpAffine(self._source_image, M, (HEAD_SIZE, HEAD_SIZE),
                                   borderValue=0)
            head = face_parser.mask_512(canon, include_hair=True)   # face + hair + ears
            face_m = face_parser.mask_512(canon, include_hair=False)  # face only
            if head is None:
                return
            head = cv2.resize(head, (HEAD_SIZE, HEAD_SIZE))
            if face_m is not None:
                face_m = cv2.resize(face_m, (HEAD_SIZE, HEAD_SIZE))
                tmask = np.clip(head - face_m, 0.0, 1.0)  # hair/scalp/ears, not face
            else:
                tmask = head
            self._head_canon = canon
            self._head_mask = tmask.astype(np.float32)
            log.info("Hair transfer: source head prepared (coverage=%.2f)",
                     float(tmask.mean()))
        except Exception as exc:
            log.debug("prepare head skipped: %s", exc)

    def _transfer_head(self, frame: np.ndarray, face) -> np.ndarray:
        """Composite the cached source hair/scalp onto the user's head, aligned to
        this face via the keypoint similarity transform and feathered. Transfers
        the photo's hairstyle when it has hair, or its bald scalp when it doesn't."""
        import cv2

        if self._head_canon is None or self._head_mask is None:
            return frame
        try:
            M = self._head_M(face.kps)
            if M is None:
                return frame
            inv = cv2.invertAffineTransform(M)
            h, w = frame.shape[:2]
            head = cv2.warpAffine(self._head_canon, inv, (w, h), borderValue=0)
            m = cv2.warpAffine(self._head_mask, inv, (w, h), borderValue=0)
            m = cv2.GaussianBlur(m, (0, 0), sigmaX=max(w, h) * 0.006)
            m = np.clip(m, 0, 1)[:, :, None]
            out = m * head.astype(np.float32) + (1 - m) * frame.astype(np.float32)
            return out.astype(np.uint8)
        except Exception as exc:
            log.debug("head transfer skipped: %s", exc)
            return frame

    @staticmethod
    def _clamp_box(bbox, shape) -> tuple[int, int, int, int] | None:
        h, w = shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        return x1, y1, x2, y2

    def _match_to_source(self, frame: np.ndarray, box) -> None:
        """Recolour the swapped face to the SOURCE photo's exact complexion, so
        the result shows the photo's real skin tone/brightness — not darkened by
        the user's room lighting."""
        import cv2

        x1, y1, x2, y2 = box
        swapped = frame[y1:y2, x1:x2]
        h, w = swapped.shape[:2]
        mask = np.zeros((h, w), np.float32)
        cv2.ellipse(mask, (w // 2, h // 2), (int(w * 0.42), int(h * 0.5)), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=max(w, h) * 0.06)
        sel = mask > 0.3
        if int(sel.sum()) < 20:
            return
        lab_s = cv2.cvtColor(swapped, cv2.COLOR_BGR2LAB).astype(np.float32)
        strength = float(np.clip(self.skin_match, 0, 1))
        for c in range(3):
            shift = self._source_color[c] - lab_s[..., c][sel].mean()
            lab_s[..., c] += shift * strength * mask
        frame[y1:y2, x1:x2] = cv2.cvtColor(
            np.clip(lab_s, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    def _blend_neck(self, frame: np.ndarray, box) -> None:
        """Carry the swapped face's complexion down into the neck/upper-chest
        skin so there's no colour seam at the jaw — the face and body read as one
        person, even when the head turns. Targets skin only (leaves clothes),
        with a gradient that's strong at the jaw and fades downward."""
        import cv2

        x1, y1, x2, y2 = box
        fh, fw = y2 - y1, x2 - x1
        ny1 = max(0, y2 - int(fh * 0.12))
        ny2 = min(frame.shape[0], y2 + int(fh * 1.1))
        nx1 = max(0, x1 - int(fw * 0.2))
        nx2 = min(frame.shape[1], x2 + int(fw * 0.2))
        if ny2 - ny1 < 8 or nx2 - nx1 < 8:
            return
        roi = frame[ny1:ny2, nx1:nx2]
        h, w = roi.shape[:2]
        # Skin mask (YCrCb) so we don't recolour clothes/background.
        ycc = cv2.cvtColor(roi, cv2.COLOR_BGR2YCrCb)
        cr, cb = ycc[:, :, 1].astype(int), ycc[:, :, 2].astype(int)
        skin = ((cr > 133) & (cr < 180) & (cb > 77) & (cb < 130)).astype(np.float32)
        grad = np.linspace(1.0, 0.0, h)[:, None]          # strong at jaw, fade down
        mask = cv2.GaussianBlur(skin * grad, (0, 0), sigmaX=max(w, h) * 0.05)
        sel = mask > 0.12
        if int(sel.sum()) < 30:
            return
        lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
        strength = float(np.clip(self.skin_match, 0, 1)) * 0.85
        for c in range(3):
            shift = self._source_color[c] - lab[..., c][sel].mean()
            lab[..., c] += shift * strength * mask
        frame[ny1:ny2, nx1:nx2] = cv2.cvtColor(
            np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    def _compute_face_color(self, image_bgr: np.ndarray, bbox) -> None:
        """Cache the source photo's face-oval LAB mean (its complexion)."""
        import cv2

        box = self._clamp_box(bbox, image_bgr.shape)
        if not box:
            self._source_color = None
            return
        x1, y1, x2, y2 = box
        crop = image_bgr[y1:y2, x1:x2]
        h, w = crop.shape[:2]
        mask = np.zeros((h, w), np.uint8)
        cv2.ellipse(mask, (w // 2, h // 2), (int(w * 0.42), int(h * 0.5)), 0, 0, 360, 255, -1)
        sel = mask > 0
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
        self._source_color = [float(lab[..., c][sel].mean()) for c in range(3)]

    @staticmethod
    def download_model() -> bool:
        """Fetch inswapper_128.onnx if missing. Returns True if present after."""
        if INSWAPPER_FILE.exists():
            return True
        try:
            import urllib.request

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            log.info("Downloading inswapper model from %s", INSWAPPER_URL)
            urllib.request.urlretrieve(INSWAPPER_URL, INSWAPPER_FILE)
            return INSWAPPER_FILE.exists()
        except Exception as exc:
            log.error("Failed to download inswapper model: %s", exc)
            return False
