import { useEffect, useRef, useState } from "react";
import { api, type ProStatus, type RefMode } from "../api/client";
import { cloud, signedIn, type Account, type Pkg } from "../api/cloud";
import { DualPreview } from "../components/DualPreview";
import { ProAuth } from "../components/ProAuth";
import { usePipeline } from "../context/PipelineContext";

// Apex Pro — its OWN universe, separate from local Apex Cam: its own dashboard,
// its own cam (studio) page, its own face (persona) page, its own voice and
// billing. The cloud engine (Decart Lucy 2.1 + fal voice) is billed by us; the
// provider key is server-side and never shown to users.

const LOOKS = [
  "Photorealistic 30-year-old man, studio lighting",
  "Photorealistic young woman, soft cinematic light",
  "Anime character, vibrant colors",
  "Realistic older gentleman, warm tone",
  "Fashion model, editorial lighting",
];

// Prepaid minute packages. The price shown comes from the backend (/pro/pricing),
// which sets the currency (Naira for Nigeria) at a buffered FX rate over our
// underlying $0.03/sec.
const PACKAGES = [1, 5, 12, 15, 25, 30, 50, 100, 160, 375, 1000];

// Cloud voices for the Pro tier (voice change/cloning via fal). Real list comes
// from the backend once billing is live; these are the presets shown meanwhile.
// Photo Studio quick edits. "Face swap" uses the persona reference; the rest are
// free-text edits the AI editor applies to the uploaded photo.
const PHOTO_PRESETS: { label: string; prompt: string; ref: RefMode }[] = [
  { label: "Anime", prompt: "Turn this into a vibrant anime illustration", ref: "none" },
  { label: "3D Pixar", prompt: "3D Pixar-style animated character, cute", ref: "none" },
  { label: "Beach", prompt: "Place the person on a sunny tropical beach", ref: "none" },
  { label: "Studio suit", prompt: "Dress the person in a sharp formal suit, studio portrait", ref: "none" },
  { label: "Remove BG", prompt: "Remove the background, clean plain white studio backdrop", ref: "none" },
  { label: "B&W film", prompt: "Black-and-white film photograph, grainy and cinematic", ref: "none" },
  { label: "Cyberpunk", prompt: "Cyberpunk neon city style, moody lighting", ref: "none" },
];

// Plain-English guide to what each Apex Pro mode actually does.
const MODE_GUIDE: { icon: string; name: string; what: string; ref: string; price: string }[] = [
  { icon: "🎥", name: "Live cam", price: "$1.80/min",
    what: "Transforms your camera in real time while you're on a call or streaming.",
    ref: "Reference = the person you become. Or no reference: just describe a look." },
  { icon: "🖼", name: "Photo", price: "$0.40/photo",
    what: "Edits one picture: swap the face, change the background, outfit, or art style.",
    ref: "Reference = a face to become, or a style/outfit to copy. Optional." },
  { icon: "🎬", name: "Video", price: "$3.60/min",
    what: "Same power as Photo, but on a clip (MP4, up to 200 MB). Runs in the background.",
    ref: "Reference = a face to become, or a style/outfit to copy. Optional." },
  { icon: "🎨", name: "Restyle", price: "$1.20/min",
    what: "Repaints a whole video in an art style (anime, cinematic, cyberpunk…). Cheapest, best for long videos.",
    ref: "No reference and no face swap — style only. Your face stays yours." },
];

// What the reference image is used for — the user picks this explicitly.
const REF_MODES: { id: RefMode; label: string; help: string }[] = [
  { id: "face", label: "Face swap",
    help: "Become the person in the reference photo — their face and identity." },
  { id: "style", label: "Style & outfit",
    help: "Keep your own face; copy the reference's look, clothes and style." },
  { id: "none", label: "No reference",
    help: "Ignore the reference — your typed instruction alone drives the edit." },
];

const VOICES = [
  { id: "deep-male", name: "Deep Male", tag: "narrator" },
  { id: "warm-female", name: "Warm Female", tag: "soft" },
  { id: "young-male", name: "Young Male", tag: "casual" },
  { id: "bright-female", name: "Bright Female", tag: "energetic" },
  { id: "robotic", name: "Robotic", tag: "fx" },
];

