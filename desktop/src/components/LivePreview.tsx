import { useEffect, useRef } from "react";
import { useMedia } from "../context/MediaContext";

// Live view of the shared capture stream with the AI-GENERATED badge overlay.
// `videoRef` can be passed in so other features (recording, screenshots) can
// read frames from the same element.
export function LivePreview({
  videoRef,
  muted = true,
}: {
  videoRef?: React.RefObject<HTMLVideoElement>;
  muted?: boolean;
}) {
  const localRef = useRef<HTMLVideoElement>(null);
  const ref = videoRef ?? localRef;
  const { stream, error, running, start } = useMedia();

  useEffect(() => {
    if (ref.current) ref.current.srcObject = stream;
  }, [stream, ref]);

  return (
    <div className="preview-frame">
      {stream ? (
        <video ref={ref} autoPlay playsInline muted={muted} className="preview-video" />
      ) : (
        <div className="preview-idle">
          {error ? (
            <span className="error">Camera error: {error}</span>
          ) : (
            <>
              <span className="preview-hint">Camera is off</span>
              <button className="btn primary" onClick={start} disabled={running}>
                Start camera
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
