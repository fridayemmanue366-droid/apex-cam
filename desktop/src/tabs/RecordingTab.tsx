import { useCallback, useEffect, useRef, useState } from "react";
import { DualPreview, type OutputMediaRef } from "../components/DualPreview";
import { enhanceFilter, useMedia } from "../context/MediaContext";
import { usePipeline } from "../context/PipelineContext";

// Records the EMY CAM output pane. In local mode the enhancement filter and the
// AI-GENERATED badge are composited here; in AI-engine mode the backend has
// already burned both into the frames, so they're captured as-is.
export function RecordingTab() {
  const outputRef = useRef<HTMLVideoElement | HTMLImageElement | null>(
    null,
  ) as OutputMediaRef;
  const recorderRef = useRef<MediaRecorder | null>(null);
  const rafRef = useRef(0);
  const { stream, enhance } = useMedia();
  const pipeline = usePipeline();
  const aiActiveRef = useRef(pipeline.active);
  aiActiveRef.current = pipeline.active;
  const enhanceRef = useRef(enhance);
  enhanceRef.current = enhance;
  const [recording, setRecording] = useState(false);
  const [lastFile, setLastFile] = useState<string | null>(null);

  const hasSource = pipeline.active || !!stream;

  const sourceSize = (): { w: number; h: number } => {
    const el = outputRef.current;
    if (!el) return { w: 0, h: 0 };
    if (el instanceof HTMLVideoElement) return { w: el.videoWidth, h: el.videoHeight };
    return { w: el.naturalWidth, h: el.naturalHeight };
  };

  const drawBadge = (ctx: CanvasRenderingContext2D) => {
    ctx.save();
    ctx.filter = "none";
    ctx.font = "bold 20px Segoe UI, sans-serif";
    const text = "● AI-GENERATED";
    const w = ctx.measureText(text).width + 24;
    ctx.fillStyle = "rgba(229,72,77,0.9)";
    ctx.beginPath();
    ctx.roundRect(16, 16, w, 36, 8);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.fillText(text, 28, 41);
    ctx.restore();
  };

  const drawFrame = (ctx: CanvasRenderingContext2D, width: number, height: number) => {
    const el = outputRef.current;
    if (!el) return;
    if (aiActiveRef.current) {
      // Backend frames already carry enhancement + badge.
      ctx.filter = "none";
      ctx.drawImage(el, 0, 0, width, height);
    } else {
      ctx.filter = enhanceFilter(enhanceRef.current);
      ctx.drawImage(el, 0, 0, width, height);
      drawBadge(ctx);
    }
  };

  const download = (blob: Blob, name: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
    setLastFile(name);
  };

  const screenshot = () => {
    const { w, h } = sourceSize();
    if (!w) return;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    drawFrame(canvas.getContext("2d")!, w, h);
    canvas.toBlob((b) => b && download(b, `emycam-${Date.now()}.png`), "image/png");
  };

  const stopRecording = useCallback(() => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  }, []);

  const startRecording = () => {
    const { w, h } = sourceSize();
    if (!w) return;
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d")!;

    const draw = () => {
      drawFrame(ctx, w, h);
      rafRef.current = requestAnimationFrame(draw);
    };
    draw();

    const out = canvas.captureStream(30);
    // Mic audio is only available in local mode (the backend owns audio later).
    stream?.getAudioTracks().forEach((t) => out.addTrack(t));

    const chunks: Blob[] = [];
    const rec = new MediaRecorder(out, { mimeType: "video/webm" });
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = () => {
      cancelAnimationFrame(rafRef.current);
      download(new Blob(chunks, { type: "video/webm" }), `emycam-${Date.now()}.webm`);
    };
    rec.start(250);
    recorderRef.current = rec;
    setRecording(true);
  };

  // End the recording cleanly if the source disappears (camera stopped, device
  // unplugged, AI engine stopped) instead of capturing a frozen frame.
  useEffect(() => {
    if (!recording) return;
    if (!hasSource) {
      stopRecording();
      return;
    }
    if (pipeline.active) return; // WS frames; stop is caught by !hasSource
    const videoTrack = stream?.getVideoTracks()[0];
    if (!videoTrack) {
      stopRecording();
      return;
    }
    videoTrack.addEventListener("ended", stopRecording);
    return () => videoTrack.removeEventListener("ended", stopRecording);
  }, [recording, hasSource, pipeline.active, stream, stopRecording]);

  useEffect(() => () => cancelAnimationFrame(rafRef.current), []);

  return (
    <>
      <section className="preview">
        <DualPreview outputMediaRef={outputRef} />
      </section>

      <section className="panel">
        <h3>Capture</h3>
        <div className="row">
          {recording ? (
            <button type="button" className="btn danger" onClick={stopRecording}>
              ■ Stop &amp; save recording
            </button>
          ) : (
            <button
              type="button"
              className="btn primary"
              onClick={startRecording}
              disabled={!hasSource}
            >
              ● Record
            </button>
          )}
          <button type="button" className="btn" onClick={screenshot} disabled={!hasSource}>
            Screenshot
          </button>
        </div>
        {lastFile && <p className="muted">Saved to Downloads: {lastFile}</p>}
        <p className="muted">
          Captures the EMY CAM output pane (AI-GENERATED label always included) as WebM.
          Recording stops automatically if the camera or AI engine is turned off.
        </p>
      </section>
    </>
  );
}
