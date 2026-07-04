import { useEffect, useState } from "react";
import { api, type Capabilities, type Metrics } from "../api/client";

const PROC_WIDTHS = [
  { w: 640, label: "640 px — weakest PCs" },
  { w: 960, label: "960 px — CPU-only (recommended here)" },
  { w: 1280, label: "1280 px — mid GPU" },
  { w: 1920, label: "1920 px — strong GPU (full 1080p)" },
];

export function PerformanceTab() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [procWidth, setProcWidth] = useState(960);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
    api.getPerformance().then((p) => setProcWidth(p.proc_width)).catch(() => undefined);
    const id = setInterval(() => {
      api.metrics().then(setMetrics).catch(() => setMetrics(null));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const applyProcWidth = (w: number) => {
    setProcWidth(w);
    api.setPerformance({ proc_width: w }).catch(() => undefined);
  };

  return (
    <>
      <section className="panel">
        <h3>Live monitors</h3>
        <div className="stats">
          <div className="stat">
            <div className="stat-value">{metrics ? metrics.fps.toFixed(0) : "—"}</div>
            <div className="stat-label">Pipeline FPS</div>
          </div>
          <div className="stat">
            <div className="stat-value">{metrics ? metrics.latency_ms.toFixed(0) : "—"}</div>
            <div className="stat-label">Latency (ms)</div>
          </div>
          <div className="stat">
            <div className="stat-value">{metrics ? `${metrics.gpu_util.toFixed(0)}%` : "—"}</div>
            <div className="stat-label">GPU util</div>
          </div>
          <div className="stat">
            <div className="stat-value">{metrics ? metrics.gpu_mem_mb.toFixed(0) : "—"}</div>
            <div className="stat-label">GPU mem (MB)</div>
          </div>
        </div>
        <p className="muted">
          Counters read 0 until the AI media pipeline starts running (Phase 3+).
        </p>
      </section>

      <section className="panel">
        <h3>Processing quality</h3>
        <label className="row">
          AI processing resolution
          <select
            value={procWidth}
            onChange={(e) => applyProcWidth(Number(e.target.value))}
          >
            {PROC_WIDTHS.map((p) => (
              <option key={p.w} value={p.w}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">
          Frames are processed at this width and scaled back to full size, so weaker machines
          trade sharpness for smoothness instead of lagging. Users with strong GPUs should pick
          1280–1920 for maximum quality; changes apply live to the running engine.
        </p>
      </section>

      <section className="panel">
        <h3>Acceleration</h3>
        <div className="kv">
          <span>GPU</span>
          <span>{caps ? (caps.gpu.available ? caps.gpu.name : "Not detected (CPU mode)") : "—"}</span>
          <span>CUDA</span>
          <span>{caps?.gpu.available ? "Available" : "Unavailable"}</span>
          <span>GPU monitor</span>
          <span>Live via nvidia-smi on NVIDIA machines (shows 0 here)</span>
          <span>TensorRT / ONNX Runtime</span>
          <span>Configured in Phase 10 (optimization)</span>
        </div>
      </section>
    </>
  );
}
