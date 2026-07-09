import { useEffect, useRef, useState } from "react";
import { api, type ProStatus } from "../api/client";
import { cloud, signedIn, type Account } from "../api/cloud";
import { DualPreview } from "../components/DualPreview";
import { ProAuth } from "../components/ProAuth";

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
const PACKAGES = [5, 12, 15, 25, 30, 50, 100, 160, 375, 1000];

// Cloud voices for the Pro tier (voice change/cloning via fal). Real list comes
// from the backend once billing is live; these are the presets shown meanwhile.
// Photo Studio quick edits. "Face swap" uses the persona reference; the rest are
// free-text edits the AI editor applies to the uploaded photo.
const PHOTO_PRESETS: { label: string; prompt: string; face: boolean }[] = [
  { label: "Face swap", prompt: "", face: true },
  { label: "Anime", prompt: "Turn this into a vibrant anime illustration", face: false },
  { label: "3D Pixar", prompt: "3D Pixar-style animated character, cute", face: false },
  { label: "Beach", prompt: "Place the person on a sunny tropical beach", face: false },
  { label: "Studio suit", prompt: "Dress the person in a sharp formal suit, studio portrait", face: false },
  { label: "Remove BG", prompt: "Remove the background, clean plain white studio backdrop", face: false },
  { label: "B&W film", prompt: "Black-and-white film photograph, grainy and cinematic", face: false },
  { label: "Cyberpunk", prompt: "Cyberpunk neon city style, moody lighting", face: false },
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

export function ProTab() {
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

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.enabled) setEntered(true);
    }).catch(() => setPro(null));
    api.getProPricing().then(setPricing).catch(() => undefined);
  }, []);

  // Show USD prominently (premium feel); the charge happens in charge_currency.
  const usdLabel = (min: number) =>
    pricing ? `$${(min * pricing.usd_per_minute).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "…";
  const chargeLabel = (min: number) => {
    if (!pricing || pricing.charge_currency === "USD") return "";
    const sym = pricing.charge_currency === "NGN" ? "₦" : "";
    return `${sym}${Math.round(min * pricing.charge_per_minute).toLocaleString()}`;
  };

  // Poll the balance so the countdown updates live and the UI reflects an
  // auto-stop (when minutes run out, the backend flips enabled -> false).
  useEffect(() => {
    if (!entered) return;
    const t = setInterval(() => {
      api.getPro().then(setPro).catch(() => undefined);
    }, 1500);
    return () => clearInterval(t);
  }, [entered]);

  const minutes = pro?.minutes_remaining ?? 0;

  const save = (enabled: boolean, p = prompt) =>
    api.setPro({ enabled, prompt: p }).then(setPro).catch(() => undefined);
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
  const onRefPick = (f: File | undefined) => {
    if (!f) return;
    setPhotoRef(f); setPhotoRefUrl(URL.createObjectURL(f)); setPhotoErr(null);
  };
  const runPhoto = (promptText: string, faceSwap: boolean) => {
    if (!photoInput) { setPhotoErr("Upload a photo to edit first"); return; }
    setPhotoBusy(true); setPhotoErr(null); setPhotoResult(null);
    api.makeProPhoto(photoInput, promptText, faceSwap, photoRef)
      .then((u) => { setPhotoResult(u); api.getPro().then(setPro).catch(() => undefined); })
      .catch((e) => setPhotoErr(String(e.message || e)))
      .finally(() => setPhotoBusy(false));
  };

  // Video + Restyle jobs (upload clip -> background job -> poll -> result video).
  // Shared state; one job at a time.
  const [vFile, setVFile] = useState<File | null>(null);
  const [vFileName, setVFileName] = useState("");
  const [vRef, setVRef] = useState<File | null>(null);
  const [vRefUrl, setVRefUrl] = useState<string | null>(null);
  const [vPrompt, setVPrompt] = useState("");
  const [vStatus, setVStatus] = useState<"" | "submitting" | "processing" | "done" | "error">("");
  const [vResult, setVResult] = useState<string | null>(null);
  const [vErr, setVErr] = useState<string | null>(null);
  const vFileRef = useRef<HTMLInputElement>(null);
  const vRefRef = useRef<HTMLInputElement>(null);
  const vBusy = vStatus === "submitting" || vStatus === "processing";
  const runVideo = (mode: "video" | "restyle", promptText: string, faceSwap = false) => {
    if (!vFile) { setVErr("Upload a video first"); return; }
    setVStatus("submitting"); setVErr(null); setVResult(null);
    api.startProVideo(vFile, promptText, mode, mode === "video" ? vRef : null, faceSwap)
      .then((r) => {
        setVStatus("processing");
        const poll = () => api.getProJob(r.job_id).then((s) => {
          if (s.status === "completed") {
            setVResult(api.proJobContentUrl(r.job_id)); setVStatus("done");
            api.getPro().then(setPro).catch(() => undefined);
          } else if (s.status === "failed") { setVStatus("error"); setVErr("The job failed — try again"); }
          else setTimeout(poll, 3000);
        }).catch(() => setTimeout(poll, 4000));
        poll();
      })
      .catch((e) => { setVStatus("error"); setVErr(String(e.message || e)); });
  };
  const pickVideo = (f: File | undefined) => {
    if (!f) return; setVFile(f); setVFileName(f.name); setVResult(null); setVStatus(""); setVErr(null);
  };
  // Real purchase: open the Flutterwave checkout in the browser. After paying,
  // the backend callback adds the minutes and our poll picks up the new balance.
  const buy = (min: number) =>
    api.startPayment(min)
      .then((r) => { window.open(r.link, "_blank"); })
      .catch(() => undefined);

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
          <span className="pro-pill">◈ {minutes} min</span>
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

          {page === "dashboard" && (
            <>
              <div className="pro-status">
                <span className="pro-pill">{pro.configured ? "Cloud ready" : "Cloud not funded yet"}</span>
                {liveBadge}
                <span className="pro-pill">{pro.model}</span>
                {pro.enabled ? (
                  <button type="button" className="btn danger" onClick={() => save(false)}>■ Stop</button>
                ) : (
                  <button type="button" className="pro-goldbtn"
                          disabled={!pro.configured || !pro.has_credit}
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
                  <h3>Minutes</h3>
                  <p className="pro-muted">{minutes} min left</p>
                  <button type="button" className="pro-chip" onClick={() => setPage("credits")}>Buy minutes</button>
                </div>
              </div>
              <div className="pro-card">
                <h3>What Apex Pro does</h3>
                <p className="pro-muted">
                  Your whole camera — face, body, hair, background, any angle — becomes a lifelike
                  persona in real time, with a matching cloud voice. Set your persona, pick a look
                  and voice, buy minutes, and GO LIVE. Pick “OBS Virtual Camera” in your call app.
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
                          disabled={!pro.configured || !pro.has_credit}
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
                      <div className="pro-photo-slot small" onClick={() => photoRefFile.current?.click()}>
                        {photoRefUrl ? <img src={photoRefUrl} alt="" />
                          : <span className="pro-muted">＋ Face / style</span>}
                      </div>
                      <input ref={photoRefFile} type="file" accept="image/*" hidden
                             onChange={(e) => onRefPick(e.target.files?.[0])} />
                    </div>
                  </div>

                  <div className="pro-uplabel">What do you want?</div>
                  <input className="pro-input" type="text" value={photoPrompt}
                         placeholder="e.g. change the face in the photo to the reference person"
                         onChange={(e) => setPhotoPrompt(e.target.value)} />
                  <div className="row preset-row pro-photo-presets">
                    {photoRef && (
                      <button type="button" className="pro-chip on" disabled={photoBusy || !photoInput}
                              onClick={() => runPhoto("Change the face in the photo to the reference person, keep everything else", false)}>
                        Face → reference</button>
                    )}
                    {PHOTO_PRESETS.map((p) => (
                      <button key={p.label} type="button" className="pro-chip"
                              disabled={photoBusy || !photoInput}
                              onClick={() => runPhoto(p.prompt, p.face)}>{p.label}</button>
                    ))}
                  </div>
                  <div className="row preset-row">
                    <button type="button" className="pro-goldbtn"
                            disabled={photoBusy || !photoInput || (!photoPrompt && !photoRef)}
                            onClick={() => runPhoto(photoPrompt, false)}>✦ Transform</button>
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
                background. ~$3.60/min of video.
              </p>
              <div className="pro-editor">
                <div className="pro-editor-inputs">
                  <div className="pro-uploads">
                    <div>
                      <div className="pro-uplabel">Video clip</div>
                      <div className="pro-photo-slot" onClick={() => vFileRef.current?.click()}>
                        <span className="pro-muted">{vFileName || "＋ Upload video"}</span>
                      </div>
                      <input ref={vFileRef} type="file" accept="video/*" hidden
                             onChange={(e) => pickVideo(e.target.files?.[0])} />
                    </div>
                    <div>
                      <div className="pro-uplabel">Reference face <span className="pro-muted">(optional)</span></div>
                      <div className="pro-photo-slot small" onClick={() => vRefRef.current?.click()}>
                        {vRefUrl ? <img src={vRefUrl} alt="" /> : <span className="pro-muted">＋ Face</span>}
                      </div>
                      <input ref={vRefRef} type="file" accept="image/*" hidden
                             onChange={(e) => { const f = e.target.files?.[0]; if (f) { setVRef(f); setVRefUrl(URL.createObjectURL(f)); } }} />
                    </div>
                  </div>
                  <div className="pro-uplabel">What do you want?</div>
                  <input className="pro-input" type="text" value={vPrompt}
                         placeholder="e.g. change the face to the reference, or make it anime"
                         onChange={(e) => setVPrompt(e.target.value)} />
                  <div className="row preset-row pro-photo-presets">
                    <button type="button" className="pro-chip on" disabled={vBusy || !vFile}
                            onClick={() => runVideo("video", vRef ? "Change the face in the video to the reference person" : "", true)}>
                      Face swap</button>
                    {VIDEO_PRESETS.map((p) => (
                      <button key={p} type="button" className="pro-chip" disabled={vBusy || !vFile}
                              onClick={() => { setVPrompt(p); runVideo("video", p, false); }}>
                        {p.split(",")[0].replace(/^(Turn the video into |Change the background to |Dress the person in )/, "")}</button>
                    ))}
                  </div>
                  <div className="row preset-row">
                    <button type="button" className="pro-goldbtn" disabled={vBusy || !vFile || (!vPrompt && !vRef)}
                            onClick={() => runVideo("video", vPrompt, false)}>✦ Transform video</button>
                  </div>
                  {vErr && <p className="error">{vErr}</p>}
                </div>
                <div className="pro-editor-result">
                  <div className="pro-uplabel">Result</div>
                  <div className="pro-result-slot">
                    {vBusy ? <span className="pro-muted">{vStatus === "submitting" ? "Uploading…" : "Processing your video… (this can take a while)"}</span>
                      : vResult ? <video src={vResult} controls />
                      : <span className="pro-muted">Your video appears here</span>}
                  </div>
                  {vResult && <a className="pro-goldbtn pro-dl" href={vResult} download="apexpro-video.mp4">⤓ Download</a>}
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
                  <div className="pro-photo-slot" onClick={() => vFileRef.current?.click()}>
                    <span className="pro-muted">{vFileName || "＋ Upload video"}</span>
                  </div>
                  <input ref={vFileRef} type="file" accept="video/*" hidden
                         onChange={(e) => pickVideo(e.target.files?.[0])} />
                  <div className="pro-uplabel">Style</div>
                  <input className="pro-input" type="text" value={vPrompt}
                         placeholder="Describe the style…"
                         onChange={(e) => setVPrompt(e.target.value)} />
                  <div className="row preset-row pro-photo-presets">
                    {RESTYLE_PRESETS.map((s) => (
                      <button key={s} type="button" className="pro-chip" disabled={vBusy || !vFile}
                              onClick={() => { setVPrompt(s); runVideo("restyle", s); }}>{s}</button>
                    ))}
                  </div>
                  <div className="row preset-row">
                    <button type="button" className="pro-goldbtn" disabled={vBusy || !vFile || !vPrompt}
                            onClick={() => runVideo("restyle", vPrompt)}>✦ Restyle video</button>
                  </div>
                  {vErr && <p className="error">{vErr}</p>}
                </div>
                <div className="pro-editor-result">
                  <div className="pro-uplabel">Result</div>
                  <div className="pro-result-slot">
                    {vBusy ? <span className="pro-muted">{vStatus === "submitting" ? "Uploading…" : "Restyling your video…"}</span>
                      : vResult ? <video src={vResult} controls />
                      : <span className="pro-muted">Your restyled video appears here</span>}
                  </div>
                  {vResult && <a className="pro-goldbtn pro-dl" href={vResult} download="apexpro-restyle.mp4">⤓ Download</a>}
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
                  Apex Pro runs on prepaid minutes. Minutes burn per second only while you're live,
                  and leftovers stay for next time. (Checkout is wired next.)
                </p>
              </div>
              <div className="pro-packages">
                {PACKAGES.map((m) => (
                  <div key={m} className="pro-pack">
                    <div className="pro-pack-min">{m}<span> min</span></div>
                    <div className="pro-pack-price">{usdLabel(m)}</div>
                    {chargeLabel(m) && <div className="pro-pack-alt">≈ {chargeLabel(m)}</div>}
                    <button type="button" className="pro-chip" onClick={() => buy(m)}>Buy</button>
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
