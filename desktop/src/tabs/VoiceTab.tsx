import { useEffect, useState } from "react";
import { api, type VoiceState } from "../api/client";
import { MicMeter } from "../components/MicMeter";
import { useMedia } from "../context/MediaContext";

export function VoiceTab() {
  const [state, setState] = useState<VoiceState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { running, start } = useMedia();

  useEffect(() => {
    api.getVoice().then(setState).catch((e) => setErr(String(e)));
  }, []);

  const update = (patch: Partial<VoiceState>) => {
    if (!state) return;
    const next = { ...state, ...patch };
    setState(next);
    api.setVoice(next).catch((e) => setErr(String(e)));
  };

  return (
    <>
      <section className="panel">
        <h3>Microphone input</h3>
        {!running && (
          <button className="btn primary" onClick={start}>
            Start capture (camera + mic)
          </button>
        )}
        <MicMeter />
      </section>

      <section className="panel">
        <h3>Voice engine</h3>
        {err && <p className="error">{err}</p>}
        {state ? (
          <>
            <label className="row">
              <input
                type="checkbox"
                checked={state.enabled}
                onChange={(e) => update({ enabled: e.target.checked })}
              />
              Enable voice engine
            </label>
            <label className="row">
              <input
                type="checkbox"
                checked={state.noise_reduction}
                onChange={(e) => update({ noise_reduction: e.target.checked })}
              />
              Noise reduction
            </label>
            <label className="row">
              Pitch ({state.pitch.toFixed(1)})
              <input
                type="range"
                min={-12}
                max={12}
                step={0.5}
                value={state.pitch}
                onChange={(e) => update({ pitch: Number(e.target.value) })}
              />
            </label>
            <label className="row">
              Model
              <select value={state.model} onChange={(e) => update({ model: e.target.value })}>
                <option value="passthrough">Passthrough (Phase 1)</option>
              </select>
            </label>
            <p className="muted">
              Controls are wired to the backend now; audible voice cloning/conversion
              (OpenVoice, XTTS, Fish Speech) arrives in Phase 8.
            </p>
          </>
        ) : (
          <p className="muted">Backend offline.</p>
        )}
      </section>
    </>
  );
}
