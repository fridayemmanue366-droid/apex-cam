import { useEffect, useRef, useState } from "react";
import {
  api,
  type AudioDevices,
  type AudioStatus,
  type CloudVoiceStatus,
  type RVCStatus,
  type VoiceParams,
} from "../api/client";
import { cloud, getToken, signedIn, type Account } from "../api/cloud";

// Only one target voice is on the cloud service today — the picker grows
// when more are uploaded to the Modal volume (apexcam-voice-models/voices/).
const CLOUD_VOICES = [{ id: "GuraTalkV2", label: "Gura" }];

// Virtual microphone: the backend captures the mic, processes the voice, and
// publishes it to the virtual audio device (VB-CABLE Input). Call apps then
// pick "CABLE Output" as their microphone. Only processed audio is published.
export function AudioTab() {
  const [devices, setDevices] = useState<AudioDevices | null>(null);
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [params, setParams] = useState<VoiceParams | null>(null);
  const [inputDevice, setInputDevice] = useState<number | "">("");
  const [outputDevice, setOutputDevice] = useState<number | "">("");
  const [rvc, setRvcState] = useState<RVCStatus | null>(null);
  const meterRef = useRef<HTMLDivElement>(null);

  // --- Cloud voice (Apex Pro): same GPU RVC pipeline as the local engine
  // above, running on Modal so it works without a local GPU or downloaded
  // models. Server-metered like the rest of Apex Pro.
  const [cloudVoice, setCloudVoiceState] = useState<CloudVoiceStatus | null>(null);
  const [cloudSession, setCloudSession] = useState<string | null>(null);
  const [cloudVoiceId, setCloudVoiceId] = useState(CLOUD_VOICES[0].id);
  const [cloudPitch, setCloudPitch] = useState(0);
  const [cloudErr, setCloudErr] = useState<string | null>(null);
  const [cloudBusy, setCloudBusy] = useState(false);
  const [account, setAccount] = useState<Account | null>(null);

  const updateRvc = (patch: Partial<{ enabled: boolean; voice: string | null; pitch_shift: number }>) => {
    if (!rvc) return;
    const body = { enabled: rvc.enabled, voice: rvc.voice, pitch_shift: rvc.pitch_shift, ...patch };
    setRvcState({ ...rvc, ...patch });
    api.setRvc(body).then(setRvcState).catch(() => undefined);
    // Local RVC and cloud voice never run together (the backend enforces this
    // too) — if cloud voice's session is still open client-side, close it out
    // so the wallet session doesn't dangle.
    if (patch.enabled && cloudSession) {
      cloud.voiceCloudStop(cloudSession).catch(() => undefined);
      setCloudSession(null);
    }
  };

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
    api.getRvc().then(setRvcState).catch(() => setRvcState(null));
    api.audioCloudStatus().then(setCloudVoiceState).catch(() => setCloudVoiceState(null));
    if (signedIn()) cloud.me().then(setAccount).catch(() => setAccount(null));

    const id = setInterval(() => {
      api.audioStatus().then(setStatus).catch(() => setStatus(null));
      api.audioCloudStatus().then(setCloudVoiceState).catch(() => undefined);
    }, 500);
    return () => clearInterval(id);
  }, []);

  const toggleCloudVoice = async () => {
    setCloudErr(null);
    if (cloudVoice?.enabled) {
      setCloudBusy(true);
      try {
        await api.audioCloudStop().then(setCloudVoiceState).catch(() => undefined);
        if (cloudSession) await cloud.voiceCloudStop(cloudSession).catch(() => undefined);
      } finally {
        setCloudSession(null);
        setCloudBusy(false);
      }
      return;
    }
    if (!signedIn()) {
      setCloudErr("Sign in on the Apex Pro tab first — cloud voice is a Pro feature.");
      return;
    }
    if ((account?.credit_seconds ?? 0) <= 0) {
      setCloudErr("No credit — buy minutes on the Apex Pro tab first.");
      return;
    }
    setCloudBusy(true);
    try {
      const r = await cloud.voiceCloudStart();
      setCloudSession(r.session_id);
      const s = await api.audioCloudStart({
        ws_url: r.url,
        token: r.token,
        voice: cloudVoiceId,
        pitch_shift: cloudPitch,
        session_id: r.session_id,
        cloud_url: cloud.url,
        auth: getToken() ?? "",
      });
      setCloudVoiceState(s);
    } catch (e) {
      setCloudErr(e instanceof Error ? e.message : "Could not start cloud voice — try again");
      if (cloudSession) cloud.voiceCloudStop(cloudSession).catch(() => undefined);
      setCloudSession(null);
      await api.audioCloudStop().catch(() => undefined);
    } finally {
      setCloudBusy(false);
      cloud.me().then(setAccount).catch(() => undefined);
    }
  };

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
              lower for a deeper voice, raise for a higher one. For a full identity change (not
              just pitch), use <strong>Cloud Voice</strong> below or local Voice Cloning further
              down.
            </p>
          </>
        ) : (
          <p className="muted">Backend offline.</p>
        )}
      </section>

      <section className="panel">
        <h3>Cloud voice (Apex Pro)</h3>
        <p className="muted">
          Full voice cloning — changes your voice <em>identity</em>, not just pitch — running on
          our cloud GPU. Works on any laptop, no local model download and no GPU required. Metered
          from your Apex Pro credit, same wallet as the video/photo tools.
        </p>
        <label className="row">
          Target voice
          <select
            value={cloudVoiceId}
            disabled={!!cloudVoice?.enabled}
            onChange={(e) => setCloudVoiceId(e.target.value)}
          >
            {CLOUD_VOICES.map((v) => (
              <option key={v.id} value={v.id}>{v.label}</option>
            ))}
          </select>
        </label>
        <label className="row">
          Pitch ({cloudPitch > 0 ? "+" : ""}{cloudPitch})
          <input
            type="range"
            min={-12}
            max={12}
            step={1}
            value={cloudPitch}
            disabled={!!cloudVoice?.enabled}
            onChange={(e) => setCloudPitch(Number(e.target.value))}
          />
        </label>
        <div className="row">
          <button
            type="button"
            className={cloudVoice?.enabled ? "btn danger" : "btn primary"}
            disabled={cloudBusy}
            onClick={toggleCloudVoice}
          >
            {cloudVoice?.enabled ? "■ Stop cloud voice" : "☁ Start cloud voice"}
          </button>
          {cloudVoice?.enabled && (
            <span className={cloudVoice.connected ? "pill ok" : "pill"}>
              {cloudVoice.connected ? "LIVE" : "Connecting…"}
            </span>
          )}
        </div>
        {(cloudErr || cloudVoice?.error) && <p className="error">{cloudErr ?? cloudVoice?.error}</p>}
      </section>

      {rvc && (
        <section className="panel">
          <h3>Voice cloning (local)</h3>
          {!rvc.base_present || rvc.voices.length === 0 ? (
            <p className="muted">
              The same voice cloning as Cloud Voice above, but running on <strong>your own
              GPU</strong> instead of ours — free, but needs the shared models + a{" "}
              <strong>voice model per voice</strong> installed locally. Put{" "}
              <code>content_vec.onnx</code> and <code>rmvpe.onnx</code> in
              <code> backend/models/rvc/</code>, and voice models in
              <code> backend/models/rvc/voices/</code>. See docs/GPU_SETUP.md.
              {rvc.base_present && " (Base models found — add a voice model to enable.)"}
            </p>
          ) : (
            <>
              <label className="row">
                <input
                  type="checkbox"
                  checked={rvc.enabled}
                  onChange={(e) => updateRvc({ enabled: e.target.checked })}
                />
                Enable voice cloning{rvc.on_gpu ? " (GPU)" : " (CPU — not real-time)"}
              </label>
              <label className="row">
                Voice
                <select
                  value={rvc.voice ?? ""}
                  onChange={(e) => updateRvc({ voice: e.target.value || null })}
                >
                  <option value="">Select a voice…</option>
                  {rvc.voices.map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              </label>
              <label className="row">
                Pitch ({rvc.pitch_shift > 0 ? "+" : ""}{rvc.pitch_shift})
                <input
                  type="range"
                  min={-12}
                  max={12}
                  step={1}
                  value={rvc.pitch_shift}
                  onChange={(e) => updateRvc({ pitch_shift: Number(e.target.value) })}
                />
              </label>
              <p className="muted">
                Sounds like the selected voice while you talk. Real-time on GPU; on CPU it lags.
              </p>
            </>
          )}
        </section>
      )}

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
