"""Real-time media pipeline.

Owns the physical camera and runs every frame through the processing chain:

    capture -> enhancement -> face engine -> AI-GENERATED badge -> output

The processed stream is what all consumers see: the UI preview (via WebSocket)
today, and the virtual camera device in Phase 4. Apps like WhatsApp, Telegram,
Zoom, etc. will only ever receive this processed, labeled output — never the
raw camera.

Runs on a worker thread so FastAPI stays responsive. Designed to degrade
gracefully on weak hardware: processing happens at ``proc_width`` and is scaled
back up, so low-end PCs drop resolution instead of dropping frames.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.logging import get_logger

# Light unsharp-mask strength applied to Lucy's cloud output — crisps eyes/mouth/
# edges without the halo of a heavy pass. Tune with APEXCAM_LUCY_SHARPEN.
LUCY_SHARPEN = float(os.environ.get("APEXCAM_LUCY_SHARPEN", "0.5"))
from app.engines.base import FrameEngine
from app.engines.face import PassthroughFaceEngine
from app.engines.face.tracker import FaceTracker
from app.engines.face.swapper import FaceSwapEngine
from app.engines.face.neural_swap import NeuralFaceSwapEngine, neural_swap_available
from app.engines.lipsync.procedural import ProceduralLipSync
from app.streaming.virtual_camera import VirtualCamera

log = get_logger(__name__)

# Substrings that mark a camera as VIRTUAL or infrared — never a valid AI input.
# Reading a virtual cam (esp. YouCam, which many customers run as a WhatsApp
# bridge) feeds our own output back in = a mirror-loop of "both images". IR
# cameras (Windows Hello) give a washed monochrome frame. Skip them all and pick
# the real color webcam.
VIRTUAL_CAM_HINTS = (
    "youcam", "cyberlink", "perfectcam", "apex cam", "obs", "unity",
    "virtual", "manycam", "xsplit", "splitcam", "snap camera", "snapcam",
    "nvidia broadcast", "e2esoft", "vcam", "droidcam", "iriun", "ivcam",
    "streamlabs", "restream", "wirecast", "infrared", "ir camera",
)


def enumerate_cameras() -> list[tuple[int, str]]:
    """(index, name) for each DirectShow camera, in OpenCV's index order.
    Empty list if enumeration is unavailable (we then fall back to index 0)."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        return list(enumerate(FilterGraph().get_input_devices()))
    except Exception as exc:
        log.warning("Camera enumeration unavailable: %s", exc)
        return []


def pick_real_camera_index() -> int:
    """The index of the first REAL (non-virtual, non-IR) color camera. Honors an
    explicit APEXCAM_CAMERA_INDEX override; falls back to 0 if nothing is named."""
    env = os.environ.get("APEXCAM_CAMERA_INDEX")
    if env not in (None, ""):
        try:
            return int(env)
        except ValueError:
            pass
    cams = enumerate_cameras()
    if not cams:
        return 0
    for i, name in cams:
        low = (name or "").lower()
        if not any(h in low for h in VIRTUAL_CAM_HINTS):
            log.info("Auto-selected real camera %d: %s", i, name)
            return i
    log.warning("Only virtual cameras found (%s); using index %d",
                [n for _, n in cams], cams[0][0])
    return cams[0][0]


@dataclass
class EnhanceSettings:
    """1.0 = neutral for every field."""

    brightness: float = 1.12
    contrast: float = 1.1
    saturation: float = 1.08


@dataclass
class PipelineStats:
    running: bool = False
    fps: float = 0.0
    latency_ms: float = 0.0
    frames: int = 0
    camera_index: int = 0
    proc_width: int = 0
    error: str | None = None
    vcam_active: bool = False
    vcam_device: str | None = None
    vcam_error: str | None = None
    faces_detected: int = 0