type Page = "dashboard" | "studio" | "photo" | "video" | "restyle" | "personas" | "voice" | "look" | "credits";

const NAV: { id: Page; icon: string; label: string }[] = [
  { id: "dashboard", icon: "◆", label: "Dashboard" },
  { id: "studio", icon: "🎥", label: "Live cam" },
  { id: "photo", icon: "🖼", label: "Photo" },
  { id: "video", icon: "🎬", label: "Video" },
  { id: "restyle", icon: "🎨", label: "Restyle" },
  { id: "personas", icon: "🪪", label: "Personas" },
  { id: "voice", icon: "🎙", label: "Voice" },
  { id: "look", icon: "✨", label: "Look" },
  { id: "credits", icon: "◈", label: "Credits" },
];

// Restyle quick styles (artistic — no face swap).
const RESTYLE_PRESETS = [
  "Anime style", "Cinematic film look", "Cyberpunk neon", "Oil painting",
  "3D Pixar animation", "Black & white noir", "Watercolor", "Comic book",
];

// Video editor quick edits (free-text; keep the person's own face).
const VIDEO_PRESETS = [
  "Turn the video into anime style", "Change the background to a sunny beach",
  "Dress the person in a formal suit", "Cinematic film color grade, dramatic lighting",
  "Cyberpunk neon city style", "Black-and-white film look",
];

/** One independent video job (upload -> submit -> poll -> result).
 *  Video and Restyle each get their OWN instance, so a clip or a result in one
 *  never leaks into the other. */
