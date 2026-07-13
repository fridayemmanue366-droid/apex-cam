import { useEffect, useState } from "react";
import { usePipeline } from "../context/PipelineContext";
import { LegalModal } from "../components/LegalModal";
import { LEGAL_ORDER, LEGAL_TITLES, type DocId } from "../legal/LegalContent";

// Theme preference is applied by toggling a class on <body>; dark is default.
export function SettingsTab() {
  const [light, setLight] = useState(localStorage.getItem("apexcam.theme") === "light");
  const consentDate = localStorage.getItem("apexcam.consent.date");
  const { resetAll, resetting } = usePipeline();
  const [resetDone, setResetDone] = useState(false);
  const [openDoc, setOpenDoc] = useState<DocId | null>(null);

  const doReset = async () => {
    await resetAll();
    setResetDone(true);
    setTimeout(() => setResetDone(false), 3000);
  };

  useEffect(() => {
    document.body.classList.toggle("light", light);
    localStorage.setItem("apexcam.theme", light ? "light" : "dark");
  }, [light]);

  return (
    <>
      <section className="panel">
        <h3>Appearance</h3>
        <label className="row">
          <input type="checkbox" checked={!light} onChange={(e) => setLight(!e.target.checked)} />
          Dark mode
        </label>
        <label className="row">
          Language
          <select defaultValue="en">
            <option value="en">English</option>
          </select>
        </label>
        <p className="muted">More languages arrive with packaging (Phase 10).</p>
      </section>

      <section className="panel">
        <h3>Troubleshooting</h3>
        <p className="muted">
          If the camera stays on, an app is stuck, or you hit an error, use Reset. It stops the
          camera, the AI engine and the virtual camera, and clears errors — nothing is deleted.
          Reset is also always available in the bar at the bottom of the window.
        </p>
        <div className="row">
          <button type="button" className="btn danger" onClick={() => void doReset()} disabled={resetting}>
            {resetting ? "Resetting…" : "⟳ Reset / stop everything"}
          </button>
          {resetDone && <span className="pill ok">Done — everything stopped</span>}
        </div>
      </section>

      <section className="panel">
        <h3>Responsible use</h3>
        <div className="kv">
          <span>Terms last acknowledged</span>
          <span>{consentDate ? new Date(consentDate).toLocaleString() : "Not yet"}</span>
          <span>Local audit log</span>
          <span>On — records active profile/model per session</span>
        </div>
        <p className="muted">
          Apex Cam is for streamers, creators and entertainment — never for scams, fraud, or
          impersonating real people to deceive. If someone uses Apex Cam to impersonate you without
          permission, report it: confirmed misuse leads to blocking and restriction.
        </p>
      </section>

      <section className="panel">
        <h3>Legal &amp; policies</h3>
        <p className="muted">Read the terms and policies that apply to Apex Cam.</p>
        <div className="legal-links">
          {LEGAL_ORDER.map((id) => (
            <button type="button" key={id} className="btn" onClick={() => setOpenDoc(id)}>
              {LEGAL_TITLES[id]}
            </button>
          ))}
        </div>
      </section>

      {openDoc && <LegalModal doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </>
  );
}