@dataclass
class _Shared:
    """State shared between the worker thread and API/WS handlers."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    raw_jpeg: bytes | None = None
    out_jpeg: bytes | None = None
    stats: PipelineStats = field(default_factory=PipelineStats)


class Pipeline:
    PREVIEW_WIDTH = 640
    JPEG_QUALITY = 70

    def __init__(self) -> None:
        self._shared = _Shared()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.enhance = EnhanceSettings()
        self.face_engine: FrameEngine = PassthroughFaceEngine("passthrough")
        self.face_engine.load()
        self.tracker = FaceTracker()
        # Tracking on by default so users immediately see the AI detect them;
        # overlay boxes can be toggled off for a clean output.
        self.tracking_enabled = True
        self.show_overlay = True
        self.latest_faces: list = []
        # The swapper analyses target photos with its OWN detector instance —
        # OpenCV's FaceDetectorYN isn't thread-safe, and the live pipeline is
        # already using self.tracker on the worker thread.
        self.swapper = FaceSwapEngine(FaceTracker())
        self.swap_enabled = False
        self.lipsync = ProceduralLipSync()
        self.lip_sync_enabled = True  # mouth moves with your voice (needs mic on)

        # Realistic neural swapper (InsightFace inswapper). Lazy-loaded the first
        # time neural mode is used, so startup stays fast and light. "fast" is
        # the CPU landmark paste; "neural" is the photorealistic model.
        self.neural_swapper = NeuralFaceSwapEngine()
        self.neural_available = neural_swap_available()
        self.swap_mode = "fast"  # "fast" | "neural" | "avatar"
        self.sharpen = 0.0  # 0..1.5 unsharp-mask strength on the output
        from app.engines.face.liveportrait import liveportrait_available
        self.avatar_available = liveportrait_available()
        log.info("Neural swap available: %s", self.neural_available)
        # Capture at 720p: the local swap still downscales to proc_width for CPU,
        # but Lucy (cloud) is fed the FULL frame — a sharp 720p input is what makes
        # its output crisp instead of the soft 640x360 the webcam defaults to.
        self.capture_width = int(os.environ.get("APEXCAM_CAPTURE_W", "1280"))
        self.capture_height = int(os.environ.get("APEXCAM_CAPTURE_H", "720"))
        self.proc_width = 640
        self._frame_i = 0
        self.detect_every = 2  # run face detection every Nth frame, reuse boxes
        self.vcam = VirtualCamera()
        self._vcam_requested = False
        # Media Foundation camera (WhatsApp/Windows Camera can see this one).
        from app.streaming.mf_camera import MFCamera
        self.mf_cam = MFCamera()

    # -- public API ---------------------------------------------------------

    def start(self, camera_index: int = -1) -> PipelineStats:
        if self._thread and self._thread.is_alive():
            return self.stats()
        # -1 (the default) = auto-pick the real color webcam, skipping YouCam and
        # any other virtual/IR device. An explicit index from the UI is honored.
        if camera_index < 0:
            camera_index = pick_real_camera_index()
        self._stop.clear()
        with self._shared.lock:
            self._shared.stats = PipelineStats(running=True, camera_index=camera_index,
                                               proc_width=self.proc_width)
        # Reset background matting temporal state for a clean start.
        try:
            from app.engines.background import background
            background.reset_state()
        except Exception:
            pass
        # Start the lightweight mic monitor so lip-sync can hear the user even if
        # the virtual microphone isn't running.
        try:
            from app.core.audio_pipeline import mic_level_monitor
            mic_level_monitor.start()
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, args=(camera_index,), daemon=True)
        self._thread.start()
        return self.stats()

    def stop(self) -> PipelineStats:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.disable_vcam()
        try:
            from app.core.audio_pipeline import mic_level_monitor
            mic_level_monitor.stop()
        except Exception:
            pass
        with self._shared.lock:
            self._shared.stats.running = False
            self._shared.raw_jpeg = None
            self._shared.out_jpeg = None
        return self.stats()

    @property
    def neural_active(self) -> bool:
        return self.swap_mode == "neural" and self.neural_swapper._loaded

    @property
    def swap_backend(self) -> str:
        return "neural" if self.neural_active else "landmark"

    def _ensure_neural(self) -> bool:
        """Lazy-load the neural swapper on first use. Returns True if ready."""
        if not self.neural_available:
            return False
        if not self.neural_swapper._loaded:
            self.neural_swapper.load()
        return self.neural_swapper._loaded

    def set_swap(
        self,
        enabled: bool,
        target_image_path: str | None,
        target_landmarks: list | None = None,
    ) -> bool:
        """Enable/disable face swap and (re)load the target face on whichever
        backend is active. Returns whether a target face is ready."""
        self.swap_enabled = enabled
        if not enabled:
            return False
        img = cv2.imread(target_image_path) if target_image_path else None
        if self.swap_mode == "avatar" and self._ensure_neural():
            from app.engines.face.liveportrait import liveportrait
            liveportrait.load()
            return liveportrait.set_source(img, self.neural_swapper._app)
        if self.swap_mode == "neural" and self._ensure_neural():
            # Neural swapper runs its own detector; landmarks are ignored.
            return self.neural_swapper.set_target(img)
        if img is not None:
            return self.swapper.set_target(img, target_landmarks)
        return self.swapper.ready

    def set_enhancer(self, kind: str) -> str:
        """Select the ONNX face restorer ('none'|'gfpgan'|'codeformer') on the
        neural swapper. Runs on onnxruntime (CPU or GPU)."""
        if not self.neural_available:
            return "none"
        self._ensure_neural()
        return self.neural_swapper.set_enhancer(kind)

    def set_swap_mode(self, mode: str) -> bool:
        """Switch swap engine: 'fast' (landmark), 'neural' (realistic swap),
        'avatar' (LivePortrait animation). Loading is lazy."""
        if mode not in ("fast", "neural", "avatar"):
            return False
        self.swap_mode = mode
        if mode == "neural":
            return self._ensure_neural()
        if mode == "avatar":
            from app.engines.face.liveportrait import liveportrait
            return self._ensure_neural() and liveportrait.load()
        return True

    def enable_vcam(self) -> None:
        """Ask the worker to open the virtual camera on the next frame."""
        self._vcam_requested = True
        # The Media Foundation camera (the one WhatsApp could see) is OFF by default:
        # the current third-party bridge lags and can hang the calling app. We use
        # the stable DirectShow / OBS virtual camera instead (works in OBS, Zoom,
        # Meet, Teams, Discord). Set APEXCAM_MF_CAMERA=1 to try the WhatsApp bridge.
        import os
        if os.environ.get("APEXCAM_MF_CAMERA", "0") not in ("0", "false", ""):
            try:
                self.mf_cam.start()
            except Exception:
                log.exception("MF camera start failed")
        with self._shared.lock:
            self._shared.stats.vcam_error = None

    def disable_vcam(self) -> None:
        self._vcam_requested = False
        self.vcam.close()
        self.mf_cam.close()
        with self._shared.lock:
            self._shared.stats.vcam_active = False
            self._shared.stats.vcam_device = None

    def stats(self) -> PipelineStats:
        with self._shared.lock:
            return PipelineStats(**vars(self._shared.stats))

    def previews(self) -> tuple[bytes | None, bytes | None]:
        with self._shared.lock:
            return self._shared.raw_jpeg, self._shared.out_jpeg

    # -- worker -------------------------------------------------------------

    def _open_camera(self, camera_index: int):
        """Open the webcam and make sure it delivers a REAL image — not the coloured
        static a broken format negotiation produces (which happens when the device was
        just released by the UI preview and Windows hasn't fully freed it). Warms past
        torn/black startup frames, and if it sees static, reopens — trying DirectShow
        then Media Foundation — until the feed is clean."""
        backends = (cv2.CAP_DSHOW, cv2.CAP_MSMF)
        cap = None
        for attempt in range(4):
            backend = backends[attempt % len(backends)]
            name = "DSHOW" if backend == cv2.CAP_DSHOW else "MSMF"
            cap = cv2.VideoCapture(camera_index, backend)
            if self.capture_width and self.capture_height:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_height)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # lowest latency
            except Exception:
                pass
            if not cap.isOpened():
                cap.release()
                time.sleep(0.4)
                continue
            # Warm-up: discard torn/black startup frames until a few good ones.
            good = 0
            for _ in range(30):
                if self._stop.is_set():
                    return cap
                ok, f = cap.read()
                if ok and f is not None and f.size and f.ndim == 3:
                    good += 1
                    if good >= 4:
                        break
                else:
                    good = 0
                time.sleep(0.015)
            # Static check: coloured noise changes completely every frame (huge
            # frame-to-frame difference) and is near-uniformly bright; a real image —
            # even a dark room — barely differs between two quick back-to-back reads.
            ok1, a = cap.read()
            ok2, b = cap.read()
            if ok1 and ok2 and a is not None and b is not None and a.shape == b.shape:
                diff = float(np.mean(cv2.absdiff(a, b)))
                if float(a.std()) > 65 and diff > 32:
                    log.warning("Camera %d gave static on %s (std=%.0f diff=%.0f); reopening",
                                camera_index, name, a.std(), diff)
                    cap.release()
                    time.sleep(0.6)
                    continue
            log.info("Camera %d opened cleanly (%s)", camera_index, name)
            return cap
        return cap   # best effort — return the last capture even if imperfect

    def _run(self, camera_index: int) -> None:
        log.info("Pipeline starting on camera %d", camera_index)
        cap = self._open_camera(camera_index)
        if cap is None or not cap.isOpened():
            with self._shared.lock:
                self._shared.stats.running = False
                self._shared.stats.error = (
                    f"Could not open camera {camera_index}. "
                    "Close other apps using it (including the UI preview) and retry."
                )
            log.error("Pipeline: camera %d could not be opened", camera_index)
            return

        fps_ema = 0.0
        last = time.perf_counter()
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None or frame.ndim != 3 or not frame.size:
                    time.sleep(0.01)   # skip a dropped/torn frame rather than show it
                    continue
                t0 = time.perf_counter()

                processed = self._process(frame)
                self._pump_vcam(processed)

                now = time.perf_counter()
                latency_ms = (now - t0) * 1000
                inst_fps = 1.0 / max(now - last, 1e-6)
                last = now
                fps_ema = inst_fps if fps_ema == 0 else fps_ema * 0.9 + inst_fps * 0.1

                raw_jpeg = self._to_preview_jpeg(frame)
                out_jpeg = self._to_preview_jpeg(processed)
                with self._shared.lock:
                    s = self._shared.stats
                    s.fps = fps_ema
                    s.latency_ms = latency_ms
                    s.frames += 1
                    s.error = None
                    self._shared.raw_jpeg = raw_jpeg
                    self._shared.out_jpeg = out_jpeg
        finally:
            cap.release()
            self.vcam.close()
            self.mf_cam.close()
            log.info("Pipeline stopped after %d frames", self.stats().frames)

    def _pump_vcam(self, processed: np.ndarray) -> None:
        """Publish the processed frame to the virtual camera device, opening it
        lazily once the frame size is known."""
        if not self._vcam_requested:
            return
        try:
            if not self.vcam.active:
                h, w = processed.shape[:2]
                self.vcam.open(w, h, 30)
                with self._shared.lock:
                    self._shared.stats.vcam_active = True
                    self._shared.stats.vcam_device = self.vcam.device
            # A non-contiguous buffer is read with the wrong row stride downstream
            # and shows as a diagonal "zig-zag" tear — force a clean C-order copy.
            clean = np.ascontiguousarray(processed)
            self.vcam.send(clean)
            # Same frame to the Media Foundation "Apex Cam" (WhatsApp/Windows Camera).
            self.mf_cam.send(clean)
        except Exception as exc:
            self._vcam_requested = False
            self.vcam.close()
            with self._shared.lock:
                self._shared.stats.vcam_active = False
                self._shared.stats.vcam_device = None
                self._shared.stats.vcam_error = str(exc)
            log.error("Virtual camera failed: %s", exc)

    def _process(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]

        # Downscale for processing so weak machines lose resolution, not FPS.
        if w > self.proc_width:
            ph = int(h * self.proc_width / w)
            work = cv2.resize(frame, (self.proc_width, ph), interpolation=cv2.INTER_AREA)
        else:
            work = frame.copy()

        work = self._apply_enhance(work)

        # Apex Cam Pro (Lucy cloud) — the transform happens in the cloud. For the
        # SHARPEST result: feed Lucy the FULL-RES camera frame (not the proc_width
        # downscale — that softened its input), keep its native output resolution
        # (no shrink back to the small camera size), and add a light unsharp pass
        # to crisp the eyes/mouth/edges. Lucy renders at its native size regardless.
        from app.engines.lucy_pro import lucy_pro
        if lucy_pro.ready:
            out = lucy_pro.process(frame)
            out = self._sharpen(out, max(self.sharpen, LUCY_SHARPEN))
            self._draw_badge(out)   # no-op unless label_output is enabled
            return out

        # Background matting (blur / green-screen / replace) — real-time on CPU.
        from app.engines.background import background
        if background.mode != "off":
            work = background.process(work)

        if self.tracking_enabled and self.tracker.available:
            # Detect every Nth frame and reuse boxes in between — roughly doubles
            # throughput on CPU while the swap/overlay still track smoothly.
            self._frame_i += 1
            if self._frame_i % self.detect_every == 0 or not self.latest_faces:
                faces = self.tracker.detect(work)
                self.latest_faces = faces
            else:
                faces = self.latest_faces
            with self._shared.lock:
                self._shared.stats.faces_detected = len(faces)
            if self.swap_enabled and self.swap_mode == "avatar":
                # Avatar path: animate the chosen photo with the user's motion.
                # Only the primary (largest) face — avoids duplicate/false faces.
                from app.engines.face.liveportrait import liveportrait
                if liveportrait.ready:
                    afaces = self.neural_swapper._app.get(work)
                    if afaces:
                        primary = max(afaces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
                        work = liveportrait.animate(work, primary)
            elif self.swap_enabled and self.neural_active and self.neural_swapper.ready:
                # Realistic path: inswapper keeps the user's pose/expression/
                # blink and swaps identity — no separate lip-sync needed.
                work = self.neural_swapper.swap_frame(work)
            elif self.swap_enabled and self.swapper.ready and faces:
                # CPU path: rigid landmark paste + audio-driven mouth movement.
                work = self.swapper.swap(work, faces)
                if self.lip_sync_enabled:
                    self.lipsync.update_level(self._current_audio_level())
                    work = self.lipsync.apply(work, faces)
            if self.show_overlay:
                self.tracker.draw_overlay(work, faces)
        else:
            self.latest_faces = []
            with self._shared.lock:
                self._shared.stats.faces_detected = 0

        work = self.face_engine.process(work)

        # Studio Beautify — cinematic tone, clean skin, HD crispness.
        from app.engines.beautify import beautify
        if beautify.level > 0.01:
            work = beautify.process(work, self.latest_faces)

        # Full-body pose skeleton overlay (optional).
        from app.engines.body_pose import body_pose
        if body_pose.enabled:
            work = body_pose.process(work)

        if self.sharpen > 0.01:
            work = self._apply_sharpen(work)

        if work.shape[1] != w:
            work = cv2.resize(work, (w, h), interpolation=cv2.INTER_LINEAR)

        self._draw_badge(work)
        return work

    def _apply_sharpen(self, img: np.ndarray) -> np.ndarray:
        """Fast unsharp-mask sharpening — a crispness boost that runs on CPU
        everywhere. (GFPGAN is the heavier GPU-grade face restorer.)"""
        return self._sharpen(img, self.sharpen)

    @staticmethod
    def _sharpen(img: np.ndarray, amount: float) -> np.ndarray:
        """Unsharp mask at a given strength. A fine radius (sigma 1.0) crisps
        eyes/mouth/edges without the halos a large radius creates."""
        if amount <= 0.01:
            return img
        blur = cv2.GaussianBlur(img, (0, 0), sigmaX=1.0)
        return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)

    def _current_audio_level(self) -> float:
        """Live mic loudness (0..1) for lip-sync — from the virtual-mic pipeline
        if it's running, else from the lightweight monitor."""
        try:
            from app.core.audio_pipeline import audio_pipeline, mic_level_monitor
            main = audio_pipeline.stats()
            if main.running:
                return main.level
            return mic_level_monitor.level
        except Exception:
            return 0.0

    def _apply_enhance(self, img: np.ndarray) -> np.ndarray:
        e = self.enhance
        # brightness/contrast: out = contrast * img + brightness_offset
        out = cv2.convertScaleAbs(img, alpha=e.contrast, beta=(e.brightness - 1.0) * 80)
        if abs(e.saturation - 1.0) > 0.01:
            hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * e.saturation, 0, 255)
            out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
        return out

    @staticmethod
    def _draw_badge(img: np.ndarray) -> None:
        """Optional responsible-use label. Off by default (settings.label_output);
        when on, burns a small "AI-GENERATED" disclosure into the output frame."""
        from app.config import settings
        if not settings.label_output:
            return
        text = "AI-GENERATED"
        scale = max(img.shape[1] / 1280, 0.5)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, 2)
        pad = int(8 * scale)
        x, y = int(16 * scale), int(16 * scale)
        overlay = img.copy()
        cv2.rectangle(overlay, (x, y), (x + tw + pad * 2, y + th + pad * 2), (77, 72, 229), -1)
        cv2.addWeighted(overlay, 0.85, img, 0.15, 0, img)
        cv2.putText(img, text, (x + pad, y + th + pad // 2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6 * scale, (255, 255, 255), 2, cv2.LINE_AA)

    def _to_preview_jpeg(self, img: np.ndarray) -> bytes:
        w = img.shape[1]
        if w > self.PREVIEW_WIDTH:
            h = int(img.shape[0] * self.PREVIEW_WIDTH / w)
            img = cv2.resize(img, (self.PREVIEW_WIDTH, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY])
        return buf.tobytes() if ok else b""


# Singleton used by the API routes.
pipeline = Pipeline()
