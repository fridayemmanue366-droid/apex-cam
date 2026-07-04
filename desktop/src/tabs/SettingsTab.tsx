import { useEffect, useState } from "react";
import { usePipeline } from "../context/PipelineContext";

// Theme preference is applied by toggling a class on <body>; dark is default.
export function SettingsTab() {
  const [light, setLight] = useState(localStorage.getItem("emycam.theme") === "light");
  const consentDate = localStorage.getItem("emycam.consent.date");
  const { resetAll, resetting } = usePipeline();
  const [resetDone, setResetDone] = useState(false);

  const doReset = async () => {
    await resetAll();
    setResetDone(true);
    setTimeout(() => setResetDone(false), 3000);
  };

  useEffect(() => {
    document.body.classList.toggle("light", light);
    localStorage.setItem("emycam.theme", light ? "light" : "dark");
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
          <span>Terms accepted</span>
          <span>{consentDate ? new Date(consentDate).toLocaleString() : "Not yet"}</span>
          <span>Output labeling</span>
          <span>Always on (cannot be disabled)</span>
          <span>Local audit log</span>
          <span>On — records active profile/model per session</span>
        </div>
        <p className="muted">
          If someone uses EMY CAM to impersonate you without permission, report it — confirmed
          misuse leads to blocking and restriction. See docs/LEGAL.md.
        </p>
      </section>
    </>
  );
}
