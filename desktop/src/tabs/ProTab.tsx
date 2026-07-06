import { useEffect, useRef, useState } from "react";
import { api, type ProStatus } from "../api/client";

// Apex Cam Pro — the premium cloud tier (Decart Lucy 2.1). Entered from here;
// activates when a key is set. Payment/credits are wired later.
export function ProTab() {
  const [pro, setPro] = useState<ProStatus | null>(null);
  const [unlocked, setUnlocked] = useState(false);
  const [key, setKey] = useState("");
  const [prompt, setPrompt] = useState("");
  const refFile = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.configured) setUnlocked(true);
    }).catch(() => setPro(null));
  }, []);

  const save = (enabled: boolean) => {
    api.setPro({ enabled, api_key: key || null, prompt }).then((p) => {
      setPro(p);
      if (p.configured) setUnlocked(true);
    }).catch(() => undefined);
  };

  const uploadRef = (f: File | undefined) => {
    if (f) api.uploadProReference(f).then(setPro).catch(() => undefined);
  };

  if (!pro) return <section className="panel"><p className="muted">Backend offline.</p></section>;

  if (!unlocked) {
    return (
      <section className="panel">
        <h3>Apex Cam Pro ✦</h3>
        <p className="muted">
          Pro unlocks the <strong>cloud AI engine (Lucy 2.1)</strong> — the same tech behind the
          top real-time avatar apps. It transforms your <strong>whole camera</strong> (face, body,
          scene) into a photorealistic persona, live. This is a premium metered feature (uses
          cloud credits), separate from the free local Apex Cam.
        </p>
        <button type="button" className="btn primary" onClick={() => setUnlocked(true)}>
          ✦ Enable Pro
        </button>
      </section>
    );
  }

  return (
    <>
      <section className="panel">
        <h3>Apex Cam Pro ✦ — cloud engine</h3>
        <div className="row">
          <span className={pro.configured ? "pill ok" : "pill"}>
            {pro.configured ? "Configured" : "Not configured"}
          </span>
          <span className={pro.enabled ? "pill ok" : "pill"}>
            {pro.enabled ? "LIVE (using credits)" : "Off"}
          </span>
          <span className="pill">{pro.model}</span>
        </div>
        {pro.error && <p className="error">{pro.error}</p>}

        <label className="row">
          Cloud API key (fal.ai)
          <input
            type="password"
            value={key}
            placeholder="fal_..."
            onChange={(e) => setKey(e.target.value)}
          />
        </label>
        <label className="row">
          Look / prompt
          <input
            type="text"
            value={prompt}
            placeholder="e.g. a realistic 30-year-old man, studio lighting"
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>
        <div className="row">
          <button type="button" className="btn" onClick={() => refFile.current?.click()}>
            {pro.has_reference ? "Change reference photo" : "Add reference photo"}
          </button>
          <input
            ref={refFile}
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => uploadRef(e.target.files?.[0])}
          />
          <button type="button" className="btn" onClick={() => save(false)}>Save</button>
          {pro.enabled ? (
            <button type="button" className="btn danger" onClick={() => save(false)}>Stop Pro</button>
          ) : (
            <button type="button" className="btn primary" onClick={() => save(true)}>Go Live (Pro)</button>
          )}
        </div>
        <p className="muted">
          Then start the AI engine (Home) — frames route through the cloud model. Pro is metered
          (~$0.02/sec at retail). Once we have wholesale pricing + payment, this becomes credit-based.
        </p>
      </section>

      <section className="panel">
        <h3>How Pro differs from free Apex Cam</h3>
        <ul className="list">
          <li><strong>Free (local):</strong> face swap, avatar, beautify, background — runs on your PC.</li>
          <li><strong>Pro (cloud):</strong> full-scene photorealistic transform via Lucy 2.1 — needs internet + credits, best quality.</li>
        </ul>
      </section>
    </>
  );
}
