import { useEffect, useRef, useState } from "react";
import { api, getAiCameraIndex, setAiCameraIndex, type CameraInfo, type ProStatus } from "../api/client";
import { cloud, getToken, signedIn, type Account, type Pkg } from "../api/cloud";
import { DualPreview } from "../components/DualPreview";
import { ProAuth } from "../components/ProAuth";
import { usePipeline } from "../context/PipelineContext";
import {
  MIN_REFERENCE_S,
  MicRecorder,
  decodeToMonoPCM,
  encodeWav,
  rms,
} from "../lib/audioRecorder";

// Apex Pro — its OWN universe, separate from local Apex Cam: its own sign-in,
// its own pay-per-call credits, its own cloud engine (currently Lucy Realtime,
// via fal). Each model gets its own tab, each tab its own Playground / About /
// Privacy pages — only models that are actually built and verified are listed;
// nothing here is a stub for something half-working.

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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
  { id: "lucy-image", name: "Apex Image", icon: "🖼️",
    tag: "Edit or create any photo", status: "available" },
  { id: "lucy-restyle", name: "Lucy Restyle", icon: "🎨",
    tag: "Restyle a recorded video", status: "available" },
  { id: "voice-note", name: "Voice Note", icon: "🎙️",
    tag: "Clone a voice, type a message, get an audio file", status: "available" },
];

const LOOKS = [
  "Photorealistic 30-year-old man, studio lighting",
  "Photorealistic young woman, soft cinematic light",
  "Anime character, vibrant colors",
  "Realistic older gentleman, warm tone",
  "Fashion model, editorial lighting",
];

