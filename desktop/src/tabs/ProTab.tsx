import { useEffect, useRef, useState } from "react";
import { api, type ProStatus } from "../api/client";
import { DualPreview } from "../components/DualPreview";

// Apex Cam Pro — its own VIP universe: a full, bold, premium dashboard (not a
// sidebar panel). The cloud engine (Lucy 2.1) is billed by us; the provider key
// is server-side and never shown.
const LOOKS = [
  "Photorealistic 30-year-old man, studio lighting",
  "Photorealistic young woman, soft cinematic light",
  "Anime character, vibrant colors",
  "Realistic older gentleman, warm tone",
  "Fashion model, editorial lighting",
];

export function ProTab() {
  const [pro, setPro] = useState<ProStatus | null>(null);
  const [entered, setEntered] = useState(false);
  const [prompt, setPrompt] = useState("");
  const refFile = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.enabled) setEntered(true);
    }).catch(() => setPro(null));
  }, []);

  const save = (enabled: boolean, p = prompt) =>
    api.setPro({ enabled, prompt: p }).then(setPro).catch(() => undefined);
  const uploadRef = (f: File | undefined) => {
    if (f) api.uploadProReference(f).then(setPro).catch(() => undefined);
  };

  if (!pro) return <section className="panel"><p className="muted">Backend offline.</p></section>;

  // --- Entry gate (the "door" into the Pro universe) ---
  if (!entered) {
    return (
      <div className="pro">
        <div className="pro-hero">
          <span className="pro-crown">✦</span>
          <div>
            <h1 className="pro-title">APEX&nbsp;PRO</h1>
            <p className="pro-sub">The cloud studio — photorealistic, whole-body, any angle.</p>
          </div>
          <span className="pro-vip">VIP</span>
        </div>
        <div className="pro-card narrow">
          <p className="pro-muted">
            Apex Pro is a separate premium space powered by the <strong>Lucy 2.1</strong> real-time
            model — the same class of engine behind the top persona apps. It transforms your
            <strong> entire camera</strong> (face, body, scene, side profile) into a lifelike
            persona, live. Its own face, its own look, its own universe.
          </p>
          <p className="pro-muted">Metered by credits, billed by Apex Cam. No keys, no setup.</p>
          <button type="button" className="pro-goldbtn" onClick={() => setEntered(true)}>
            ✦ Enter Apex Pro
          </button>
        </div>
      </div>
    );
  }

  // --- The full VIP dashboard ---
  return (
    <div className="pro">
      <div className="pro-hero">
        <span className="pro-crown">✦</span>
        <div>
          <h1 className="pro-title">APEX&nbsp;PRO</h1>
          <p className="pro-sub">Cloud studio · Lucy 2.1 · photorealistic full-cam persona</p>
        </div>
        <span className="pro-vip">VIP</span>
      </div>

      <div className="pro-status">
        <span className={pro.configured ? "pro-pill" : "pro-pill"}>
          {pro.configured ? "Cloud ready" : "Cloud not enabled on this build"}
        </span>
        <span className={pro.enabled ? "pro-pill live" : "pro-pill"}>
          {pro.enabled ? "● LIVE" : "Off"}
        </span>
        <span className="pro-pill">{pro.model}</span>
        {pro.enabled ? (
          <button type="button" className="btn danger" onClick={() => save(false)}>■ Stop</button>
        ) : (
          <button type="button" className="pro-goldbtn" disabled={!pro.configured}
                  onClick={() => save(true)}>✦ GO LIVE</button>
        )}
      </div>
      {pro.error && <p className="error">{pro.error}</p>}

      <div className="pro-grid">
        {/* Left: the dual preview — the real you vs your AI persona */}
        <div className="pro-card">
          <h3>Live studio — the real you vs your AI persona</h3>
          <DualPreview />
          <p className="pro-muted pro-note">
            Start the AI engine (Home) once you've set your persona — frames route through the
            cloud model. Pick “OBS Virtual Camera” in your call app to go live everywhere.
          </p>
        </div>

        {/* Right: persona + look controls */}
        <div>
          <div className="pro-card">
            <h3>Your persona</h3>
            <div className="pro-persona">
              {pro.has_reference ? (
                <img className="pro-face" src={`http://127.0.0.1:8790/pro/reference?t=${Date.now()}`}
                     alt="" onClick={() => refFile.current?.click()} />
              ) : (
                <div className="pro-face empty" onClick={() => refFile.current?.click()}>
                  + Add<br />face
                </div>
              )}
              <div>
                <p className="pro-muted">The face/identity the cloud model becomes. Its own —
                  separate from the local library.</p>
                <button type="button" className="pro-chip" onClick={() => refFile.current?.click()}>
                  {pro.has_reference ? "Change face" : "Upload face"}
                </button>
              </div>
              <input ref={refFile} type="file" accept="image/*" hidden
                     onChange={(e) => uploadRef(e.target.files?.[0])} />
            </div>
          </div>

          <div className="pro-card">
            <h3>Look</h3>
            <input className="pro-input" type="text" value={prompt}
                   placeholder="Describe your look…"
                   onChange={(e) => setPrompt(e.target.value)} onBlur={() => save(pro.enabled)} />
            <div className="row preset-row" style={{ marginTop: 10 }}>
              {LOOKS.map((l) => (
                <button key={l} type="button" className="pro-chip"
                        onClick={() => { setPrompt(l); save(pro.enabled, l); }}>
                  {l.split(",")[0]}
                </button>
              ))}
            </div>
          </div>

          <div className="pro-card">
            <h3>Billing</h3>
            <p className="pro-muted">
              Pro runs on our cloud and is metered by credits (billed by Apex Cam). You never
              enter a provider key. Free local Apex Cam stays unlimited.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
