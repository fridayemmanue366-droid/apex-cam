import { useEffect, useRef, useState } from "react";
import { api, type SetupStatus } from "../api/client";

// Detects the user's GPU and lets them self-provision the realistic stack
// (onnxruntime-gpu + torch + GFPGAN) into this machine. Dormant on GPU-less PCs.
export function GpuSetupPanel() {
  const [s, setS] = useState<SetupStatus | null>(null);
  const timer = useRef<number>(0);

  const poll = () => api.setupStatus().then(setS).catch(() => setS(null));

  useEffect(() => {
    poll();
    timer.current = window.setInterval(poll, 2500);
    return () => window.clearInterval(timer.current);
  }, []);

  const install = async () => {
    await api.installGpu().catch(() => undefined);
    // Poll faster while installing.
    window.clearInterval(timer.current);
    timer.current = window.setInterval(poll, 1500);
  };

  if (!s) return null;

  return (
    <section className="panel wide">
      <h3>Realistic GPU mode</h3>
      <div className="kv">
        <span>Graphics card</span>
        <span>
          {s.has_nvidia_gpu ? (
            <span className="pill ok">{s.gpu_name}</span>
          ) : (
            <span className="pill">No NVIDIA GPU detected — {s.gpu_name ?? "integrated/CPU"}</span>
          )}
        </span>
        <span>AI runtime</span>
        <span>
          {s.on_gpu ? (
            <span className="pill ok">GPU acceleration active</span>
          ) : (
            <span className="pill">CPU ({s.onnx_providers.join(", ") || "none"})</span>
          )}
        </span>
        <span>Realistic swap</span>
        <span>{s.neural_available ? <span className="pill ok">Installed</span> : <span className="pill">Not installed</span>}</span>
        <span>GFPGAN enhancer</span>
        <span>{s.enhancer_available ? <span className="pill ok">Installed</span> : <span className="pill">Not installed</span>}</span>
      </div>

      <div className="row">
        <button
          type="button"
          className="btn primary"
          disabled={s.installing}
          onClick={() => void install()}
        >
          {s.installing ? "Installing…" : "⤓ Set up realistic GPU mode"}
        </button>
        {s.install_done && (
          <span className={s.install_ok ? "pill ok" : "pill"}>
            {s.install_ok ? "Setup complete" : "Finished with errors"}
          </span>
        )}
      </div>

      {(s.installing || s.install_done) && s.install_tail.length > 0 && (
        <pre className="setup-log">{s.install_tail.join("\n")}</pre>
      )}

      <p className="muted">
        This downloads and installs the realistic-swap GPU components into this machine
        (~2–3 GB). Best on an NVIDIA GPU with CUDA — on this laptop the realistic swap runs but
        slowly. See docs/GPU_SETUP.md. After setup completes, restart the AI engine.
      </p>
    </section>
  );
}
