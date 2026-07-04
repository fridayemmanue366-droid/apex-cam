import { useEffect, useRef, type MutableRefObject } from "react";
import { enhanceFilter, useMedia } from "../context/MediaContext";
import { usePipeline } from "../context/PipelineContext";
import { AiLabelBadge } from "./AiLabelBadge";

export type OutputMediaRef = MutableRefObject<HTMLVideoElement | HTMLImageElement | null>;

// Side-by-side view: the user's raw camera on the left, EMY CAM's output on the
// right. Two modes:
//  - Local mode: both panes play the UI's getUserMedia stream; the output pane
//    applies the CSS enhancement filter + badge overlay.
//  - AI mode (backend pipeline running): both panes show frames streamed from
//    the backend; the badge is already burned into the output pixels.
export function DualPreview({ outputMediaRef }: { outputMediaRef?: OutputMediaRef }) {
  const rawRef = useRef<HTMLVideoElement>(null);
  const outVideoRef = useRef<HTMLVideoElement>(null);
  const { stream, error, running, start, enhance, externalHold } = useMedia();
  const pipeline = usePipeline();

  useEffect(() => {
    if (pipeline.active) return;
    if (rawRef.current) rawRef.current.srcObject = stream;
    if (outVideoRef.current) outVideoRef.current.srcObject = stream;
    if (outputMediaRef && outVideoRef.current) outputMediaRef.current = outVideoRef.current;
  }, [stream, pipeline.active, outputMediaRef]);

  // The enhancement filter is user-adjustable at runtime, so it can't live in a
  // static stylesheet; apply it imperatively.
  useEffect(() => {
    if (outVideoRef.current) outVideoRef.current.style.filter = enhanceFilter(enhance);
  }, [enhance, stream, pipeline.active]);

  if (pipeline.active) {
    return (
      <div className="dual">
        <div className="dual-pane">
          <div className="pane-label">You — raw input (stays on this device)</div>
          {pipeline.rawSrc ? (
            <img src={pipeline.rawSrc} className="preview-video" alt="" />
          ) : (
            <div className="preview-idle">
              <span className="preview-hint">Starting camera… (a few seconds)</span>
            </div>
          )}
        </div>
        <div className="dual-pane">
          <div className="pane-label accent">EMY CAM output — what call apps receive</div>
          {pipeline.outSrc && (
            <img
              src={pipeline.outSrc}
              className="preview-video"
              alt=""
              ref={(el) => {
                if (outputMediaRef) outputMediaRef.current = el;
              }}
            />
          )}
        </div>
      </div>
    );
  }

  if (!stream) {
    return (
      <div className="preview-frame">
        <div className="preview-idle">
          {error ? (
            <span className="error">Camera error: {error}</span>
          ) : externalHold ? (
            <span className="preview-hint">Starting AI engine…</span>
          ) : (
            <>
              <span className="preview-hint">Camera is off</span>
              <button type="button" className="btn primary" onClick={start} disabled={running}>
                Start camera
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="dual">
      <div className="dual-pane">
        <div className="pane-label">You — raw input</div>
        <video ref={rawRef} autoPlay playsInline muted className="preview-video" />
      </div>
      <div className="dual-pane">
        <div className="pane-label accent">EMY CAM output</div>
        <AiLabelBadge />
        <video ref={outVideoRef} autoPlay playsInline muted className="preview-video" />
      </div>
    </div>
  );
}
