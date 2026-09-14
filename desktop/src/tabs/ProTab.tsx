import { useEffect, useRef, useState } from "react";
import { api, getAiCameraIndex, setAiCameraIndex, type CameraInfo, type ProStatus } from "../api/client";
import { cloud, getToken, signedIn, type Account, type Pkg } from "../api/cloud";
import { DualPreview } from "../components/DualPreview";
import { ProAuth } from "../components/ProAuth";
import { usePipeline } from "../context/PipelineContext";

// Apex Pro — its OWN universe, separate from local Apex Cam: its own sign-in,
// its own pay-per-call credits, its own cloud engine (currently Lucy Realtime,
// via fal). Each model gets its own tab, each tab its own Playground / About /
// Privacy pages — only models that are actually built and verified are listed;
// nothing here is a stub for something half-working.

type SubPage = "playground" | "about" | "privacy";

interface ModelDef {
  id: string;
  name: string;
  icon: string;
  tag: string;
  status: "available";
}

// Add a new model here ONLY once it's actually wired, verified, and (per the
// design decision) has its own independent reference photo + prompt — not a
// slot shared with another model. Today there is exactly one.
const MODELS: ModelDef[] = [
  { id: "lucy-realtime", name: "Lucy Realtime", icon: "🎥",
    tag: "Live face, body & scene", status: "available" },
];

const LOOKS = [
  "Photorealistic 30-year-old man, studio lighting",
  "Photorealistic young woman, soft cinematic light",
  "Anime character, vibrant colors",
  "Realistic older gentleman, warm tone",
  "Fashion model, editorial lighting",
];

// Real, working presets — a live pitch shift on the same local engine the
// Voice tab uses (no cloud call, no missing model, works on any machine).
// Labeled by what the tech actually does, not a specific cloned identity.
const VOICES = [
  { name: "Slightly Deeper", semitones: -2 },
  { name: "Deeper", semitones: -5 },
  { name: "Slightly Higher", semitones: 2 },
  { name: "Higher", semitones: 5 },
];

const SUB_PAGES: { id: SubPage; label: string }[] = [
  { id: "playground", label: "Playground" },
  { id: "about", label: "About" },
  { id: "privacy", label: "Privacy" },
];

