import { useEffect, useRef, useState } from "react";
import { api, type ProStatus } from "../api/client";
import { DualPreview } from "../components/DualPreview";

// Apex Cam Pro — its own self-contained "house": the premium cloud engine
// (Decart Lucy 2.1) with its own face/reference, look, and settings. The cloud
// key is server-side (our billing) — users never see it; they pay us credits.
const LOOKS = [
  "Photorealistic 30-year-old man, studio lighting",
  "Photorealistic young woman, soft cinematic light",
  "Anime character, vibrant colors",
  "Realistic older gentleman, warm tone",
];

export function ProTab() {
  const [pro, setPro] = useState<ProStatus | null>(null);
  const [unlocked, setUnlocked] = useState(false);
  const [prompt, setPrompt] = useState("");
  const refFile = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.enabled) setUnlocked(true);
    }).catch(() => setPro(null));
  }, []);

  const save = (enabled: boolean, p = prompt) =>
    api.setPro({ enabled, prompt: p }).then(setPro).catch(() => undefined);

  const uploadRef = (f: File | undefined) => {
    if (f) api.uploadProReference(f).then(setPro).catch(() => undefined);
  };

  if (!pro) return <section className="panel"><p className="muted">Backend offline.</p></section>;

  // Entry gate — "enter through Apex Cam by clicking Enable Pro".
  if (!unlocked) {
    return (
      <section className="panel">
        <h3>Apex Cam Pro ✦</h3>
        <p className="muted">
          Pro is the <strong>premium cloud studio</strong> — powered by the Lucy 2.1 real-time
          model. It transforms your <strong>whole camera</strong> (face, body, scene) into a
          photorealistic persona, live. It's a separate paid space with its own face, look and
          settings. Metered by credits (billed by us).
        </p>
        {!pro.configured && (
          <p className="muted">
            Status: <span className="pill">Cloud engine not yet enabled on this build</span> —
            the operator sets the cloud key server-side.
          </p>
        )}
        <button type="button" className="btn primary" onClick={() => setUnlocked(true)}>
          ✦ Enter Apex Pro
        </button>
      </section>
    );
  }

  return (
    <>
      <section className="preview">
        <DualPreview />
      </section>

      <section className="panel">
        <h3>Apex Pro ✦ — cloud studio</h3>
        <div className="row">
          <span className={pro.configured ? "pill ok" : "pill"}>
            {pro.configured ? "Cloud ready" : "Cloud not enabled (server key unset)"}
          </span>
          <span className={pro.enabled ? "pill ok" : "pill"}>
            {pro.enabled ? "LIVE (using credits)" : "Off"}
          </span>
          <span className="pill">{pro.model}</span>
        </div>
        {pro.error && <p className="error">{pro.error}</p>}
        <div className="row">
          {pro.enabled ? (
            <button type="button" className="btn danger" onClick={() => save(false)}>■ Stop Pro</button>
          ) : (
            <button
              type="button"
              className="btn primary"
              disabled={!pro.configured}
              onClick={() => save(true)}
            >
              ✦ Go Live (Pro)
            </button>
          )}
          <span className="muted">Then start the AI engine on Home.</span>
        </div>
      </section>

      <section className="panel">
        <h3>Your Pro face</h3>
        <p className="muted">
          Upload the face/persona the cloud model should become. This is Pro's own reference —
          separate from the local face library.
        </p>
        <div className="row">
          <button type="button" className="btn" onClick={() => refFile.current?.click()}>
            {pro.has_reference ? "Change reference face" : "+ Add reference face"}
          </button>
          {pro.has_reference && <span className="pill ok">Reference set</span>}
          <input ref={refFile} type="file" accept="image/*" hidden
                 onChange={(e) => uploadRef(e.target.files?.[0])} />
        </div>
      </section>

      <section className="panel">
        <h3>Look</h3>
        <label className="row">
          Describe the look
          <input type="text" value={prompt} placeholder="e.g. photorealistic 30-year-old man"
                 onChange={(e) => setPrompt(e.target.value)} onBlur={() => save(pro.enabled)} />
        </label>
        <div className="row preset-row">
          {LOOKS.map((l) => (
            <button key={l} type="button" className="btn"
                    onClick={() => { setPrompt(l); save(pro.enabled, l); }}>
              {l.split(",")[0]}
            </button>
          ))}
        </div>
      </section>

      <section className="panel">
        <h3>About Pro billing</h3>
        <p className="muted">
          Pro runs on our cloud (Lucy 2.1) and is metered. Credits are billed by Apex Cam —
          you never enter any provider key. Pricing/credits are set up separately.
        </p>
      </section>
    </>
  );
}
