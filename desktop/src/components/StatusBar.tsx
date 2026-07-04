import { useEffect, useState } from "react";
import { api, type Health, type Metrics } from "../api/client";
import { usePipeline } from "../context/PipelineContext";

// Bottom status bar: backend connection + live FPS/latency/GPU counters, plus
// an always-visible emergency Reset so users can recover from any stuck state.
export function StatusBar() {
  const [health, setHealth] = useState<Health | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [connected, setConnected] = useState(false);
  const { resetAll, resetting } = usePipeline();

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [h, m] = await Promise.all([api.health(), api.metrics()]);
        if (!alive) return;
        setHealth(h);
        setMetrics(m);
        setConnected(true);
      } catch {
        if (alive) setConnected(false);
      }
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <footer className="statusbar">
      <span className={connected ? "dot ok" : "dot bad"} />
      <span>{connected ? `Backend ${health?.version ?? ""}` : "Backend offline"}</span>
      <span className="spacer" />
      <span>FPS {metrics?.fps.toFixed(0) ?? "—"}</span>
      <span>Latency {metrics?.latency_ms.toFixed(0) ?? "—"} ms</span>
      <span>GPU {metrics?.gpu_util.toFixed(0) ?? "—"}%</span>
      <button
        type="button"
        className="btn reset-btn"
        onClick={() => void resetAll()}
        disabled={resetting}
        title="Stop the camera, AI engine and virtual camera, and clear errors"
      >
        {resetting ? "Resetting…" : "⟳ Reset"}
      </button>
    </footer>
  );
}