// Ready-made faces to test Lucy Realtime with, no upload needed. Every one
// is AI-generated — a person who does not exist, not a real photo of a real
// person — specifically so there's no likeness/publicity-rights question at
// all, unlike even a "free to use" stock photo of an actual person (that
// license usually covers copying the image, not using someone's real
// identity for face-swapping, which is a separate legal question the
// license doesn't answer).
const PERSONA_GALLERY = [
  { name: "woman_professional", label: "Woman (professional)" },
  { name: "man_young", label: "Man (young)" },
  { name: "woman_older", label: "Woman (older)" },
  { name: "man_older", label: "Man (older)" },
  { name: "woman_casual", label: "Woman (casual)" },
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

// Click-to-fill examples — proof the prompt box isn't a fixed menu, shown as
// real instructions rather than described in prose.
const IMAGE_PRESETS = [
  "Swap this face for the reference photo",
  "Make them hold a red umbrella",
  "Change the background to a beach at sunset",
  "Remove the person on the left",
  "Give her a short black bob",
  "Add sunglasses and a leather jacket",
  "Make it look like a rainy night in Tokyo",
  "Turn this into a professional studio headshot",
];

// Spec-sheet tags for the About page — a capability list, not a paragraph.
const IMAGE_CAPS = [
  "Face swap", "Object add / remove / replace", "Background change",
  "Hair & clothing restyle", "Lighting & weather", "Color grade",
  "Style transfer", "Upscale / cleanup",
];

const RESTYLE_PRESETS = [
  "Anime style, vibrant colors",
  "Oil painting, visible brush strokes",
  "Claymation / stop-motion look",
  "Cyberpunk neon aesthetic",
  "Watercolor sketch",
  "1980s VHS film grain",
];

const RESTYLE_CAPS = [
  "Full art-style transfer", "Anime / cartoon / painterly looks",
  "Color grade & film-stock looks", "Text-prompt OR reference-image style",
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
  const [imgPricing, setImgPricing] = useState<{ credits: number; currency: string; usd: number; charge: number } | null>(null);
  // Live per-second sell rate for every metered mode, straight from the cloud
  // server — NOT the local /pro/pricing fallback above, which is a hand-
  // maintained constant that drifts from the real, admin-tunable rate (it
  // was showing $0.05/sec while the real rate was $0.0652/sec at the time
  // this was fixed). video/restyle each have their own independent margin
  // now, not a fixed ratio of live's, so this can't be computed client-side
  // either — it has to be fetched.
  const [rates, setRates] = useState<{ live_usd_per_sec: number; video_usd_per_sec: number;
    restyle_usd_per_sec: number } | null>(null);
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

  // Keep the LOCAL backend's per-frame watermark decision in sync whenever
  // `account` is refreshed from anywhere in this tab (many call sites do
  // `cloud.me().then(setAccount)` — watching the value itself here, rather
  // than adding this call at every one of them, means none can be missed).
  //
  // REAL BUG FIXED (2026-09-22): this used to run `?? false` while `account`
  // was still null (e.g. every time this tab remounts, which the sidebar's
  // ternary render does on every navigation away-and-back) and actually
  // push that wrong "unlicensed" false to the local pipeline BEFORE the
  // real value arrived. If the user navigated away again quickly, the
  // corrective real sync (inside cloud.me().then(setAccount)) never landed
  // on a still-mounted component, leaving the local app stuck reporting
  // unlicensed even for a genuinely licensed account. Now this only ever
  // sends a value once we actually HAVE one.
  useEffect(() => {
    if (account) api.setLicensed(account.licensed).catch(() => undefined);
  }, [account]);

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
    cloud.imagePricing().then(setImgPricing).catch(() => undefined);
    cloud.studioPricing().then(setRates).catch(() => undefined);
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

  // Poll the balance + engine status while inside the universe. A 401 here
  // means the token just went stale (call() already cleared it) — drop
  // `account` too, so the UI falls back to the sign-in gate immediately
  // instead of showing a cached balance while every real request underneath
  // silently fails with "Not signed in".
  useEffect(() => {
    if (!entered) return;
    const t = setInterval(() => {
      cloud.me().then(setAccount).catch(() => { if (!signedIn()) setAccount(null); });
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
  const perSec = rates ? rates.live_usd_per_sec : (pricing ? pricing.usd_per_minute / 60 : 0.05);
  // Customer-facing unit for Lucy Image: credits, never a raw $/₦ amount.
  // The count comes live from the server (today always 1) so this never hardcodes it.
  const imageCredits = imgPricing
    ? `${imgPricing.credits} credit${imgPricing.credits === 1 ? "" : "s"}`
    : "…";
  // Video jobs (restyle) burn wallet-minutes like Lucy Realtime, but has its
  // OWN independent, admin-tunable margin now — not a fixed multiple of
  // live's rate — so this comes straight from the server, never computed
  // client-side.
  const restylePerSec = rates ? rates.restyle_usd_per_sec : perSec * (2 / 3);
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

  const [licenseErr, setLicenseErr] = useState<string | null>(null);
  const [buyingLicense, setBuyingLicense] = useState(false);
  const buyLicense = () => {
    setLicenseErr(null);
    setBuyingLicense(true);
    cloud.startLicense()
      .then((r) => { window.open(r.link, "_blank"); })
      .catch((e) => setLicenseErr(e instanceof Error ? e.message : "Could not start payment"))
      .finally(() => setBuyingLicense(false));
  };

  // Apex Pro requires a cloud account (that's where the credits live).
  if (!authChecked) return (
    <section className="panel">
      <p className="muted">Loading…</p>
      <button type="button" className="pro-linkbtn" onClick={onExit}>← Apex Cam</button>
    </section>
  );
  if (!account) {
    return <ProAuth onExit={onExit}
                    onSignedIn={() => { cloud.me().then(setAccount).catch(() => undefined); }} />;
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
          <span className="pro-pill" title="Same balance, shown as Image/Voice Note credits">
            {account.credits} credit{account.credits === 1 ? "" : "s"}
          </span>
          {account.licensed ? (
            <span className="pro-pill ok" title="Lucy Realtime and local face swap output has no watermark">
              ✓ No watermark
            </span>
          ) : (
            <button type="button" className="pro-pill" disabled={buyingLicense} onClick={buyLicense}
                    title="Removes the AI-GENERATED watermark from Lucy Realtime and local face swap, one time">
              {buyingLicense ? "Opening…" : "Remove watermark — $10 once"}
            </button>
          )}
          {liveBadge}
          <span className="pro-account">
            <span className="pro-muted">{account.email}</span>
            <button type="button" className="pro-linkbtn"
                    onClick={() => { cloud.logout(); setAccount(null); }}>Sign out</button>
          </span>
          <button type="button" className="pro-linkbtn pro-exit" onClick={onExit}>← Apex Cam</button>
        </div>
      </div>
      {licenseErr && <p className="error" style={{ margin: "0 24px" }}>{licenseErr}</p>}

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
                    {/* Only show a second-currency line when the charge is actually in a
                        DIFFERENT currency than the USD price above (e.g. NGN) — when
                        currency is USD, `charge` IS `usd`, so showing it again with a
                        currency symbol would be redundant/wrong (was hardcoded ₦
                        regardless of what currency was actually active). */}
                    {p.charge && p.currency !== "USD" ? (
                      <div className="pro-pack-alt">≈ {p.currency === "NGN" ? "₦" : ""}{p.charge.toLocaleString()}</div>
                    ) : (
                      chargeLabel(p.minutes) && <div className="pro-pack-alt">≈ {chargeLabel(p.minutes)}</div>
                    )}
                    {p.credits != null && (
                      <div className="pro-pack-alt">≈ {p.credits} credit{p.credits === 1 ? "" : "s"}</div>
                    )}
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
                {model.id === "lucy-realtime" && (
                  <span className="pro-pill" title="Charged per second while actually streaming">
                    ${perSec.toFixed(2)}/sec · ${(perSec * 60).toFixed(2)}/min
                  </span>
                )}
                {model.id === "lucy-image" && (
                  <span className="pro-pill" title="Charged once per successful generation">
                    {imageCredits} / image
                  </span>
                )}
                {model.id === "lucy-restyle" && (
                  <span className="pro-pill" title="Charged for the source video's length while processing">
                    ${restylePerSec.toFixed(3)}/sec of video
                  </span>
                )}
                {model.id === "voice-note" && (
                  <span className="pro-pill" title="Charged once per generated message, by length">
                    priced per message length
                  </span>
                )}
              </div>
              <div className="pro-subtabs">
                {SUB_PAGES.map((s) => (
                  <button key={s.id} type="button"
                          className={`pro-subtab${sub === s.id ? " active" : ""}`}
                          onClick={() => setSub(s.id)}>{s.label}</button>
                ))}
              </div>

              {model.id === "lucy-realtime" && (
                <>
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
              {model.id === "lucy-image" && (
                <>
                  {sub === "playground" && (
                    <LucyImagePlayground account={account} imageCredits={imageCredits}
                      refreshAccount={() => cloud.me().then(setAccount).catch(() => undefined)} />
                  )}
                  {sub === "about" && <LucyImageAbout imageCredits={imageCredits} />}
                  {sub === "privacy" && <LucyImagePrivacy />}
                </>
              )}
              {model.id === "lucy-restyle" && (
                <>
                  {sub === "playground" && (
                    <RestylePlayground account={account} perSecRate={restylePerSec}
                      refreshAccount={() => cloud.me().then(setAccount).catch(() => undefined)} />
                  )}
                  {sub === "about" && <RestyleAbout perSec={restylePerSec} />}
                  {sub === "privacy" && <RestylePrivacy />}
                </>
              )}
              {model.id === "voice-note" && (
                <>
                  {sub === "playground" && (
                    <VoiceNotePlayground account={account}
                      refreshAccount={() => cloud.me().then(setAccount).catch(() => undefined)} />
                  )}
                  {sub === "about" && <VoiceNoteAbout />}
                  {sub === "privacy" && <VoiceNotePrivacy />}
                </>
              )}
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
  const [personaErr, setPersonaErr] = useState<string | null>(null);
  const pickPersona = async (name: string) => {
    setPersonaErr(null);
    try {
      const blob = await fetch(`/persona-presets/${name}.jpg`).then((r) => r.blob());
      uploadRef(new File([blob], `${name}.jpg`, { type: "image/jpeg" }));
    } catch {
      setPersonaErr("Could not load that preset — try again");
    }
  };
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
        <p className="pro-muted" style={{ marginTop: 10 }}>Or try one of these — every face here is
          AI-generated, not a real person, so there's no likeness/rights question testing with them:</p>
        <div className="row preset-row" style={{ marginTop: 6 }}>
          {PERSONA_GALLERY.map((p) => (
            <button key={p.name} type="button" className="pro-chip" onClick={() => pickPersona(p.name)}>
              {p.label}
            </button>
          ))}
        </div>
        {personaErr && <p className="error">{personaErr}</p>}
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

// --- Lucy Image: a real editing tool, not a landing page. Upload, instruct,
// generate, download — left panel is input, right panel is output, same
// shape as Decart's own playground. -----------------------------------------
function LucyImagePlayground(props: {
  account: Account;
  imageCredits: string;
  refreshAccount: () => void;
}) {
  const { account, imageCredits, refreshAccount } = props;
  const mainInput = useRef<HTMLInputElement>(null);
  const refInput = useRef<HTMLInputElement>(null);
  const [mainFile, setMainFile] = useState<File | null>(null);
  const [refFile, setRefFile] = useState<File | null>(null);
  const [mainPreview, setMainPreview] = useState<string | null>(null);
  const [refPreview, setRefPreview] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [usedAsLive, setUsedAsLive] = useState(false);

  const pickMain = (f: File | undefined) => {
    if (!f) return;
    setMainFile(f);
    setMainPreview(URL.createObjectURL(f));
    setResultUrl(null);
  };
  const pickRef = (f: File | undefined) => {
    if (!f) return;
    setRefFile(f);
    setRefPreview(URL.createObjectURL(f));
  };
  const clearRef = () => { setRefFile(null); setRefPreview(null); };

  const generate = async () => {
    if (!mainFile || !prompt.trim() || busy) return;
    setErr(null);
    setBusy(true);
    setUsedAsLive(false);
    try {
      // A real photo can take Decart 30-90+ seconds — this is a background job,
      // not one long-held request, so a network blip while waiting doesn't lose
      // the result: the job keeps running server-side and polling just resumes.
      const { job_id } = await cloud.photoStart(mainFile, prompt.trim(), false, refFile);
      // Longer than the server's own stuck-job self-heal window (4 min), so a
      // genuinely orphaned job (a deploy landing mid-generation) reaches
      // "error" — and gets refunded server-side — before the client gives up,
      // instead of abandoning the poll with the charge stuck in limbo.
      const deadline = Date.now() + 5 * 60 * 1000;
      let status: "processing" | "done" | "error" = "processing";
      while (status === "processing") {
        if (Date.now() > deadline) throw new Error("Taking too long — try again");
        await sleep(2000);
        status = (await cloud.photoStatus(job_id)).status;
      }
      if (status === "error") throw new Error("Could not generate the image");
      setResultUrl(await cloud.photoContent(job_id));
      refreshAccount();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not generate the image");
    } finally {
      setBusy(false);
    }
  };

  const useAsLiveCharacter = async () => {
    if (!resultUrl) return;
    try {
      const blob = await fetch(resultUrl).then((r) => r.blob());
      await api.uploadProReference(new File([blob], "persona.jpg", { type: "image/jpeg" }));
      setUsedAsLive(true);
    } catch {
      setErr("Could not set this as your Live Character");
    }
  };

  const noCredit = (account.credit_seconds ?? 0) <= 0;

  return (
    <div className="pro-card">
      <div className="pro-editor">
        <div className="pro-editor-inputs">
          <div className="pro-uploads">
            <div>
              <div className="pro-uplabel">Input</div>
              <div className="pro-photo-slot" onClick={() => mainInput.current?.click()}>
                {mainPreview ? <img src={mainPreview} alt="" /> : <span className="pro-muted">+ Upload photo</span>}
              </div>
            </div>
            <div>
              <div className="pro-uplabel">Reference · optional</div>
              {refPreview ? (
                <div className="pro-slot-wrap">
                  <div className="pro-photo-slot small" onClick={() => refInput.current?.click()}>
                    <img src={refPreview} alt="" />
                  </div>
                  <button type="button" className="pro-slot-x" onClick={clearRef}>✕</button>
                </div>
              ) : (
                <div className="pro-photo-slot small" onClick={() => refInput.current?.click()}>
                  <span className="pro-muted">+ Face / style</span>
                </div>
              )}
            </div>
          </div>
          <input ref={mainInput} type="file" accept="image/*" hidden
                 onChange={(e) => pickMain(e.target.files?.[0])} />
          <input ref={refInput} type="file" accept="image/*" hidden
                 onChange={(e) => pickRef(e.target.files?.[0])} />

          <div className="pro-uplabel" style={{ marginTop: 4 }}>Instruction</div>
          <textarea className="pro-input pro-photo-prompt" rows={3} value={prompt}
                    placeholder='Type exactly what to change — "swap this face for the reference photo"…'
                    onChange={(e) => setPrompt(e.target.value)} />
          <div className="row preset-row pro-photo-presets">
            {IMAGE_PRESETS.map((p) => (
              <button key={p} type="button" className="pro-chip" onClick={() => setPrompt(p)}>{p}</button>
            ))}
          </div>

          {err && <p className="error">{err}</p>}
          <button type="button" className="pro-goldbtn"
                  disabled={!mainFile || !prompt.trim() || busy || noCredit}
                  onClick={generate}>
            {busy ? "Generating…" : `✦ Generate — ${imageCredits}`}
          </button>
          {noCredit && <p className="pro-muted pro-note">No credit — buy minutes on the Credits tab.</p>}
        </div>

        <div>
          <div className="pro-uplabel">Output</div>
          <div className="pro-result-slot">
            {busy ? <span className="pro-muted">Generating… can take up to a minute or two</span>
             : resultUrl ? <img src={resultUrl} alt="Result" />
             : <span className="pro-muted">Result appears here</span>}
          </div>
          {resultUrl && (
            <div className="row preset-row" style={{ marginTop: 10 }}>
              <a className="pro-chip pro-dl" href={resultUrl} download="apex-lucy-image.png">⬇ Download</a>
              <button type="button" className="pro-chip" onClick={useAsLiveCharacter} disabled={usedAsLive}>
                {usedAsLive ? "✓ Set as Live Character" : "→ Use as Live Character"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --- About: a spec sheet — capabilities, inputs/outputs, price. No prose. --
function LucyImageAbout({ imageCredits }: { imageCredits: string }) {
  return (
    <div className="pro-card narrow">
      <h3>Capabilities</h3>
      <div className="row preset-row">
        {IMAGE_CAPS.map((c) => <span key={c} className="pro-chip">{c}</span>)}
      </div>

      <h3 style={{ marginTop: 18 }}>Spec</h3>
      <div className="pro-grid3">
        <div><div className="pro-uplabel">Input</div><p className="pro-muted">1 photo + optional reference</p></div>
        <div><div className="pro-uplabel">Instruction</div><p className="pro-muted">Free-text, no fixed menu</p></div>
        <div><div className="pro-uplabel">Output</div><p className="pro-muted">1 edited photo, 720p</p></div>
        <div><div className="pro-uplabel">Provider</div><p className="pro-muted">Nano Banana 2 (Google, via fal)</p></div>
        <div><div className="pro-uplabel">Speed</div><p className="pro-muted">Seconds, not a live stream</p></div>
        <div><div className="pro-uplabel">Cost</div><p className="pro-muted">{imageCredits} / image, on success only</p></div>
      </div>

      <h3 style={{ marginTop: 18 }}>Bridge to Lucy Realtime</h3>
      <p className="pro-muted">
        "Use as Live Character" on a result sends it straight to Lucy Realtime's persona slot —
        no re-upload.
      </p>
    </div>
  );
}

// --- Privacy: honest, model-specific data/consent/risk policy. -------------
function LucyImagePrivacy() {
  return (
    <div className="pro-card narrow">
      <h3>Where your data goes</h3>
      <p className="pro-muted">
        Your uploaded photo and any reference photo go to our cloud processing provider (fal,
        running Google's Nano Banana 2 model) for editing — that's inherent to how a cloud model
        works. Apex Cam does not itself store your uploaded or generated photos beyond what's
        needed to return the result to you.
      </p>
      <h3 style={{ marginTop: 18 }}>Consent</h3>
      <p className="pro-muted">
        <strong>Only use your own likeness, or one you have explicit permission to use.</strong>{" "}
        Using someone else's photo to deceive, defraud, harass, or misrepresent them is prohibited
        here and may be illegal in your jurisdiction. Accounts can be restricted over confirmed
        misuse.
      </p>
      <h3 style={{ marginTop: 18 }}>Labeling</h3>
      <p className="pro-muted">
        Edited/generated photos are labeled AI-generated. You may not remove or misrepresent that
        labeling — same policy that covers Lucy Realtime and local face swapping.
      </p>
    </div>
  );
}

// --- Lucy Restyle: a background VIDEO job (submit -> poll -> download).
// Real preset gallery (clickable sample-style thumbnails shipped with the
// app, not just text prompts) plus a before/after view so the change is
// actually visible, not just a file dropped in your downloads. -----------
const RESTYLE_GALLERY = [
  { name: "anime", label: "Anime" },
  { name: "oilpainting", label: "Oil Painting" },
  { name: "claymation", label: "Claymation" },
  { name: "cyberpunk", label: "Cyberpunk" },
  { name: "watercolor", label: "Watercolor" },
  { name: "vhs", label: "Retro VHS" },
];

function RestylePlayground(props: {
  account: Account;
  perSecRate: number;
  refreshAccount: () => void;
}) {
  const { account, perSecRate, refreshAccount } = props;
  const videoInput = useRef<HTMLInputElement>(null);
  const refInput = useRef<HTMLInputElement>(null);
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoPreview, setVideoPreview] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [refFile, setRefFile] = useState<File | null>(null);
  const [refPreview, setRefPreview] = useState<string | null>(null);
  const [presetName, setPresetName] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  // Restyle takes a text style XOR a reference image, never both — matches
  // Decart's own restyle API constraint.
  const [styleBy, setStyleBy] = useState<"prompt" | "reference">("prompt");
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [costMinutes, setCostMinutes] = useState<number | null>(null);

  const pickVideo = (f: File | undefined) => {
    if (!f) return;
    setVideoFile(f);
    setVideoPreview(URL.createObjectURL(f));
    setDuration(null);
    setResultUrl(null);
  };
  const pickRef = (f: File | undefined) => {
    if (!f) return;
    setRefFile(f);
    setRefPreview(URL.createObjectURL(f));
    setPresetName(null);
  };
  const clearRef = () => { setRefFile(null); setRefPreview(null); setPresetName(null); };
  const pickPreset = async (name: string) => {
    try {
      const blob = await fetch(`/restyle-presets/${name}.jpg`).then((r) => r.blob());
      setRefFile(new File([blob], `${name}.jpg`, { type: "image/jpeg" }));
      setRefPreview(URL.createObjectURL(blob));
      setPresetName(name);
    } catch {
      setErr("Could not load that preset — try again");
    }
  };

  const showRefSlot = styleBy === "reference";
  const showPrompt = styleBy === "prompt";
  const canGenerate = !!videoFile && !busy &&
    (styleBy === "prompt" ? !!prompt.trim() : !!refFile);
  const noCredit = (account.credit_seconds ?? 0) <= 0;

  const estCost = duration != null ? duration * perSecRate : null;
  const estMinutes = duration != null ? (duration * (2 / 3)) / 60 : null;

  const generate = async () => {
    if (!canGenerate || !videoFile) return;
    setErr(null);
    setBusy(true);
    setResultUrl(null);
    setStatusText("Uploading…");
    try {
      const sendPrompt = showPrompt ? prompt.trim() : "";
      const sendRef = showRefSlot ? refFile : null;
      const { job_id, cost_minutes } =
        await cloud.videoStart(videoFile, sendPrompt, "restyle", false, sendRef);
      setCostMinutes(cost_minutes);
      refreshAccount();
      setStatusText("Processing…");
      // Video jobs run much longer than a photo edit — minutes, not seconds.
      const deadline = Date.now() + 10 * 60 * 1000;
      let status = "processing";
      while (status !== "completed" && status !== "failed") {
        if (Date.now() > deadline) throw new Error("Taking too long — try again");
        await sleep(3000);
        status = (await cloud.jobStatus(job_id)).status;
      }
      if (status === "failed") throw new Error("Could not process the video");
      setStatusText("Fetching result…");
      setResultUrl(await cloud.jobContent(job_id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not process the video");
    } finally {
      setBusy(false);
      setStatusText("");
    }
  };

  return (
    <div className="pro-card">
      <div className="pro-editor">
        <div className="pro-editor-inputs">
          <div className="pro-uploads">
            <div>
              <div className="pro-uplabel">Video</div>
              <div className="pro-photo-slot" onClick={() => videoInput.current?.click()}>
                {videoPreview
                  ? <video src={videoPreview} muted
                           onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)} />
                  : <span className="pro-muted">+ Upload video</span>}
              </div>
            </div>
            {showRefSlot && (
              <div>
                <div className="pro-uplabel">Style reference</div>
                {refPreview ? (
                  <div className="pro-slot-wrap">
                    <div className="pro-photo-slot small" onClick={() => refInput.current?.click()}>
                      <img src={refPreview} alt="" />
                    </div>
                    <button type="button" className="pro-slot-x" onClick={clearRef}>✕</button>
                  </div>
                ) : (
                  <div className="pro-photo-slot small" onClick={() => refInput.current?.click()}>
                    <span className="pro-muted">+ Style image</span>
                  </div>
                )}
              </div>
            )}
          </div>
          <input ref={videoInput} type="file" accept="video/*" hidden
                 onChange={(e) => pickVideo(e.target.files?.[0])} />
          <input ref={refInput} type="file" accept="image/*" hidden
                 onChange={(e) => pickRef(e.target.files?.[0])} />

          <div className="row preset-row" style={{ marginTop: 4 }}>
            <button type="button" className={`pro-chip${styleBy === "prompt" ? " on" : ""}`}
                    onClick={() => setStyleBy("prompt")}>Describe a style</button>
            <button type="button" className={`pro-chip${styleBy === "reference" ? " on" : ""}`}
                    onClick={() => setStyleBy("reference")}>Pick a style</button>
          </div>

          {showPrompt && (
            <>
              <div className="pro-uplabel" style={{ marginTop: 4 }}>Style</div>
              <textarea className="pro-input pro-photo-prompt" rows={3} value={prompt}
                        placeholder='e.g. "Anime style, vibrant colors"'
                        onChange={(e) => setPrompt(e.target.value)} />
              <div className="row preset-row pro-photo-presets">
                {RESTYLE_PRESETS.map((p) => (
                  <button key={p} type="button" className="pro-chip" onClick={() => setPrompt(p)}>{p}</button>
                ))}
              </div>
            </>
          )}

          {showRefSlot && (
            <>
              <div className="pro-uplabel" style={{ marginTop: 4 }}>Sample styles</div>
              <div className="restyle-gallery">
                {RESTYLE_GALLERY.map((g) => (
                  <button key={g.name} type="button"
                          className={`restyle-gallery-item${presetName === g.name ? " on" : ""}`}
                          onClick={() => pickPreset(g.name)}>
                    <img src={`/restyle-presets/${g.name}.jpg`} alt={g.label} />
                    <span>{g.label}</span>
                  </button>
                ))}
              </div>
            </>
          )}

          {duration != null && (
            <p className="pro-muted pro-note">
              {duration.toFixed(0)}s video ≈ {estCost !== null ? `$${estCost.toFixed(2)}` : "…"}
              {" "}({estMinutes !== null ? estMinutes.toFixed(2) : "…"} wallet-min)
            </p>
          )}

          {err && <p className="error">{err}</p>}
          <button type="button" className="pro-goldbtn"
                  disabled={!canGenerate || noCredit}
                  onClick={generate}>
            {busy ? (statusText || "Working…") : "✦ Generate"}
          </button>
          {noCredit && <p className="pro-muted pro-note">No credit — buy minutes on the Credits tab.</p>}
          {costMinutes != null && !busy && (
            <p className="pro-muted pro-note">Last job charged {costMinutes.toFixed(2)} minutes.</p>
          )}
        </div>

        <div>
          <div className="pro-uplabel">{resultUrl ? "Before / After" : "Result"}</div>
          {resultUrl ? (
            <div className="restyle-compare">
              <div>
                <div className="pro-uplabel" style={{ marginTop: 0 }}>Before</div>
                <div className="pro-result-slot small">
                  <video src={videoPreview ?? undefined} controls muted />
                </div>
              </div>
              <div>
                <div className="pro-uplabel" style={{ marginTop: 0 }}>After</div>
                <div className="pro-result-slot small"><video src={resultUrl} controls /></div>
              </div>
            </div>
          ) : (
            <div className="pro-result-slot">
              {busy ? <span className="pro-muted">{statusText || "Working…"} — can take a few minutes</span>
               : <span className="pro-muted">Result appears here</span>}
            </div>
          )}
          {resultUrl && (
            <div className="row preset-row" style={{ marginTop: 10 }}>
              <a className="pro-chip pro-dl" href={resultUrl} download="apex-restyle.mp4">⬇ Download</a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --- About: spec sheet, matching the Lucy Image pattern. -------------------
function RestyleAbout({ perSec }: { perSec: number }) {
  return (
    <div className="pro-card narrow">
      <h3>Capabilities</h3>
      <div className="row preset-row">
        {RESTYLE_CAPS.map((c) => <span key={c} className="pro-chip">{c}</span>)}
      </div>

      <h3 style={{ marginTop: 18 }}>Spec</h3>
      <div className="pro-grid3">
        <div><div className="pro-uplabel">Input</div><p className="pro-muted">1 recorded video</p></div>
        <div><div className="pro-uplabel">Style source</div><p className="pro-muted">Text prompt OR reference image</p></div>
        <div><div className="pro-uplabel">Output</div><p className="pro-muted">1 restyled video, 720p</p></div>
        <div><div className="pro-uplabel">Provider</div><p className="pro-muted">Decart Lucy Restyle 2</p></div>
        <div><div className="pro-uplabel">Speed</div><p className="pro-muted">Background job — minutes, not live</p></div>
        <div><div className="pro-uplabel">Cost</div><p className="pro-muted">${perSec.toFixed(3)}/sec of source video</p></div>
      </div>

      <h3 style={{ marginTop: 18 }}>How it's different from Lucy Realtime</h3>
      <p className="pro-muted">
        Restyle works on a video you already recorded, not your live camera — upload a clip, choose
        a style (describe it, hand it a reference image, or pick a sample), and get back a fully
        restyled version, shown side-by-side with your original so you can see exactly what changed.
        No live streaming, no persona photo.
      </p>
    </div>
  );
}

// --- Privacy: honest, model-specific data/consent/risk policy. -------------
function RestylePrivacy() {
  return (
    <div className="pro-card narrow">
      <h3>Where your data goes</h3>
      <p className="pro-muted">
        Your uploaded video (and any reference image) goes to our cloud processing provider
        (Decart) for processing — that's inherent to how a cloud model works. Apex Cam does not
        itself store your uploaded or restyled videos beyond what's needed to return the result
        to you.
      </p>
      <h3 style={{ marginTop: 18 }}>Consent</h3>
      <p className="pro-muted">
        <strong>Only use video of yourself, or footage you have explicit permission to use.</strong>{" "}
        Using someone else's likeness to deceive, defraud, harass, or misrepresent them is
        prohibited here and may be illegal in your jurisdiction. Accounts can be restricted over
        confirmed misuse.
      </p>
      <h3 style={{ marginTop: 18 }}>Labeling</h3>
      <p className="pro-muted">
        Processed videos are labeled AI-generated. You may not remove or misrepresent that
        labeling — same policy that covers every other model here.
      </p>
    </div>
  );
}

// --- Voice Note: clone a voice from a sample, type a message, get an audio
// file (e.g. to send as a WhatsApp voice note). NOT live/real-time — that's
// Cloud Voice on the Voice tab (paused pending cloud billing). This is
// type-and-get-a-file, running on ElevenLabs via fal. ------------------------
function VoiceNotePlayground(props: { account: Account; refreshAccount: () => void }) {
  const { account, refreshAccount } = props;
  const micRef = useRef<MicRecorder | null>(null);
  const recordTimerRef = useRef<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [refBlob, setRefBlob] = useState<Blob | null>(null);
  const [refUrl, setRefUrl] = useState<string | null>(null);
  const [refSeconds, setRefSeconds] = useState(0);
  const [text, setText] = useState("");
  const [credits, setCredits] = useState(0);
  const [maxChars, setMaxChars] = useState(5000);
  const [busy, setBusy] = useState(false);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    return () => { if (refUrl) URL.revokeObjectURL(refUrl); };
  }, [refUrl]);

  // Live credit count as the customer types — never hardcode the rate here.
  useEffect(() => {
    const id = setTimeout(() => {
      cloud.voicenotePricing(text.length).then((p) => {
        setCredits(p.credits);
        setMaxChars(p.max_chars);
      }).catch(() => undefined);
    }, 250);
    return () => clearTimeout(id);
  }, [text.length]);

  const setReference = async (raw: Blob) => {
    setErr(null);
    try {
      const pcm = await decodeToMonoPCM(raw);
      const seconds = pcm.length / 48000;
      if (seconds < MIN_REFERENCE_S) {
        setErr(`That clip is only ${seconds.toFixed(0)}s — use at least ${MIN_REFERENCE_S}s for a good clone.`);
        return;
      }
      if (rms(pcm) < 0.002) {
        setErr("That clip sounds silent — check your microphone and try again.");
        return;
      }
      const wav = encodeWav(pcm);
      if (refUrl) URL.revokeObjectURL(refUrl);
      setRefBlob(wav);
      setRefSeconds(seconds);
      setRefUrl(URL.createObjectURL(wav));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not read that audio");
    }
  };

  const stopRecordingNow = async () => {
    if (recordTimerRef.current) window.clearInterval(recordTimerRef.current);
    recordTimerRef.current = null;
    setRecording(false);
    const blob = await micRef.current?.stop();
    micRef.current = null;
    if (blob) await setReference(blob);
  };

  const toggleRecording = async () => {
    if (recording) {
      await stopRecordingNow();
      return;
    }
    setErr(null);
    try {
      micRef.current = new MicRecorder();
      await micRef.current.start();
      setRecording(true);
      setRecordSeconds(0);
      recordTimerRef.current = window.setInterval(() => {
        setRecordSeconds((s) => {
          if (s + 1 >= 120) stopRecordingNow();
          return s + 1;
        });
      }, 1000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not access the microphone");
    }
  };

  const uploadReference = (f: File | undefined) => {
    if (f) setReference(f);
  };

  const generate = async () => {
    if (!refBlob || !text.trim() || busy) return;
    setErr(null);
    setBusy(true);
    try {
      const { job_id } = await cloud.voicenoteStart(refBlob, text.trim());
      // Matches the server's own VOICENOTE_STUCK_S (studio.py) -- a longer
      // message can genuinely take several minutes; giving up here sooner
      // than the server does just means we stop polling before it's actually
      // finished, not that anything is wrong.
      const deadline = Date.now() + 10 * 60 * 1000;
      let status: "processing" | "done" | "error" = "processing";
      let lastError: string | null | undefined;
      while (status === "processing") {
        if (Date.now() > deadline) throw new Error("Taking too long — try again");
        await sleep(1500);
        const s = await cloud.voicenoteStatus(job_id);
        status = s.status;
        lastError = s.error;
      }
      if (status === "error") throw new Error(lastError || "Could not generate that voice note");
      setResultUrl(await cloud.voicenoteContent(job_id));
      refreshAccount();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not generate that voice note");
    } finally {
      setBusy(false);
    }
  };

  const noCredit = (account.credit_seconds ?? 0) <= 0;

  return (
    <div className="pro-card">
      <div className="pro-editor">
        <div className="pro-editor-inputs">
          <div className="pro-uplabel">Reference voice — the voice to clone</div>
          <div className="row">
            <button type="button" className="pro-chip"
                    style={recording ? { background: "rgba(220,60,60,0.18)", borderColor: "rgba(220,60,60,0.5)", color: "#f08080" } : undefined}
                    disabled={busy} onClick={toggleRecording}>
              {recording ? `■ Stop recording (${recordSeconds}s)` : "🎙 Record"}
            </button>
            <span className="pro-muted">or</span>
            <label className="pro-chip" style={{ cursor: busy ? "not-allowed" : "pointer" }}>
              📁 Upload a clip
              <input type="file" accept="audio/*" hidden disabled={busy}
                     onChange={(e) => uploadReference(e.target.files?.[0])} />
            </label>
          </div>
          {refUrl && (
            <div style={{ marginTop: 8 }}>
              <p className="pro-muted">Reference clip ({refSeconds.toFixed(0)}s):</p>
              <audio controls src={refUrl} style={{ width: "100%" }} />
            </div>
          )}

          <div className="pro-uplabel" style={{ marginTop: 12 }}>Message — what the voice should say</div>
          <textarea className="pro-input pro-photo-prompt" rows={4} value={text}
                    maxLength={maxChars}
                    placeholder="Type exactly what you want said…"
                    onChange={(e) => setText(e.target.value)} />
          <p className="pro-muted" style={{ fontSize: 12 }}>{text.length} / {maxChars} characters</p>

          {err && <p className="error">{err}</p>}
          <button type="button" className="pro-goldbtn"
                  disabled={!refBlob || !text.trim() || busy || noCredit}
                  onClick={generate}>
            {busy ? "Generating…" : `✦ Generate — ${credits} credit${credits === 1 ? "" : "s"}`}
          </button>
          {noCredit && <p className="pro-muted pro-note">No credit — buy minutes on the Credits tab.</p>}
        </div>

        <div>
          <div className="pro-uplabel">Output</div>
          <div className="pro-result-slot">
            {busy ? <span className="pro-muted">Generating…</span>
             : resultUrl ? <audio controls src={resultUrl} style={{ width: "100%" }} />
             : <span className="pro-muted">Result appears here</span>}
          </div>
          {resultUrl && (
            <div className="row preset-row" style={{ marginTop: 10 }}>
              <a className="pro-chip pro-dl" href={resultUrl} download="apex-voice-note.ogg">⬇ Download</a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function VoiceNoteAbout() {
  return (
    <div className="pro-card narrow">
      <h3>Capabilities</h3>
      <div className="row preset-row">
        {["Clone any voice from ~10s+ of audio", "Type a message, get it spoken",
          "Download and send it yourself (e.g. as a WhatsApp voice note)",
          "No training wait"].map((c) => <span key={c} className="pro-chip">{c}</span>)}
      </div>
      <h3 style={{ marginTop: 18 }}>How it's different from Cloud Voice</h3>
      <p className="pro-muted">
        This is <strong>type-and-get-a-file</strong> — you write the message, it speaks it in the
        cloned voice, and you download the result. It is not live: it can't change your voice
        while you talk on a call. That's a separate, currently-paused feature (Cloud Voice, on the
        Voice tab).
      </p>
      <h3 style={{ marginTop: 18 }}>Provider</h3>
      <p className="pro-muted">ElevenLabs (via fal) — priced per character, shown live as you type.</p>
    </div>
  );
}

function VoiceNotePrivacy() {
  return (
    <div className="pro-card narrow">
      <h3>Where your data goes</h3>
      <p className="pro-muted">
        Your reference clip and typed message go to our cloud processing provider (fal, running
        ElevenLabs) to generate the result — that's inherent to how a cloud model works. Apex Cam does
        not itself store your reference clips or generated audio beyond what's needed to return
        the result to you.
      </p>
      <h3 style={{ marginTop: 18 }}>Consent</h3>
      <p className="pro-muted">
        <strong>Only clone your own voice, or a voice you have explicit permission to use.</strong>{" "}
        Using someone else's voice to deceive, defraud, harass, or impersonate them is prohibited
        here and may be illegal in your jurisdiction. Accounts can be restricted over confirmed
        misuse.
      </p>
    </div>
  );
}
