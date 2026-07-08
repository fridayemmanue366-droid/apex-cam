import { useEffect, useRef, useState } from "react";
import { api, type ProStatus } from "../api/client";
import { DualPreview } from "../components/DualPreview";

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

// Prepaid minute packages. Price = minutes x 60s x $0.03/sec (our per-second rate;
// we pay the provider $0.02 and keep $0.01). Payment is wired later.
const RATE = 0.03;   // what WE charge the customer per second (cost is $0.02; we keep $0.01)
const PACKAGES = [5, 12, 15, 25, 30, 50, 100, 160, 375, 1000];
const price = (min: number) => (min * 60 * RATE).toFixed(2);

// Cloud voices for the Pro tier (voice change/cloning via fal). Real list comes
// from the backend once billing is live; these are the presets shown meanwhile.
const VOICES = [
  { id: "deep-male", name: "Deep Male", tag: "narrator" },
  { id: "warm-female", name: "Warm Female", tag: "soft" },
  { id: "young-male", name: "Young Male", tag: "casual" },
  { id: "bright-female", name: "Bright Female", tag: "energetic" },
  { id: "robotic", name: "Robotic", tag: "fx" },
];

type Page = "dashboard" | "studio" | "personas" | "voice" | "look" | "credits";

const NAV: { id: Page; icon: string; label: string }[] = [
  { id: "dashboard", icon: "◆", label: "Dashboard" },
  { id: "studio", icon: "🎥", label: "Studio" },
  { id: "personas", icon: "🪪", label: "Personas" },
  { id: "voice", icon: "🎙", label: "Voice" },
  { id: "look", icon: "✨", label: "Look" },
  { id: "credits", icon: "◈", label: "Credits" },
];

export function ProTab() {
  const [pro, setPro] = useState<ProStatus | null>(null);
  const [entered, setEntered] = useState(false);
  const [page, setPage] = useState<Page>("dashboard");
  const [prompt, setPrompt] = useState("");
  const [voice, setVoice] = useState<string>("");
  const refFile = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getPro().then((p) => {
      setPro(p);
      setPrompt(p.prompt);
      if (p.enabled) setEntered(true);
    }).catch(() => setPro(null));
  }, []);

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
  const buy = (min: number) =>
    api.addProCredits(min).then(() => api.getPro().then(setPro)).catch(() => undefined);

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
                    <div className="pro-pack-price">${price(m)}</div>
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
