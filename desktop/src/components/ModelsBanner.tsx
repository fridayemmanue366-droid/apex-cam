import { useEffect, useRef, useState } from "react";
import { api, type ModelsStatus } from "../api/client";

// Finishes the AI-model download from inside the app. The models (~3 GB) are
// fetched after install; on a weak connection that step used to fail with no
// way to recover. This shows what's missing and resumes the download (finished
// files are skipped, half-finished ones continue where they stopped).
//
//  - default (banner): appears only while ESSENTIAL models are missing, a
//    download is running, or the last attempt failed; renders nothing otherwise.
//  - panel: always shown (AI Models tab) with a per-model list.
export function ModelsBanner({ panel = false }: { panel?: boolean }) {
  const [s, setS] = useState<ModelsStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef<number>(0);

  const poll = () => api.modelsStatus().then(setS).catch(() => setS(null));

  useEffect(() => {
    poll();
    timer.current = window.setInterval(poll, 3000);
    return () => window.clearInterval(timer.current);
  }, []);

  if (!s) return null;

  const missing = s.models.filter((m) => m.status !== "installed");
  const coreMissing = s.core_missing.length > 0;
  const show = panel || coreMissing || s.running || s.failed.length > 0;
  if (!show) return null;

  const start = async () => {
    setStarting(true);
    await api.modelsDownload().catch(() => undefined);
    await poll();
    setStarting(false);
  };

  const pct = Math.round(s.progress * 100);
  const busy = s.running || starting;
  const lastLine = s.log[s.log.length - 1] ?? "";

  let title: string;
  if (s.running) title = `Downloading AI models… ${pct}%${s.current ? " — " + s.current : ""}`;
  else if (coreMissing) title = "Finish setup: some AI models still need to download";
  else if (s.failed.length > 0) title = "Some optional AI models didn't finish downloading";
  else title = `AI models: ${s.models.length - missing.length} of ${s.models.length} installed`;

  return (
    <section
      className={panel ? "panel wide" : undefined}
      style={panel ? undefined : {
        margin: "0 0 14px", padding: "12px 16px", borderRadius: 10,
        background: "rgba(244,208,111,0.10)", border: "1px solid rgba(244,208,111,0.35)",
      }}
    >
      {panel && <h3>AI models</h3>}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <strong style={{ flex: "1 1 260px" }}>{title}</strong>
        {missing.length > 0 && (
          <button type="button" className="btn primary" disabled={busy} onClick={start}>
            {busy ? "Downloading…" : "Download missing models"}
          </button>
        )}
      </div>
      {s.running && (
        <div style={{ height: 6, borderRadius: 3, background: "rgba(255,255,255,0.12)", margin: "10px 0 4px" }}>
          <div style={{ width: `${pct}%`, height: "100%", borderRadius: 3, background: "#f4d06f", transition: "width .4s" }} />
        </div>
      )}
      {!s.running && missing.length > 0 && (
        <p className="pro-muted" style={{ margin: "8px 0 0", fontSize: 13 }}>
          Needs internet, about {Math.round(missing.reduce((a, m) => a + m.mb, 0) / 100) / 10} GB.
          If your connection drops, just press the button again — it continues where it stopped.
        </p>
      )}
      {lastLine && (s.running || s.failed.length > 0) && (
        <p className="pro-muted" style={{ margin: "6px 0 0", fontSize: 12 }}>{lastLine}</p>
      )}
      {panel && (
        <div className="kv" style={{ marginTop: 10 }}>
          {s.models.map((m) => (
            <span key={m.name} style={{ display: "contents" }}>
              <span>{m.name}{m.tier === "extra" ? " (optional)" : ""}</span>
              <span>
                <span className={m.status === "installed" ? "pill ok" : "pill"}>
                  {m.status === "installed" ? "Installed" : m.status === "partial" ? "Partly downloaded" : "Not downloaded"}
                </span>
              </span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}
