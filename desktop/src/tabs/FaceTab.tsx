import { useEffect, useRef, useState } from "react";
import {
  api,
  type FaceProfile,
  type FaceState,
  type SwapMode,
  type TrackingSettings,
} from "../api/client";
import { DualPreview } from "../components/DualPreview";
import { usePipeline } from "../context/PipelineContext";

export function FaceTab() {
  const [state, setState] = useState<FaceState | null>(null);
  const [profiles, setProfiles] = useState<FaceProfile[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tracking, setTracking] = useState<TrackingSettings | null>(null);
  const [strength, setStrength] = useState(0.85);
  const [swapMode, setSwapMode] = useState<SwapMode | null>(null);
  const [enhancers, setEnhancers] = useState<string[]>([]);
  const [enhancer, setEnhancerState] = useState("none");
  const fileRef = useRef<HTMLInputElement>(null);
  const { active, faces } = usePipeline();

  useEffect(() => {
    api.getFace().then(setState).catch((e) => setErr(String(e)));
    api.listProfiles().then(setProfiles).catch(() => setProfiles([]));
    api.getTracking().then(setTracking).catch(() => setTracking(null));
    api.getSwapStrength().then((s) => setStrength(s.strength)).catch(() => undefined);
    api.getSwapMode().then(setSwapMode).catch(() => setSwapMode(null));
    api.getEnhancers().then((e) => setEnhancers(e.available)).catch(() => setEnhancers([]));
  }, []);

  const changeEnhancer = (kind: string) => {
    setEnhancerState(kind);
    api.setEnhancer(kind).then((r) => setEnhancerState(r.active)).catch(() => undefined);
  };

  const changeMode = (mode: "fast" | "neural") => {
    setSwapMode((m) => (m ? { ...m, mode } : m));
    api.setSwapMode(mode).then(setSwapMode).catch(() => undefined);
  };

  const changeStrength = (v: number) => {
    setStrength(v);
    api.setSwapStrength(v).catch(() => undefined);
  };

  const updateTracking = (patch: Partial<TrackingSettings>) => {
    if (!tracking) return;
    const next = { ...tracking, ...patch };
    setTracking(next);
    api.setTracking(next).catch(() => undefined);
  };

  const update = (patch: Partial<FaceState>) => {
    if (!state) return;
    const next = { ...state, ...patch };
    setState(next);
    api.setFace(next).catch((e) => setErr(String(e)));
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        const p = await api.uploadProfile(f);
        setProfiles((prev) => [...prev, p]);
      }
      setErr(null);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const remove = async (id: string) => {
    await api.deleteProfile(id).catch(() => undefined);
    setProfiles((prev) => prev.filter((p) => p.id !== id));
    if (state?.profile_id === id) update({ profile_id: null });
  };

  return (
    <>
      <section className="preview">
        <DualPreview />
      </section>

      <section className="panel">
        <h3>Face tracking</h3>
        {tracking && !tracking.available && (
          <p className="error">
            Tracking model not found. It ships with the app — reinstall if this persists.
          </p>
        )}
        <div className="row">
          <span className="pill ok">
            {active ? `${faces} face${faces === 1 ? "" : "s"} detected` : "Start the AI engine to track"}
          </span>
        </div>
        {tracking && (
          <>
            <label className="row">
              <input
                type="checkbox"
                checked={tracking.enabled}
                onChange={(e) => updateTracking({ enabled: e.target.checked })}
              />
              Enable face tracking
            </label>
            <label className="row">
              <input
                type="checkbox"
                checked={tracking.show_overlay}
                disabled={!tracking.enabled}
                onChange={(e) => updateTracking({ show_overlay: e.target.checked })}
              />
              Show tracking overlay (boxes + landmarks)
            </label>
            <label className="row">
              <input
                type="checkbox"
                checked={tracking.lip_sync}
                onChange={(e) => updateTracking({ lip_sync: e.target.checked })}
              />
              Lip-sync — mouth moves when you talk
            </label>
          </>
        )}
        <p className="muted">
          Real-time detection with 5 face landmarks (eyes, nose, mouth), running on CPU. On
          machines with a GPU this upgrades to dense face mesh and full-body pose. Tracking is
          what the face swap (Phase 7) locks onto.
        </p>
      </section>

      <section className="panel">
        <h3>Face library</h3>
        <p className="muted">
          The demo faces are free for everyone to try. Add your own photos too — clear,
          well-lit, front-facing photos work best. Only use faces you have permission to use —
          misuse can be reported and blocked.
        </p>
        <div className="face-grid">
          {profiles.map((p) => (
            <div
              key={p.id}
              className={state?.profile_id === p.id ? "face-card selected" : "face-card"}
              onClick={() => update({ profile_id: p.id })}
              title={p.name}
            >
              <img src={api.profileImageUrl(p.id)} alt={p.name} />
              <div className="face-name">{p.builtin ? `${p.name} · free` : p.name}</div>
              {!p.builtin && (
                <button
                  type="button"
                  className="face-delete"
                  onClick={(e) => {
                    e.stopPropagation();
                    remove(p.id);
                  }}
                  title="Remove"
                >
                  ×
                </button>
              )}
            </div>
          ))}
          <label className="face-card add">
            <input
              ref={fileRef}
              type="file"
              accept=".jpg,.jpeg,.png,.webp"
              multiple
              hidden
              onChange={(e) => upload(e.target.files)}
            />
            <span>{busy ? "Uploading…" : "+ Add face"}</span>
          </label>
        </div>
        {state?.profile_id && (
          <p className="muted">
            Selected face: <strong>{profiles.find((p) => p.id === state.profile_id)?.name}</strong>{" "}
            — used as the swap target identity.
          </p>
        )}
      </section>

      <section className="panel">
        <h3>Face swap</h3>
        {swapMode && (
          <>
            <div className="row">
              <button
                type="button"
                className={swapMode.mode === "fast" ? "btn primary" : "btn"}
                onClick={() => changeMode("fast")}
              >
                Fast (smooth)
              </button>
              <button
                type="button"
                className={swapMode.mode === "neural" ? "btn primary" : "btn"}
                disabled={!swapMode.neural_available}
                onClick={() => changeMode("neural")}
              >
                Realistic (neural)
              </button>
              <span className={swapMode.backend === "neural" ? "pill ok" : "pill"}>
                {swapMode.backend === "neural" ? "Realistic engine active" : "Fast engine active"}
              </span>
            </div>
            {swapMode.mode === "neural" && (
              <p className="muted">
                Realistic neural swap (inswapper) — keeps your real expressions, mouth and
                blinks, matched to lighting. Use a <strong>real photo</strong> as the face (the
                cartoon demo faces won't be detected). Without a GPU it's very slow (seconds per
                frame); on a GPU machine it's smooth real-time.
              </p>
            )}
            {!swapMode.neural_available && (
              <p className="muted">
                Realistic mode isn't available on this machine (models or AI runtime missing).
              </p>
            )}
            {swapMode.mode === "neural" && enhancers.length > 0 && (
              <>
                <label className="row">
                  Face enhancement
                  <select value={enhancer} onChange={(e) => changeEnhancer(e.target.value)}>
                    <option value="none">Off (fastest)</option>
                    {enhancers.includes("gfpgan") && <option value="gfpgan">GFPGAN — sharp &amp; clean</option>}
                    {enhancers.includes("codeformer") && <option value="codeformer">CodeFormer — natural</option>}
                  </select>
                </label>
                <p className="muted">
                  Restores and sharpens the swapped face for a professional look. Runs on
                  onnxruntime (works on CPU and GPU). Adds processing time — instant on a GPU,
                  a few extra seconds per frame on CPU.
                </p>
              </>
            )}
          </>
        )}
        {err && <p className="error">{err}</p>}
        {state ? (
          <>
            <label className="row">
              <input
                type="checkbox"
                checked={state.enabled}
                onChange={(e) => update({ enabled: e.target.checked })}
              />
              Enable face engine
            </label>
            <label className="row">
              <input
                type="checkbox"
                checked={state.swap_enabled}
                disabled={!state.enabled}
                onChange={(e) => update({ swap_enabled: e.target.checked })}
              />
              Face swap
            </label>
            <label className="row">
              Swap strength ({Math.round(strength * 100)}%)
              <input
                type="range"
                min={0.2}
                max={1}
                step={0.05}
                value={strength}
                disabled={!state.swap_enabled}
                onChange={(e) => changeStrength(Number(e.target.value))}
              />
            </label>
            {state.swap_enabled && !state.profile_id && (
              <p className="error">Pick a face from the library above to swap to.</p>
            )}
            {state.swap_enabled && state.profile_id && !active && (
              <p className="muted">Start the AI engine to see the swap live.</p>
            )}
            <p className="muted">
              Real-time landmark-based swap running on CPU. It aligns your chosen face onto your
              tracked face and colour-matches it. On GPU machines this slot upgrades to a neural
              swapper (inswapper/SimSwap) for higher fidelity — same controls.
            </p>
          </>
        ) : (
          <p className="muted">Backend offline.</p>
        )}
      </section>
    </>
  );
}