export function ProTab({ onExit }: { onExit: () => void }) {
  const pipeline = usePipeline();
  const [pro, setPro] = useState<ProStatus | null>(null);
  const [entered, setEntered] = useState(false);
  const [modelId, setModelId] = useState<string>(MODELS[0].id);
  const [sub, setSub] = useState<SubPage>("playground");
  const [prompt, setPrompt] = useState("");
  const [voice, setVoice] = useState<number>(0);
  const [pricing, setPricing] = useState<{ usd_per_minute: number; charge_currency: string; charge_per_minute: number } | null>(null);
  const refFile = useRef<HTMLInputElement>(null);

  // Cloud account — credits live on our server, tied to this login (so they
  // follow the user to any PC and can't be edited locally).
  const [account, setAccount] = useState<Account | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    if (signedIn()) {
      cloud.me().then(setAccount)
        .catch(() => { cloud.logout(); setAccount(null); })
        .finally(() => setAuthChecked(true));
    } else {
      setAuthChecked(true);
    }
  }, []);

  // Packages come LIVE from the cloud server — adding, removing or repricing a
  // package updates every customer instantly, with no app update.
  const [pkgs, setPkgs] = useState<Pkg[]>([]);

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.enabled) setEntered(true);
    }).catch(() => setPro(null));
    api.getProPricing().then(setPricing).catch(() => undefined);
    cloud.packages().then(setPkgs).catch(() => undefined);
    api.getVoiceParams().then((p) => setVoice(p.pitch)).catch(() => undefined);
  }, []);

  const usdLabel = (min: number) =>
    pricing ? `$${(min * pricing.usd_per_minute).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "…";
  const chargeLabel = (min: number) => {
    if (!pricing || pricing.charge_currency === "USD") return "";
    const sym = pricing.charge_currency === "NGN" ? "₦" : "";
    return `${sym}${Math.round(min * pricing.charge_per_minute).toLocaleString()}`;
  };

  // Poll the balance + engine status while inside the universe.
  useEffect(() => {
    if (!entered) return;
    const t = setInterval(() => {
      cloud.me().then(setAccount).catch(() => undefined);
      api.getPro().then(setPro).catch(() => undefined);
    }, 1500);
    return () => clearInterval(t);
  }, [entered]);

  // Live per-second balance display, ticking down while actually live.
  const [secsLeft, setSecsLeft] = useState(0);
  useEffect(() => { setSecsLeft(Math.round(account?.credit_seconds ?? 0)); }, [account?.credit_seconds]);
  useEffect(() => {
    if (!pro?.enabled || !pro?.live) return;
    const id = setInterval(() => setSecsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [pro?.enabled, pro?.live]);
  const perSec = pricing ? pricing.usd_per_minute / 60 : 0.05;
  const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  // GO LIVE / Stop — server mints credentials for whichever provider is active
  // (fal today; Decart stays one env flip away) and this PC uses them directly.
  const [liveSession, setLiveSession] = useState<string | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  const [liveProvider, setLiveProvider] = useState<"fal" | "decart" | null>(null);
  const [aiCameras, setAiCameras] = useState<CameraInfo[]>([]);
  const [aiAuto, setAiAuto] = useState(-1);
  const [aiCam, setAiCam] = useState(getAiCameraIndex());
  useEffect(() => {
    api.listCameras().then((r) => {
      setAiCameras(r.cameras);
      setAiAuto(r.auto);
      // A previously-saved pick that turns out to be a virtual camera (YouCam,
      // OBS, Apex Cam's own output) is worse than useless here — it's a
      // feedback loop with no real signal. Snap back to Auto rather than
      // leave the customer stuck on a dead feed with no idea why.
      setAiCam((cur) => {
        const picked = r.cameras.find((c) => c.index === cur);
        if (cur !== -1 && (!picked || picked.virtual)) {
          setAiCameraIndex(-1);
          return -1;
        }
        return cur;
      });
    }).catch(() => undefined);
  }, []);
  const autoName = aiCameras.find((c) => c.index === aiAuto)?.name;
  const changeAiCamera = (index: number) => {
    setAiCam(index);
    setAiCameraIndex(index);   // takes effect on the next GO LIVE
  };

  const save = async (enabled: boolean, p = prompt) => {
    setLiveErr(null);
    try {
      if (enabled && liveSession) {
        // Already live — this is just a prompt/look update.
        if (liveProvider === "fal") {
          // fal's session lives entirely on this PC — update the local engine
          // directly; it sends the new look itself on its own open connection.
          await api.setPro({ enabled: true, prompt: p }).catch(() => undefined);
        } else {
          await cloud.livePrompt(liveSession, p).catch(() => undefined);
        }
        return;
      }
      if (enabled) {
        if ((account?.credit_seconds ?? 0) <= 0) {
          setLiveErr("No credit — buy minutes first, then GO LIVE.");
          return;
        }
        if (!pipeline.active) await pipeline.start();   // release local cam
        let ref: File | null = null;
        try {
          const b = await fetch(api.proReferenceUrl).then((r) => (r.ok ? r.blob() : null));
          if (b) ref = new File([b], "persona.jpg", { type: "image/jpeg" });
        } catch { /* no persona yet — runs prompt-only */ }
        const r = await cloud.liveStart(p, ref);
        setLiveSession(r.session_id);
        setLiveProvider(r.provider);
        await api
          .proLiveCloud({
            provider: r.provider,
            livekit_url: r.livekit_url,
            token: r.token,
            jwt: r.jwt,
            model: r.model,
            session_id: r.session_id,
            cloud_url: cloud.url,
            auth: getToken() ?? undefined,
          })
          .then(setPro);
      } else {
        if (liveSession) cloud.liveStop(liveSession).catch(() => undefined);
        setLiveSession(null);
        setLiveProvider(null);
        await api.proLiveCloudStop().then(setPro).catch(() => undefined);
        if (pipeline.active) await pipeline.stop();
      }
    } catch (e) {
      setLiveErr(e instanceof Error ? e.message : "Could not go live — try again");
      if (liveSession) cloud.liveStop(liveSession).catch(() => undefined);
      setLiveSession(null);
      setLiveProvider(null);
      await api.proLiveCloudStop().catch(() => undefined);
    } finally {
      api.getPro().then(setPro).catch(() => undefined);
      cloud.me().then(setAccount).catch(() => undefined);
    }
  };
  const uploadRef = (f: File | undefined) => {
    if (f) api.uploadProReference(f).then(setPro).catch(() => undefined);
  };
  // Voice is free and local — it must NOT require GO LIVE (which spends paid
  // Lucy video credits). Its own on/off starts the same audio pipeline the
  // Audio tab uses, independent of the video session and its billing.
  const [voiceOn, setVoiceOn] = useState(false);
  useEffect(() => {
    api.audioStatus().then((s) => setVoiceOn(s.running)).catch(() => undefined);
  }, []);
  const toggleVoice = () => {
    (voiceOn ? api.audioStop() : api.audioStart(null, null))
      .then((s) => setVoiceOn(s.running))
      .catch(() => undefined);
  };
  // Real local pitch shift (same engine + endpoint the Voice tab uses under
  // the hood) — not the old fake cloud picker, which never worked.
  const pickVoice = (semitones: number) => {
    setVoice(semitones);
    api.getVoiceParams()
      .then((p) => api.setVoiceParams({ ...p, enabled: true, pitch: semitones }))
      .catch(() => undefined);
  };

  const [buyErr, setBuyErr] = useState<string | null>(null);
  const buy = (min: number) => {
    setBuyErr(null);
    return cloud.startPayment(min)
      .then((r) => { window.open(r.link, "_blank"); })
      .catch((e) => setBuyErr(e instanceof Error ? e.message : "Could not start payment"));
  };

  // Apex Pro requires a cloud account (that's where the credits live).
  if (!authChecked) return <section className="panel"><p className="muted">Loading…</p></section>;
  if (!account) {
    return <ProAuth onSignedIn={() => { cloud.me().then(setAccount).catch(() => undefined); }} />;
  }
  if (!pro) return <section className="panel"><p className="muted">Backend offline.</p></section>;

  // --- Entry gate: the "door" into the Pro universe ---
  if (!entered) {
    return (
      <div className="pro">
        <div className="pro-hero">
          <span className="pro-crown">✦</span>
          <div>
            <h1 className="pro-title">APEX&nbsp;PRO</h1>
            <p className="pro-sub">The cloud studio — photorealistic, whole-body, any angle.</p>
          </div>
          <button type="button" className="pro-linkbtn pro-exit" onClick={onExit}>← Apex Cam</button>
        </div>
        <div className="pro-card narrow">
          <p className="pro-muted">
            Apex Pro is a <strong>separate premium space</strong>. <strong>Lucy Realtime</strong>{" "}
            transforms your <strong>entire camera</strong> — face, body, scene — into a lifelike
            persona, live, matching your real movements and expressions. Its own face, its own
            voice, its own universe.
          </p>
          <p className="pro-muted">Metered by prepaid minutes, billed by Apex Cam. No keys, no setup.</p>
          <button type="button" className="pro-goldbtn" onClick={() => setEntered(true)}>
            ✦ Enter Apex Pro
          </button>
        </div>
      </div>
    );
  }

  const liveBadge = pro.enabled
    ? <span className="pro-pill live">● LIVE</span>
    : <span className="pro-pill">Off</span>;
  const model = MODELS.find((m) => m.id === modelId) ?? MODELS[0];

  return (
    <div className="pro pro-universe">
      <div className="pro-hero">
        <span className="pro-crown">✦</span>
        <div>
          <h1 className="pro-title">APEX&nbsp;PRO</h1>
          <p className="pro-sub">Cloud studio · pay-per-minute · photorealistic full-cam persona</p>
        </div>
        <div className="pro-hero-right">
          <span className="pro-pill" title={`$${perSec.toFixed(2)}/sec`}>
            ◈ {clock(secsLeft)}{pro.enabled && pro.live ? " ⏱" : ""}
          </span>
          {liveBadge}
          <span className="pro-account">
            <span className="pro-muted">{account.email}</span>
            <button type="button" className="pro-linkbtn"
                    onClick={() => { cloud.logout(); setAccount(null); }}>Sign out</button>
          </span>
          <button type="button" className="pro-linkbtn pro-exit" onClick={onExit}>← Apex Cam</button>
        </div>
      </div>

      <div className="pro-shell">
        {/* Model rail — one entry per WORKING model. Credits is account-level,
            not per-model, so it sits pinned below the model list. */}
        <nav className="pro-nav-rail">
          <div className="pro-nav-group-label">Models</div>
          {MODELS.map((m) => (
            <button key={m.id} type="button"
                    className={`pro-nav-item${modelId === m.id ? " active" : ""}`}
                    onClick={() => { setModelId(m.id); setSub("playground"); }}>
              <span className="pro-nav-ic">{m.icon}</span>{m.name}
            </button>
          ))}
          <div className="pro-nav-more">More models arrive here as they're built.</div>
          <div className="pro-nav-divider" />
          <button type="button" className={`pro-nav-item${modelId === "credits" ? " active" : ""}`}
                  onClick={() => setModelId("credits")}>
            <span className="pro-nav-ic">◈</span>Credits
          </button>
        </nav>

        <div className="pro-page">
          {pro.error && <p className="error">{pro.error}</p>}
          {liveErr && <p className="error">{liveErr}</p>}

          {modelId === "credits" ? (
            <>
              <div className="pro-card">
                <h3>Minutes — buy a package</h3>
                <p className="pro-muted">
                  Apex Pro runs on prepaid minutes shared across every model. Minutes burn per
                  second only while you're live ({`$${perSec.toFixed(2)}/sec`}), and leftovers
                  stay for next time.
                </p>
                {buyErr && <p className="error">{buyErr}</p>}
              </div>
              {pkgs.length === 0 && <p className="pro-muted">Loading packages…</p>}
              <div className="pro-packages">
                {pkgs.map((p) => (
                  <div key={p.minutes} className="pro-pack">
                    <div className="pro-pack-min">{p.minutes}<span> min</span></div>
                    <div className="pro-pack-price">{p.usd ? `$${p.usd.toFixed(2)}` : usdLabel(p.minutes)}</div>
                    {(p.charge ? <div className="pro-pack-alt">≈ ₦{p.charge.toLocaleString()}</div>
                               : chargeLabel(p.minutes) && <div className="pro-pack-alt">≈ {chargeLabel(p.minutes)}</div>)}
                    <button type="button" className="pro-chip" onClick={() => buy(p.minutes)}>Buy</button>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              {/* Model header: name, status, price, and its own sub-tabs. */}
              <div className="pro-model-head">
                <div className="pro-model-title">
                  <span className="pro-nav-ic">{model.icon}</span>
                  <div>
                    <h2>{model.name}</h2>
                    <p className="pro-muted">{model.tag}</p>
                  </div>
                </div>
                <span className="pro-pill ok">Available now</span>
                <span className="pro-pill" title="Charged per second while actually streaming">
                  ${perSec.toFixed(2)}/sec · ${(perSec * 60).toFixed(2)}/min
                </span>
              </div>
              <div className="pro-subtabs">
                {SUB_PAGES.map((s) => (
                  <button key={s.id} type="button"
                          className={`pro-subtab${sub === s.id ? " active" : ""}`}
                          onClick={() => setSub(s.id)}>{s.label}</button>
                ))}
              </div>

              {sub === "playground" && (
                <LucyRealtimePlayground
                  pro={pro} liveBadge={liveBadge} account={account}
                  prompt={prompt} setPrompt={setPrompt} save={save}
                  refFile={refFile} uploadRef={uploadRef}
                  voice={voice} pickVoice={pickVoice}
                  voiceOn={voiceOn} toggleVoice={toggleVoice}
                  aiCameras={aiCameras} aiCam={aiCam} autoName={autoName}
                  changeAiCamera={changeAiCamera}
                />
              )}
              {sub === "about" && <LucyRealtimeAbout perSec={perSec} />}
              {sub === "privacy" && <LucyRealtimePrivacy />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// --- Playground: reference photo, prompt, and live controls together — the
// full control for THIS model, not split across separate pages and not shared
// with any other model. -------------------------------------------------
function LucyRealtimePlayground(props: {
  pro: ProStatus;
  liveBadge: JSX.Element;
  account: Account;
  prompt: string;
  setPrompt: (p: string) => void;
  save: (enabled: boolean, p?: string) => void;
  refFile: React.RefObject<HTMLInputElement>;
  uploadRef: (f: File | undefined) => void;
  voice: number;
  pickVoice: (semitones: number) => void;
  voiceOn: boolean;
  toggleVoice: () => void;
  aiCameras: CameraInfo[];
  aiCam: number;
  autoName: string | undefined;
  changeAiCamera: (i: number) => void;
}) {
  const { pro, liveBadge, account, prompt, setPrompt, save, refFile, uploadRef,
          voice, pickVoice, voiceOn, toggleVoice,
          aiCameras, aiCam, autoName, changeAiCamera } = props;
  return (
    <>
      <div className="pro-status">
        {liveBadge}
        {pro.enabled ? (
          <button type="button" className="btn danger" onClick={() => save(false)}>■ Stop</button>
        ) : (
          <button type="button" className="pro-goldbtn"
                  disabled={(account.credit_seconds ?? 0) <= 0}
                  onClick={() => save(true)}>✦ GO LIVE</button>
        )}
      </div>

      <div className="pro-card">
        <h3>Preview — the real you vs your AI persona</h3>
        <DualPreview />
      </div>

      <div className="pro-card">
        <h3>Persona</h3>
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
            <p className="pro-muted">
              The identity Lucy becomes — a clear, front-facing photo works best. Optional: leave
              it unset and drive the look with the prompt alone.
            </p>
            <button type="button" className="pro-chip" onClick={() => refFile.current?.click()}>
              {pro.has_reference ? "Change face" : "Upload face"}
            </button>
          </div>
          <input ref={refFile} type="file" accept="image/*" hidden
                 onChange={(e) => uploadRef(e.target.files?.[0])} />
        </div>
      </div>

      <div className="pro-card">
        <h3>Look / prompt</h3>
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
        <p className="pro-muted pro-note">
          Updates live while you're already GO LIVE — no need to stop and restart.
        </p>
      </div>

      <div className="pro-card">
        <h3>Camera</h3>
        <div className="pro-camrow" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <select value={aiCam} onChange={(e) => changeAiCamera(Number(e.target.value))}>
            <option value={-1}>Auto{autoName ? ` — ${autoName}` : " — real webcam"}</option>
            {aiCameras.map((c) => (
              <option key={c.index} value={c.index}>
                {c.name}{c.virtual ? " (virtual — not recommended)" : ""}
              </option>
            ))}
          </select>
          <span className="pro-muted" style={{ fontSize: 12 }}>
            The real webcam Lucy reads — skips YouCam &amp; virtual cams. Applies on the next GO LIVE.
          </span>
        </div>
      </div>

      <div className="pro-card">
        <h3>Voice</h3>
        <p className="pro-muted">
          Real-time pitch shift, applied live — runs locally on this machine (not the cloud), so
          it works on any hardware with no extra setup. <strong>Free</strong> — independent of
          GO LIVE and your Lucy minutes; turn it on any time, with or without video running.
        </p>
        <div className="pro-status" style={{ marginBottom: 4 }}>
          <span className={`pro-pill${voiceOn ? " live" : ""}`}>{voiceOn ? "● Voice on" : "Voice off"}</span>
          <button type="button" className="pro-chip" onClick={toggleVoice}>
            {voiceOn ? "Turn off" : "Turn on"}
          </button>
        </div>
        <div className="row preset-row" style={{ marginTop: 10 }}>
          <button type="button" className={`pro-chip${voice === 0 ? " on" : ""}`}
                  onClick={() => pickVoice(0)}>Natural (off)</button>
          {VOICES.map((v) => (
            <button key={v.name} type="button"
                    className={`pro-chip${voice === v.semitones ? " on" : ""}`}
                    onClick={() => pickVoice(v.semitones)}>{v.name}</button>
          ))}
        </div>
      </div>
    </>
  );
}

// --- About: what it is, how it works, price, requirements. -----------------
function LucyRealtimeAbout({ perSec }: { perSec: number }) {
  return (
    <div className="pro-card narrow">
      <h3>What Lucy Realtime does</h3>
      <p className="pro-muted">
        Transforms your <strong>entire camera</strong> — face, body, and scene — into a chosen
        persona, live. It's not a filter over a static photo: it tracks your real head turns,
        expressions, and movement, and renders the persona doing the same thing, frame by frame,
        as you stream.
      </p>
      <h3 style={{ marginTop: 18 }}>How it works</h3>
      <p className="pro-muted">
        Your camera streams to Decart's Lucy 2.5 model through fal's realtime relay. The result
        streams back in real time and feeds Apex Cam's virtual camera, so any app that lets you
        pick a camera — Zoom, Discord, Meet, Teams — shows the transformed you.
      </p>
      <p className="pro-muted">
        In our own testing, this path does not add a visible "AI Generated" watermark to the
        output — unlike Decart's direct API, which does. That's the current provider's behavior,
        not a permanent guarantee; the AI-generated disclosure Apex Cam itself applies (see
        Privacy) is what you should rely on for compliance either way.
      </p>
      <h3 style={{ marginTop: 18 }}>What you need</h3>
      <p className="pro-muted">
        One clear, front-facing reference photo (optional — you can drive the look with just a
        text prompt), a working webcam, and a reasonably stable internet connection. Prepaid
        minutes come from your Apex Pro wallet, shared across every model.
      </p>
      <h3 style={{ marginTop: 18 }}>Price</h3>
      <p className="pro-muted">
        <strong>${perSec.toFixed(2)}/sec (${(perSec * 60).toFixed(2)}/min)</strong>, charged only
        while frames are actually streaming back — connecting or a stalled reconnect doesn't
        burn your balance.
      </p>
    </div>
  );
}

// --- Privacy: honest, model-specific data/consent/risk policy. -------------
function LucyRealtimePrivacy() {
  return (
    <div className="pro-card narrow">
      <h3>Where your data goes</h3>
      <p className="pro-muted">
        Unlike Apex Cam's <strong>local</strong> face swap (processed entirely on your own PC),
        Lucy Realtime sends your live camera feed and reference photo to our cloud processing
        provider (fal, running Decart's Lucy model) for transformation. That's inherent to how a
        cloud model works — video has to leave your device to be processed there. Apex Cam does
        not itself record or store your live stream; for how the processing provider handles data
        on their end, their own policies apply — we don't control or see behind that boundary.
      </p>
      <h3 style={{ marginTop: 18 }}>Consent — read this before you use it</h3>
      <p className="pro-muted">
        <strong>Only use your own face, or a face you have explicit permission to use.</strong>{" "}
        Using someone else's likeness to deceive, defraud, harass, or misrepresent them is
        prohibited here and may be illegal in your jurisdiction. Apex Cam can restrict or block
        accounts over confirmed misuse.
      </p>
      <h3 style={{ marginTop: 18 }}>Labeling is mandatory, not optional</h3>
      <p className="pro-muted">
        Apex Cam labels processed video as AI-generated. You may not remove, obscure, or
        misrepresent that labeling — this is a core, non-optional part of the product, matching
        the same responsible-use policy that covers local swapping (see Settings).
      </p>
      <h3 style={{ marginTop: 18 }}>Risk</h3>
      <p className="pro-muted">
        A convincing live persona swap makes impersonation more believable, not less risky. You
        are responsible for how you use the output, including the terms of whatever platform
        (video call, stream, recording) you use it on.
      </p>
    </div>
  );
}
