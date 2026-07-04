import { usePipeline } from "../context/PipelineContext";

// Start/stop for the backend AI engine + Go Live (virtual camera), shown on
// Home and Camera tabs.
export function AiEngineControl() {
  const {
    active,
    starting,
    fps,
    latencyMs,
    error,
    vcamActive,
    vcamDevice,
    vcamError,
    start,
    stop,
    goLive,
    stopLive,
  } = usePipeline();

  return (
    <section className="panel">
      <h3>AI engine</h3>
      <div className="row">
        {active ? (
          <button type="button" className="btn danger" onClick={() => void stop()}>
            ■ Stop AI engine
          </button>
        ) : (
          <button
            type="button"
            className="btn primary"
            onClick={() => void start()}
            disabled={starting}
          >
            {starting ? "Starting…" : "▶ Start AI engine"}
          </button>
        )}
        {active && (
          <span className="pill ok">
            {fps.toFixed(0)} fps · {latencyMs.toFixed(0)} ms
          </span>
        )}
        {vcamActive ? (
          <button type="button" className="btn" onClick={() => void stopLive()}>
            Stop virtual camera
          </button>
        ) : (
          <button type="button" className="btn primary" onClick={() => void goLive()}>
            📡 Go Live (virtual camera)
          </button>
        )}
        {vcamActive && <span className="pill ok">LIVE — {vcamDevice}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {vcamError && <p className="error">{vcamError}</p>}
      <p className="muted">
        “Go Live” publishes the processed output as a system camera device that Zoom, WhatsApp,
        Telegram, Meet, Teams and OBS can select. Apps only ever receive this AI output (with
        its AI-GENERATED label) — never your raw camera.
      </p>
    </section>
  );
}
