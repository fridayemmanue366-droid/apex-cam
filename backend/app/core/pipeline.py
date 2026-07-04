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

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.logging import get_logger
from app.engines.base import FrameEngine
from app.engines.face import PassthroughFaceEngine
from app.engines.face.tracker import FaceTracker
from app.engines.face.swapper import FaceSwapEngine
from app.engines.face.neural_swap import NeuralFaceSwapEngine, neural_swap_available
from app.engines.lipsync.procedural import ProceduralLipSync
from app.streaming.virtual_camera import VirtualCamera

log = get_logger(__name__)


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
        self.swap_mode = "fast"  # "fast" | "neural"
        self.sharpen = 0.0  # 0..1.5 unsharp-mask strength on the output
        log.info("Neural swap available: %s", self.neural_available)
        # Fast, light capture defaults for CPU; the Performance tab / GPU builds
        # can raise processing resolution. capture_width=0 means "use the
        # camera's native default" — forcing a resolution roughly doubles the
        # DirectShow open time, so we avoid it unless explicitly overridden.
        self.capture_width = 0
        self.capture_height = 0
        self.proc_width = 640
        self._frame_i = 0
        self.detect_every = 2  # run face detection every Nth frame, reuse boxes
        self.vcam = VirtualCamera()
        self._vcam_requested = False

    # -- public API ---------------------------------------------------------

    def start(self, camera_index: int = 0) -> PipelineStats:
        if self._thread and self._thread.is_alive():
            return self.stats()
        self._stop.clear()
        with self._shared.lock:
            self._shared.stats = PipelineStats(running=True, camera_index=camera_index,
                                               proc_width=self.proc_width)
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
        """Switch between 'fast' (landmark) and 'neural' (realistic). Loading of
        the neural model is lazy; returns True if the requested mode is usable."""
        if mode not in ("fast", "neural"):
            return False
        self.swap_mode = mode
        if mode == "neural":
            return self._ensure_neural()
        return True

    def enable_vcam(self) -> None:
        """Ask the worker to open the virtual camera on the next frame."""
        self._vcam_requested = True
        with self._shared.lock:
            self._shared.stats.vcam_error = None

    def disable_vcam(self) -> None:
        self._vcam_requested = False
        self.vcam.close()
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

    def _run(self, camera_index: int) -> None:
        log.info("Pipeline starting on camera %d", camera_index)
        # DirectShow at 640x480 opens ~2x faster than forcing 720p on typical
        # webcams and keeps CPU processing light. GPU builds can raise this.
        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if self.capture_width and self.capture_height:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_height)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # lowest latency
        except Exception:
            pass

        if not cap.isOpened():
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
                if not ok:
                    time.sleep(0.01)
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
            self.vcam.send(processed)
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
            if self.swap_enabled and self.neural_active and self.neural_swapper.ready:
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

        if self.sharpen > 0.01:
            work = self._apply_sharpen(work)

        if work.shape[1] != w:
            work = cv2.resize(work, (w, h), interpolation=cv2.INTER_LINEAR)

        self._draw_badge(work)
        return work

    def _apply_sharpen(self, img: np.ndarray) -> np.ndarray:
        """Fast unsharp-mask sharpening — a crispness boost that runs on CPU
        everywhere. (GFPGAN is the heavier GPU-grade face restorer.)"""
        blur = cv2.GaussianBlur(img, (0, 0), sigmaX=1.2)
        return cv2.addWeighted(img, 1 + self.sharpen, blur, -self.sharpen, 0)

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
        """Non-optional responsible-use label, burned into every output frame."""
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
