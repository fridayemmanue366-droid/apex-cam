import { useEffect, useRef, useState } from "react";
import {
  api,
  type AudioDevices,
  type AudioStatus,
  type VoiceParams,
} from "../api/client";

// Virtual microphone: the backend captures the mic, processes the voice, and
// publishes it to the virtual audio device (VB-CABLE Input). Call apps then
// pick "CABLE Output" as their microphone. Only processed audio is published.
export function AudioTab() {
  const [devices, setDevices] = useState<AudioDevices | null>(null);
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [params, setParams] = useState<VoiceParams | null>(null);
  const [inputDevice, setInputDevice] = useState<number | "">("");
  const [outputDevice, setOutputDevice] = useState<number | "">("");
  const meterRef = useRef<HTMLDivElement>(null);

  // Drive the level meter width imperatively (updates twice a second with status).
  useEffect(() => {
    if (meterRef.current) {
      meterRef.current.style.width = `${Math.round((status?.level ?? 0) * 100)}%`;
    }
  }, [status]);

  useEffect(() => {
    api.audioDevices().then((d) => {
      setDevices(d);
      if (d.cable_output !== null) setOutputDevice(d.cable_output);
    }).catch(() => setDevices(null));
    api.getVoiceParams().then(setParams).catch(() => setParams(null));

    const id = setInterval(() => {
      api.audioStatus().then(setStatus).catch(() => setStatus(null));
    }, 500);
    return () => clearInterval(id);
  }, []);

  const running = status?.running ?? false;
  const cableMissing = devices !== null && devices.cable_output === null;

  const start = () =>
    api
      .audioStart(inputDevice === "" ? null : inputDevice, outputDevice === "" ? null : outputDevice)
      .then(setStatus)
      .catch(() => undefined);

  const stop = () => api.audioStop().then(setStatus).catch(() => undefined);

  const updateParams = (patch: Partial<VoiceParams>) => {
    if (!params) return;
    const next = { ...params, ...patch };
    setParams(next);
    api.setVoiceParams(next).catch(() => undefined);
  };

  const PRESETS: { label: string; pitch: number }[] = [
    { label: "Deeper", pitch: -5 },
    { label: "Deep", pitch: -8 },
    { label: "Higher", pitch: 5 },
    { label: "Chipmunk", pitch: 9 },
    { label: "Neutral", pitch: 0 },
  ];

  return (
    <>
      <section className="panel">
        <h3>Virtual microphone</h3>
        {cableMissing && (
          <p className="error">
            No virtual audio device found. Install VB-CABLE (free) from vb-audio.com/Cable, then
            reopen this tab. You can still adjust settings below.
          </p>
        )}
        <label className="row">
          Microphone (input)
          <select
            value={inputDevice}
            disabled={running}
            onChange={(e) => setInputDevice(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">Default microphone</option>
            {devices?.inputs.map((d) => (
              <option key={d.index} value={d.index}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="row">
          Output to (virtual device)
          <select
            value={outputDevice}
            disabled={running}
            onChange={(e) => setOutputDevice(e.target.value === "" ? "" : Number(e.target.value))}
          >
            <option value="">Auto-detect CABLE Input</option>
            {devices?.outputs.map((d) => (
              <option key={d.index} value={d.index}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <div className="row">
          {running ? (
            <button type="button" className="btn danger" onClick={stop}>
              ■ Stop virtual microphone
            </button>
          ) : (
            <button type="button" className="btn primary" onClick={start}>
              🎙 Start virtual microphone
            </button>
          )}
          {running && <span className="pill ok">LIVE → {status?.output_name}</span>}
        </div>
        {status?.error && !cableMissing && <p className="error">{status.error}</p>}
        <p className="muted mt8">Output level:</p>
        <div className="meter">
          <div ref={meterRef} className="meter-fill" />
        </div>
      </section>

      <section className="panel">
        <h3>Voice processing</h3>
        {params ? (
          <>
            <label className="row">
              <input
                type="checkbox"
                checked={params.enabled}
                onChange={(e) => updateParams({ enabled: e.target.checked })}
              />
              Enable voice processing
            </label>
            <label className="row">
              <input
                type="checkbox"
                checked={params.noise_reduction}
                onChange={(e) => updateParams({ noise_reduction: e.target.checked })}
              />
              Noise reduction (noise gate)
            </label>
            <label className="row">
              Gain ({params.gain.toFixed(1)}×)
              <input
                type="range"
                min={0.5}
                max={3}
                step={0.1}
                value={params.gain}
                onChange={(e) => updateParams({ gain: Number(e.target.value) })}
              />
            </label>
            <label className="row">
              Pitch ({params.pitch > 0 ? "+" : ""}{params.pitch.toFixed(1)} semitones)
              <input
                type="range"
                min={-12}
                max={12}
                step={0.5}
                value={params.pitch}
                onChange={(e) => updateParams({ pitch: Number(e.target.value) })}
              />
            </label>
            <div className="row preset-row">
              {PRESETS.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  className={params.pitch === p.pitch ? "btn primary" : "btn"}
                  onClick={() => updateParams({ pitch: p.pitch })}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <p className="muted">
              Noise reduction, gain and <strong>real-time pitch shifting all work now</strong> —
              lower for a deeper voice, raise for a higher one. Full neural voice cloning
              (OpenVoice, XTTS, Fish Speech) is the GPU upgrade in a later pass; it slots in
              behind the same controls.
            </p>
          </>
        ) : (
          <p className="muted">Backend offline.</p>
        )}
      </section>

      <section className="panel">
        <h3>Use it in a call</h3>
        <p className="muted">
          Start the virtual microphone above, then in your call app (Zoom, WhatsApp, Telegram,
          Meet, Teams, Discord) choose <strong>“CABLE Output (VB-Audio Virtual Cable)”</strong> as
          the microphone. The app hears your processed voice — never the raw mic.
        </p>
      </section>
    </>
  );
}