function useVideoJob(onCredit: () => void) {
  const [file, setFile] = useState<File | null>(null);
  const [fileName, setFileName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<"" | "submitting" | "processing" | "done" | "error">("");
  const [result, setResult] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const busy = status === "submitting" || status === "processing";

  const pick = (f: File | undefined) => {
    if (!f) return;
    setFile(f); setFileName(f.name); setResult(null); setStatus(""); setErr(null);
  };
  const run = (mode: "video" | "restyle", promptText: string,
               refMode: RefMode = "none", reference: File | null = null) => {
    if (!file) { setErr("Upload a video first"); return; }
    setStatus("submitting"); setErr(null); setResult(null);
    // Jobs run on the CLOUD server: it holds the Decart key and charges the
    // cloud wallet (the same one purchases top up).
    cloud.videoStart(file, promptText, mode, refMode === "face",
                     mode === "video" ? reference : null)
      .then((r) => {
        setStatus("processing");
        const poll = () => cloud.jobStatus(r.job_id).then((s) => {
          if (s.status === "completed") {
            cloud.jobContent(r.job_id)
              .then((u) => { setResult(u); setStatus("done"); onCredit(); })
              .catch(() => setTimeout(poll, 4000));
          } else if (s.status === "failed") {
            setStatus("error"); setErr("The job failed — try again"); onCredit();
          } else setTimeout(poll, 3000);
        }).catch(() => setTimeout(poll, 4000));
        poll();
      })
      .catch((e) => { setStatus("error"); setErr(String(e.message || e)); });
  };
  return { file, fileName, prompt, setPrompt, status, result, err, setErr, fileRef, busy, pick, run };
}

export function ProTab() {
  const pipeline = usePipeline();
  const [pro, setPro] = useState<ProStatus | null>(null);
  const [entered, setEntered] = useState(false);
  const [page, setPage] = useState<Page>("dashboard");
  const [prompt, setPrompt] = useState("");
  const [voice, setVoice] = useState<string>("");
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

  // Packages come LIVE from the cloud server (cloud.packages) — so adding, removing
  // or repricing a package on the server updates every customer instantly, with no
  // app update. Falls back to a default list only if the server can't be reached.
  const [pkgs, setPkgs] = useState<Pkg[]>([]);

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.enabled) setEntered(true);
    }).catch(() => setPro(null));
    api.getProPricing().then(setPricing).catch(() => undefined);
    cloud.packages().then(setPkgs).catch(() => undefined);
  }, []);

  // Show USD prominently (premium feel); the charge happens in charge_currency.
  const usdLabel = (min: number) =>
    pricing ? `$${(min * pricing.usd_per_minute).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "…";
  const chargeLabel = (min: number) => {
    if (!pricing || pricing.charge_currency === "USD") return "";
    const sym = pricing.charge_currency === "NGN" ? "₦" : "";
    return `${sym}${Math.round(min * pricing.charge_per_minute).toLocaleString()}`;
  };

  // Poll the balance. The credit that PURCHASES add lives on the CLOUD account
  // (cloud.me) — that's the real wallet, and where a Flutterwave payment lands.
  // (api.getPro is only the local engine's live flag.)
  useEffect(() => {
    if (!entered) return;
    const t = setInterval(() => {
      cloud.me().then(setAccount).catch(() => undefined);
      api.getPro().then(setPro).catch(() => undefined);
    }, 1500);
    return () => clearInterval(t);
  }, [entered]);

  // Live per-second balance, like Decart. Sourced from the CLOUD account (in
  // credit-seconds) so a purchase shows immediately; ticks down while live and
  // re-syncs on every poll.
  const [secsLeft, setSecsLeft] = useState(0);
  useEffect(() => { setSecsLeft(Math.round(account?.credit_seconds ?? 0)); }, [account?.credit_seconds]);
  useEffect(() => {
    if (!pro?.enabled || !pro?.live) return;   // only counts while Lucy is transforming
    const id = setInterval(() => setSecsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [pro?.enabled, pro?.live]);
  const perSec = pricing ? pricing.usd_per_minute / 60 : 0.03;
  const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  // GO LIVE / Stop for the Lucy live cam — fully CLOUD-driven:
  //   1. cloud.liveStart -> the SERVER does the Decart handshake (its key) and
  //      meters the cloud wallet per second; it returns a LiveKit room.
  //   2. the local engine joins that room with just the token (no key on the PC)
  //      and pumps camera frames through it.
  // The shared pipeline starts first so the preview shows Lucy, not the raw cam.
  const [liveSession, setLiveSession] = useState<string | null>(null);
  const [liveErr, setLiveErr] = useState<string | null>(null);
  const save = async (enabled: boolean, p = prompt) => {
    setLiveErr(null);
    try {
      if (enabled && liveSession) {
        // Already live — this is just a prompt/look update.
        await cloud.livePrompt(liveSession, p).catch(() => undefined);
        return;
      }
      if (enabled) {
        if ((account?.credit_seconds ?? 0) <= 0) {
          setLiveErr("No credit — buy minutes first, then GO LIVE.");
          return;
        }
        if (!pipeline.active) await pipeline.start();   // release local cam
        // Send the saved persona to the server (it sets the face on Decart).
        let ref: File | null = null;
        try {
          const b = await fetch(api.proReferenceUrl).then((r) => (r.ok ? r.blob() : null));
          if (b) ref = new File([b], "persona.jpg", { type: "image/jpeg" });
        } catch { /* no persona yet — Lucy runs prompt-only */ }
        const r = await cloud.liveStart(p, ref);
        setLiveSession(r.session_id);
        await api.proLiveCloud(r.livekit_url, r.token).then(setPro);
      } else {
        if (liveSession) cloud.liveStop(liveSession).catch(() => undefined);
        setLiveSession(null);
        await api.proLiveCloudStop().then(setPro).catch(() => undefined);
        if (pipeline.active) await pipeline.stop();     // hand the camera back
      }
    } catch (e) {
      setLiveErr(e instanceof Error ? e.message : "Could not go live — try again");
      if (liveSession) cloud.liveStop(liveSession).catch(() => undefined);
      setLiveSession(null);
      await api.proLiveCloudStop().catch(() => undefined);
    } finally {
      api.getPro().then(setPro).catch(() => undefined);
      refreshCredit();
    }
  };
  const uploadRef = (f: File | undefined) => {
    if (f) api.uploadProReference(f).then(setPro).catch(() => undefined);
  };
  const pickVoice = (v: string) => {
    setVoice(v);
    api.setProVoice({ enabled: v !== "", voice: v || null }).catch(() => undefined);
  };

  // Photo Studio — full AI photo editor: upload a picture, then face-swap into
  // the persona OR type any edit (restyle, background, outfit, add/remove…).
  const [photoInput, setPhotoInput] = useState<File | null>(null);
  const [photoInputUrl, setPhotoInputUrl] = useState<string | null>(null);
  const [photoRef, setPhotoRef] = useState<File | null>(null);
  const [photoRefUrl, setPhotoRefUrl] = useState<string | null>(null);
  const [photoPrompt, setPhotoPrompt] = useState("");
  const [photoResult, setPhotoResult] = useState<string | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoErr, setPhotoErr] = useState<string | null>(null);
  const photoFile = useRef<HTMLInputElement>(null);
  const photoRefFile = useRef<HTMLInputElement>(null);
  const onPhotoPick = (f: File | undefined) => {
    if (!f) return;
    setPhotoInput(f); setPhotoInputUrl(URL.createObjectURL(f));
    setPhotoResult(null); setPhotoErr(null);
  };
  const [photoRefMode, setPhotoRefMode] = useState<RefMode>("face");
  const onRefPick = (f: File | undefined) => {
    if (!f) return;
    setPhotoRef(f); setPhotoRefUrl(URL.createObjectURL(f)); setPhotoErr(null);
  };
  const clearPhotoRef = () => { setPhotoRef(null); setPhotoRefUrl(null); };
  const runPhoto = (promptText: string, refMode: RefMode) => {
    if (!photoInput) { setPhotoErr("Upload a photo to edit first"); return; }
    if (refMode === "style" && !photoRef) {
      setPhotoErr("Upload a reference image to copy its look"); return;
    }
    setPhotoBusy(true); setPhotoErr(null); setPhotoResult(null);
    // Photo edits run on the CLOUD server (key + wallet live there).
    cloud.photo(photoInput, promptText, refMode === "face", photoRef)
      .then((u) => { setPhotoResult(u); cloud.me().then(setAccount).catch(() => undefined); })
      .catch((e) => setPhotoErr(String(e.message || e)))
      .finally(() => setPhotoBusy(false));
  };

  // Video + Restyle jobs (upload clip -> background job -> poll -> result video).
  // Video and Restyle are INDEPENDENT — separate uploads, prompts, jobs, results.
  const refreshCredit = () => { cloud.me().then(setAccount).catch(() => undefined); };
  const vid = useVideoJob(refreshCredit);   // Video tab (lucy-2.5, face swap + edits)
  const rst = useVideoJob(refreshCredit);   // Restyle tab (lucy-restyle-2, style only)

  // Reference image belongs to the Video tab only (Restyle takes no face).
  const [vRef, setVRef] = useState<File | null>(null);
  const [vRefUrl, setVRefUrl] = useState<string | null>(null);
  const [vRefMode, setVRefMode] = useState<RefMode>("face");
  const clearVRef = () => { setVRef(null); setVRefUrl(null); };
  const vRefRef = useRef<HTMLInputElement>(null);
  const runVideo = (mode: "video" | "restyle", promptText: string, refMode: RefMode = "none") => {
    if (refMode === "style" && !vRef) {
      vid.setErr("Upload a reference image to copy its look"); return;
    }
    vid.run(mode, promptText, refMode, vRef);
  };
  // Real purchase: open the Flutterwave checkout in the browser. This MUST go to
  // the CLOUD server — that's where the Flutterwave key lives. (The local backend
  // has no key: the installer strips secrets, so buying there silently did nothing.)
  // After paying, the server callback credits the account and our poll shows it.
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
          <span className="pro-vip">VIP</span>
        </div>
        <div className="pro-card narrow">
          <p className="pro-muted">
            Apex Pro is a <strong>separate premium space</strong> powered by the{" "}
            <strong>Lucy 2.1</strong> real-time model — the same class of engine behind the top
            persona apps. It transforms your <strong>entire camera</strong> (face, body, scene,
            side profile) into a lifelike persona, live — with cloud voice cloning to match. Its
            own face, its own voice, its own universe.
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

  return (
    <div className="pro pro-universe">
      {/* Hero bar — always visible across the universe */}
      <div className="pro-hero">
        <span className="pro-crown">✦</span>
        <div>
          <h1 className="pro-title">APEX&nbsp;PRO</h1>
          <p className="pro-sub">Cloud studio · Lucy 2.1 + voice · photorealistic full-cam persona</p>
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
          <span className="pro-vip">VIP</span>
        </div>
      </div>

      <div className="pro-shell">
        {/* Pro's OWN navigation — separate from the local app */}
        <nav className="pro-nav-rail">
          {NAV.map((n) => (
            <button key={n.id} type="button"
                    className={`pro-nav-item${page === n.id ? " active" : ""}`}
                    onClick={() => setPage(n.id)}>
              <span className="pro-nav-ic">{n.icon}</span>{n.label}
            </button>
          ))}
        </nav>

        <div className="pro-page">
          {pro.error && <p className="error">{pro.error}</p>}
          {liveErr && <p className="error">{liveErr}</p>}

          {page === "dashboard" && (
            <>
              <div className="pro-status">
                <span className="pro-pill">{(account?.credit_seconds ?? 0) > 0 ? "Cloud ready" : "No credit — buy minutes"}</span>
                {liveBadge}
                <span className="pro-pill">{pro.model}</span>
                {pro.enabled ? (
                  <button type="button" className="btn danger" onClick={() => save(false)}>■ Stop</button>
                ) : (
                  <button type="button" className="pro-goldbtn"
                          disabled={(account?.credit_seconds ?? 0) <= 0}
                          onClick={() => save(true)}>✦ GO LIVE</button>
                )}
              </div>
              <div className="pro-grid3">
                <div className="pro-card">
                  <h3>Persona</h3>
                  <p className="pro-muted">{pro.has_reference ? "Face set ✓" : "No face yet"}</p>
                  <button type="button" className="pro-chip" onClick={() => setPage("personas")}>
                    {pro.has_reference ? "Change face" : "Add face"}
                  </button>
                </div>
                <div className="pro-card">
                  <h3>Voice</h3>
                  <p className="pro-muted">{voice || "Your natural voice"}</p>
                  <button type="button" className="pro-chip" onClick={() => setPage("voice")}>Pick voice</button>
                </div>
                <div className="pro-card">
                  <h3>Balance</h3>
                  <p className="pro-muted">{clock(secsLeft)} left</p>
                  <p className="pro-muted" style={{ fontSize: 12 }}>
                    ${perSec.toFixed(2)}/sec · ${(perSec * 60).toFixed(2)}/min — charged per second while live
                  </p>
                  <button type="button" className="pro-chip" onClick={() => setPage("credits")}>Buy minutes</button>
                </div>
              </div>
              <div className="pro-card">
                <h3>What each mode does</h3>
                <p className="pro-muted">
                  Everything runs from the same credit. Pick the mode that matches what you have —
                  a live camera, a photo, or a video.
                </p>
                <div className="pro-guide">
                  {MODE_GUIDE.map((m) => (
                    <div key={m.name} className="pro-guide-row">
                      <span className="pro-guide-ic">{m.icon}</span>
                      <div>
                        <div className="pro-guide-head">
                          <strong>{m.name}</strong>
                          <span className="pro-pill">{m.price}</span>
                        </div>
                        <p className="pro-muted">{m.what}</p>
                        <p className="pro-muted pro-guide-ref">{m.ref}</p>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="pro-muted pro-note">
                  <strong>The reference photo</strong> is optional. Use it as a{" "}
                  <strong>Face swap</strong> (become that person), as{" "}
                  <strong>Style &amp; outfit</strong> (keep your face, copy their look), or pick{" "}
                  <strong>No reference</strong> and just type what you want. You can remove a
                  reference any time with the ✕ on its thumbnail.
                </p>
              </div>
            </>
          )}

          {page === "studio" && (
            <>
              <div className="pro-status">
                {liveBadge}
                {pro.enabled ? (
                  <button type="button" className="btn danger" onClick={() => save(false)}>■ Stop</button>
                ) : (
                  <button type="button" className="pro-goldbtn"
                          disabled={(account?.credit_seconds ?? 0) <= 0}
                          onClick={() => save(true)}>✦ GO LIVE</button>
                )}
              </div>
              <div className="pro-card">
                <h3>Live studio — the real you vs your AI persona</h3>
                <DualPreview />
                <p className="pro-muted pro-note">
                  Once your persona is set and you have minutes, GO LIVE routes your camera through
                  the cloud model. Select “OBS Virtual Camera” in Zoom/Meet/Teams to appear as your
                  persona everywhere.
                </p>
              </div>
            </>
          )}

          {page === "photo" && (
            <div className="pro-card">
              <h3>Photo Studio — AI photo editor</h3>
              <p className="pro-muted">
                Upload a photo, add a <strong>reference</strong> face/style if you want, then
                tell it what to do — “change the face to the reference”, “put me on a beach”,
                “make it anime”. ~$0.40 per edit, from your credit.
              </p>
              <div className="pro-editor">
                {/* Left: inputs */}
                <div className="pro-editor-inputs">
                  <div className="pro-uploads">
                    <div>
                      <div className="pro-uplabel">Photo to edit</div>
                      <div className="pro-photo-slot" onClick={() => photoFile.current?.click()}>
                        {photoInputUrl ? <img src={photoInputUrl} alt="" />
                          : <span className="pro-muted">＋ Photo</span>}
                      </div>
                      <input ref={photoFile} type="file" accept="image/*" hidden
                             onChange={(e) => onPhotoPick(e.target.files?.[0])} />
                    </div>
                    <div>
                      <div className="pro-uplabel">Reference <span className="pro-muted">(optional)</span></div>
                      <div className="pro-slot-wrap">
                        <div className="pro-photo-slot small" onClick={() => photoRefFile.current?.click()}>
                          {photoRefUrl ? <img src={photoRefUrl} alt="" />
                            : <span className="pro-muted">＋ Face / style</span>}
                        </div>
                        {photoRefUrl && (
                          <button type="button" className="pro-slot-x" title="Remove reference"
                                  onClick={clearPhotoRef}>✕</button>
                        )}
                      </div>
                      <input ref={photoRefFile} type="file" accept="image/*" hidden
                             onChange={(e) => onRefPick(e.target.files?.[0])} />
                    </div>
                  </div>

                  {/* How the reference is used */}
                  <div className="pro-uplabel">Use the reference as</div>
                  <div className="row preset-row">
                    {REF_MODES.map((m) => (
                      <button key={m.id} type="button" title={m.help}
                              className={`pro-chip${photoRefMode === m.id ? " on" : ""}`}
                              onClick={() => setPhotoRefMode(m.id)}>{m.label}</button>
                    ))}
                  </div>
                  <p className="pro-muted pro-note">
                    {REF_MODES.find((m) => m.id === photoRefMode)?.help}
                  </p>

                  <div className="pro-uplabel">What do you want?</div>
                  <input className="pro-input" type="text" value={photoPrompt}
                         placeholder="Leave blank to just apply the mode above, or describe any edit…"
                         onChange={(e) => setPhotoPrompt(e.target.value)} />
                  <div className="row preset-row pro-photo-presets">
                    {PHOTO_PRESETS.map((p) => (
                      <button key={p.label} type="button" className="pro-chip"
                              disabled={photoBusy || !photoInput}
                              onClick={() => runPhoto(p.prompt, p.ref)}>{p.label}</button>
                    ))}
                  </div>
                  <div className="row preset-row">
                    <button type="button" className="pro-goldbtn"
                            disabled={photoBusy || !photoInput ||
                                      (photoRefMode === "none" && !photoPrompt)}
                            onClick={() => runPhoto(photoPrompt, photoRefMode)}>✦ Transform</button>
                  </div>
                  {photoErr && <p className="error">{photoErr}</p>}
                </div>

                {/* Right: result */}
                <div className="pro-editor-result">
                  <div className="pro-uplabel">Result</div>
                  <div className="pro-result-slot">
                    {photoBusy ? <span className="pro-muted">Editing… (a few seconds)</span>
                      : photoResult ? <img src={photoResult} alt="" />
                      : <span className="pro-muted">Your edited photo appears here</span>}
                  </div>
                  {photoResult && (
                    <a className="pro-goldbtn pro-dl" href={photoResult} download="apexpro-photo.png">
                      ⤓ Download</a>
                  )}
                </div>
              </div>
            </div>
          )}

          {page === "video" && (
            <div className="pro-card">
              <h3>Video Studio — AI video editor</h3>
              <p className="pro-muted">
                Upload a clip and do <strong>anything</strong>: face-swap into a reference person,
                or type any edit (restyle, background, outfit, add/remove…). 720p, runs in the
                background. ~$3.60/min of video. <strong>MP4 only, up to 200 MB.</strong>
              </p>
              <div className="pro-editor">
                <div className="pro-editor-inputs">
                  <div className="pro-uploads">
                    <div>
                      <div className="pro-uplabel">Video clip</div>
                      <div className="pro-photo-slot" onClick={() => vid.fileRef.current?.click()}>
                        <span className="pro-muted">{vid.fileName || "＋ Upload video"}</span>
                      </div>
                      <input ref={vid.fileRef} type="file" accept="video/mp4,.mp4" hidden
                             onChange={(e) => vid.pick(e.target.files?.[0])} />
                    </div>
                    <div>
                      <div className="pro-uplabel">Reference <span className="pro-muted">(optional)</span></div>
                      <div className="pro-slot-wrap">
                        <div className="pro-photo-slot small" onClick={() => vRefRef.current?.click()}>
                          {vRefUrl ? <img src={vRefUrl} alt="" /> : <span className="pro-muted">＋ Face / style</span>}
                        </div>
                        {vRefUrl && (
                          <button type="button" className="pro-slot-x" title="Remove reference"
                                  onClick={clearVRef}>✕</button>
                        )}
                      </div>
                      <input ref={vRefRef} type="file" accept="image/*" hidden
                             onChange={(e) => { const f = e.target.files?.[0]; if (f) { setVRef(f); setVRefUrl(URL.createObjectURL(f)); } }} />
                    </div>
                  </div>

                  <div className="pro-uplabel">Use the reference as</div>
                  <div className="row preset-row">
                    {REF_MODES.map((m) => (
                      <button key={m.id} type="button" title={m.help}
                              className={`pro-chip${vRefMode === m.id ? " on" : ""}`}
                              onClick={() => setVRefMode(m.id)}>{m.label}</button>
                    ))}
                  </div>
                  <p className="pro-muted pro-note">
                    {REF_MODES.find((m) => m.id === vRefMode)?.help}
                  </p>

                  <div className="pro-uplabel">What do you want?</div>
                  <input className="pro-input" type="text" value={vid.prompt}
                         placeholder="Leave blank to just apply the mode above, or describe any edit…"
                         onChange={(e) => vid.setPrompt(e.target.value)} />
                  <div className="row preset-row pro-photo-presets">
                    {VIDEO_PRESETS.map((p) => (
                      <button key={p} type="button" className="pro-chip" disabled={vid.busy || !vid.file}
                              onClick={() => { vid.setPrompt(p); runVideo("video", p, "none"); }}>
                        {p.split(",")[0].replace(/^(Turn the video into |Change the background to |Dress the person in )/, "")}</button>
                    ))}
                  </div>
                  <div className="row preset-row">
                    <button type="button" className="pro-goldbtn"
                            disabled={vid.busy || !vid.file || (vRefMode === "none" && !vid.prompt)}
                            onClick={() => runVideo("video", vid.prompt, vRefMode)}>✦ Transform video</button>
                  </div>
                  {vid.err && <p className="error">{vid.err}</p>}
                </div>
                <div className="pro-editor-result">
                  <div className="pro-uplabel">Result</div>
                  <div className="pro-result-slot">
                    {vid.busy ? <span className="pro-muted">{vid.status === "submitting" ? "Uploading…" : "Processing your video… (this can take a while)"}</span>
                      : vid.result ? <video src={vid.result} controls />
                      : <span className="pro-muted">Your video appears here</span>}
                  </div>
                  {vid.result && <a className="pro-goldbtn pro-dl" href={vid.result} download="apexpro-video.mp4">⤓ Download</a>}
                </div>
              </div>
            </div>
          )}

          {page === "restyle" && (
            <div className="pro-card">
              <h3>Restyle — give a long video a new look</h3>
              <p className="pro-muted">
                Upload a video and choose a style (anime, cinematic, cyberpunk…). This restyles
                the whole scene — it does <strong>not</strong> swap the face. Great for long clips,
                cheaper: ~$1.20/min.
              </p>
              <div className="pro-editor">
                <div className="pro-editor-inputs">
                  <div className="pro-uplabel">Video</div>
                  <div className="pro-photo-slot" onClick={() => rst.fileRef.current?.click()}>
                    <span className="pro-muted">{rst.fileName || "＋ Upload video"}</span>
                  </div>
                  <input ref={rst.fileRef} type="file" accept="video/mp4,.mp4" hidden
                         onChange={(e) => rst.pick(e.target.files?.[0])} />
                  <div className="pro-uplabel">Style</div>
                  <input className="pro-input" type="text" value={rst.prompt}
                         placeholder="Describe the style…"
                         onChange={(e) => rst.setPrompt(e.target.value)} />
                  <div className="row preset-row pro-photo-presets">
                    {RESTYLE_PRESETS.map((s) => (
                      <button key={s} type="button" className="pro-chip" disabled={rst.busy || !rst.file}
                              onClick={() => { rst.setPrompt(s); rst.run("restyle", s); }}>{s}</button>
                    ))}
                  </div>
                  <div className="row preset-row">
                    <button type="button" className="pro-goldbtn" disabled={rst.busy || !rst.file || !rst.prompt}
                            onClick={() => rst.run("restyle", rst.prompt)}>✦ Restyle video</button>
                  </div>
                  {rst.err && <p className="error">{rst.err}</p>}
                </div>
                <div className="pro-editor-result">
                  <div className="pro-uplabel">Result</div>
                  <div className="pro-result-slot">
                    {rst.busy ? <span className="pro-muted">{rst.status === "submitting" ? "Uploading…" : "Restyling your video…"}</span>
                      : rst.result ? <video src={rst.result} controls />
                      : <span className="pro-muted">Your restyled video appears here</span>}
                  </div>
                  {rst.result && <a className="pro-goldbtn pro-dl" href={rst.result} download="apexpro-restyle.mp4">⤓ Download</a>}
                </div>
              </div>
            </div>
          )}

          {page === "personas" && (
            <div className="pro-card">
              <h3>Your persona — the face the cloud becomes</h3>
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
                    This is the identity Apex Pro becomes — <strong>separate</strong> from the local
                    face library. Use a clear, front-facing photo for the best result.
                  </p>
                  <button type="button" className="pro-chip" onClick={() => refFile.current?.click()}>
                    {pro.has_reference ? "Change face" : "Upload face"}
                  </button>
                </div>
                <input ref={refFile} type="file" accept="image/*" hidden
                       onChange={(e) => uploadRef(e.target.files?.[0])} />
              </div>
            </div>
          )}

          {page === "voice" && (
            <div className="pro-card">
              <h3>Cloud voice</h3>
              <p className="pro-muted">
                Apex Pro changes your voice in the cloud to match your persona — separate from the
                local voice changer. Pick a voice, or upload a sample to clone.
              </p>
              <div className="row preset-row" style={{ marginTop: 12 }}>
                <button type="button" className={`pro-chip${voice === "" ? " on" : ""}`}
                        onClick={() => pickVoice("")}>Natural (off)</button>
                {VOICES.map((v) => (
                  <button key={v.id} type="button"
                          className={`pro-chip${voice === v.name ? " on" : ""}`}
                          onClick={() => pickVoice(v.name)}>{v.name}</button>
                ))}
              </div>
              <p className="pro-muted pro-note">
                Voice cloning activates with Apex Pro (needs cloud minutes). Uploading a voice
                sample to clone comes online with billing.
              </p>
            </div>
          )}

          {page === "look" && (
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
          )}

          {page === "credits" && (
            <>
              <div className="pro-card">
                <h3>Minutes — buy a package</h3>
                <p className="pro-muted">
                  Apex Pro runs on prepaid minutes. Minutes burn per second only while you're live
                  ({`$${perSec.toFixed(2)}/sec`}), and leftovers stay for next time.
                </p>
                {buyErr && <p className="error">{buyErr}</p>}
              </div>
              <div className="pro-packages">
                {(pkgs.length ? pkgs : PACKAGES.map((m) => ({ minutes: m, usd: 0, charge: 0, currency: "NGN" }))).map((p) => (
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
          )}
        </div>
      </div>
    </div>
  );
}
