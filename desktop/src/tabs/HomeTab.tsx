import { useEffect, useState } from "react";
import { api, type Capabilities } from "../api/client";
import { AiEngineControl } from "../components/AiEngineControl";
import { DualPreview } from "../components/DualPreview";
import { useMedia } from "../context/MediaContext";

export function HomeTab() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const { running, stop } = useMedia();

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
  }, []);

  return (
    <>
      <section className="preview">
        <DualPreview />
        {running && (
          <button type="button" className="btn stop-btn" onClick={stop}>
            Stop camera
          </button>
        )}
      </section>

      <AiEngineControl />

      <section className="panel">
        <h3>System</h3>
        <div className="kv">
          <span>GPU</span>
          <span>
            {caps
              ? caps.gpu.available
                ? caps.gpu.name
                : "Not detected — running on CPU (AI phases will be slow without CUDA)"
              : "Backend offline"}
          </span>
          <span>Target</span>
          <span>
            {caps
              ? `${caps.target.width}×${caps.target.height} @ ${caps.target.fps} fps`
              : "—"}
          </span>
          <span>Output labeling</span>
          <span>{caps?.responsible_use.label_output ? "On (always)" : "—"}</span>
        </div>
        <p className="muted">
          Roadmap: virtual camera/mic devices, face tracking &amp; swapping, voice conversion,
          and lip sync land in the next build phases. See the Streaming tab for how apps will
          pick up EMY CAM once the virtual devices exist.
        </p>
      </section>
    </>
  );
}
