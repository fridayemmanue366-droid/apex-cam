// Apex Cam cloud client — talks to OUR server (accounts, credits, payments, and
// the Decart studio). The provider keys live on the server, never here, and the
// credit balance is server-side so it can't be tampered with locally.

// Our live cloud server (Render). Override with VITE_APEXCAM_SERVER for local dev
// (e.g. http://127.0.0.1:8900). If you later put a custom domain in front of it,
// change this one line and rebuild.
const CLOUD =
  (import.meta as unknown as { env?: Record<string, string> }).env?.VITE_APEXCAM_SERVER ||
  "https://apexcam-api.onrender.com";

const TOKEN_KEY = "apexcam.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}
export function signedIn(): boolean {
  return !!getToken();
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${CLOUD}${path}`, { ...init, headers });
  if (res.status === 401) {
    setToken(null);
    throw new Error("Please sign in again");
  }
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(j.detail || "Request failed");
  }
  return res.json() as Promise<T>;
}

// GET an authenticated binary result (a finished photo/video job) and hand
// back an object URL — a plain <img>/<video src> can't attach a Bearer token,
// so this fetches it manually. Same 401 handling as call(): a stale token
// gets cleared here too, so a dead session shows as "signed out", not a
// cached balance with every real request quietly failing underneath it.
async function fetchBlob(path: string): Promise<string> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${CLOUD}${path}`, { headers });
  if (res.status === 401) {
    setToken(null);
    throw new Error("Your session expired — sign out and sign in again");
  }
  if (!res.ok) throw new Error("Result not ready");
  return URL.createObjectURL(await res.blob());
}

export interface Account { email: string; minutes: number; credit_seconds: number }
export interface Pkg { minutes: number; usd: number; charge: number; currency: string }
export interface SubStatus {
  active: boolean; trial: boolean; ever_paid: boolean; until: number; days_left: number;
  price_ngn: number; price_usd: number; currency: string; sub_days: number;
}

// Cache the access expiry so a brief network drop doesn't lock a paid-up user out.
// It only ever GRANTS access inside a window the server already confirmed — it can't
// be edited to unlock beyond what was paid for (the real gate is server-side).
const ACCESS_KEY = "apexcam.access_until";
export function cacheAccessUntil(until: number) {
  try { localStorage.setItem(ACCESS_KEY, String(until)); } catch { /* ignore */ }
}
export function cachedActive(): boolean {
  const v = Number(localStorage.getItem(ACCESS_KEY) || 0);
  return v > Date.now() / 1000;
}

export const cloud = {
  url: CLOUD,

  // --- accounts ---
  register: async (email: string, password: string) => {
    const r = await call<{ token: string; email: string; minutes: number }>(
      "/auth/register",
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }) });
    setToken(r.token);
    return r;
  },
  login: async (email: string, password: string) => {
    const r = await call<{ token: string; email: string; minutes: number }>(
      "/auth/login",
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }) });
    setToken(r.token);
    return r;
  },
  logout: () => setToken(null),
  me: () => call<Account>("/me"),

  // --- subscription (local-app access: 1-day trial, then ₦20,000/month) ---
  subscription: async () => {
    const s = await call<SubStatus>("/subscription");
    cacheAccessUntil(s.until);   // remember for offline grace
    return s;
  },
  startSubscription: () =>
    call<{ link: string; tx_ref: string }>("/subscription/start", { method: "POST" }),

  // --- credits / payment ---
  packages: () => call<Pkg[]>("/pay/packages"),
  startPayment: (minutes: number) =>
    call<{ link: string; tx_ref: string }>("/pay/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes }),
    }),

  // --- studio (metered server-side) ---
  // Per-second sell rate for every metered mode, live from the admin panel —
  // each of video/restyle has its own independent margin now, not a fixed
  // ratio of live's rate, so this must be fetched, never hardcoded.
  studioPricing: () =>
    call<{ currency: string; live_usd_per_sec: number; video_usd_per_sec: number;
           restyle_usd_per_sec: number }>("/studio/pricing"),
  // Live, admin-panel-driven — never hardcode this in the UI. `credits` is what
  // the customer sees; currency/usd are for reference only, not shown to them.
  imagePricing: () =>
    call<{ credits: number; currency: string; usd: number; charge: number }>("/studio/image/pricing"),
  // Lucy Image runs as a background job — Decart's own endpoint is synchronous
  // and a real photo can take it 30-90+ seconds, too long to hold one client
  // HTTP connection open behind Render's reverse proxy. photoStart returns
  // instantly with a job id; poll photoStatus, then fetch photoContent once
  // it's done. A dropped connection mid-generation loses nothing — the job
  // keeps running server-side and the client just polls the same id again.
  photoStart: (file: File, prompt: string, faceSwap: boolean, reference?: File | null) => {
    const f = new FormData();
    f.append("file", file);
    f.append("prompt", prompt);
    f.append("face_swap", String(faceSwap));
    if (reference) f.append("reference", reference);
    return call<{ job_id: string }>("/studio/photo/start", { method: "POST", body: f });
  },
  photoStatus: (id: string) =>
    call<{ status: "processing" | "done" | "error" }>(`/studio/photo/${id}`),
  photoContent: (id: string) => fetchBlob(`/studio/photo/${id}/content`),

  // --- Voice Notes: clone a voice from a sample, type a message, get an
  // audio file back (e.g. to send as a WhatsApp voice note). NOT the live
  // real-time voice changer (Cloud Voice, paused) -- this is type-and-get-a-
  // file, same async job shape as Photo above (F5-TTS on fal).
  voicenotePricing: (chars: number) =>
    call<{ credits: number; currency: string; usd: number; charge: number; max_chars: number }>(
      `/studio/voicenote/pricing?chars=${chars}`),
  voicenoteStart: (reference: Blob, text: string) => {
    const f = new FormData();
    f.append("reference", reference, "reference.wav");
    f.append("text", text);
    return call<{ job_id: string; cost_credits: number }>("/studio/voicenote/start", { method: "POST", body: f });
  },
  voicenoteStatus: (id: string) =>
    call<{ status: "processing" | "done" | "error"; error?: string | null }>(`/studio/voicenote/${id}`),
  voicenoteContent: (id: string) => fetchBlob(`/studio/voicenote/${id}/content`),
  videoStart: (file: File, prompt: string, mode: "video" | "restyle",
               faceSwap: boolean, reference?: File | null) => {
    const f = new FormData();
    f.append("file", file);
    f.append("prompt", prompt);
    f.append("mode", mode);
    f.append("face_swap", String(faceSwap));
    if (reference) f.append("reference", reference);
    return call<{ job_id: string; cost_minutes: number }>("/studio/video/start",
      { method: "POST", body: f });
  },
  jobStatus: (id: string) => call<{ status: string }>(`/studio/job/${id}`),
  jobUrl: (id: string) => `${CLOUD}/studio/job/${id}/content`,
  // The content endpoint needs the Bearer token, which a plain <video src> can't
  // send — fetch it authenticated and hand back an object URL instead.
  jobContent: (id: string) => fetchBlob(`/studio/job/${id}/content`),

  // --- live cam: server mints credentials for whichever provider is active ---
  // (APEXCAM_PRO_PROVIDER) — "decart" returns a LiveKit room, "fal" returns a
  // short-lived JWT. Check `provider` in the response to know which fields you got.
  liveStart: (prompt: string, reference?: File | null) => {
    const f = new FormData();
    f.append("prompt", prompt);
    if (reference) f.append("reference", reference);
    return call<{
      session_id: string;
      provider: "fal" | "decart";
      livekit_url?: string;
      token?: string;
      jwt?: string;
      model?: string;
    }>("/studio/live/start", { method: "POST", body: f });
  },
  // decart only — fal sessions update their look locally (api.setPro), never
  // through the server, since fal holds no session open here to relay into.
  livePrompt: (sessionId: string, prompt: string) => {
    const f = new FormData();
    f.append("session_id", sessionId);
    f.append("prompt", prompt);
    return call<{ ok: boolean }>("/studio/live/prompt", { method: "POST", body: f });
  },
  liveStop: (sessionId: string) => {
    const f = new FormData();
    f.append("session_id", sessionId);
    return call<{ stopped: boolean }>("/studio/live/stop", { method: "POST", body: f });
  },

  // --- cloud voice cloning (Apex Pro): mints a short-lived Modal token +
  // session id. The desktop hands these straight to the LOCAL backend
  // (client.ts's audioCloudStart), which opens the actual WebSocket and
  // streams mic audio to Modal — this call never touches audio itself.
  voiceCloudStart: () =>
    call<{ session_id: string; token: string; url: string }>(
      "/studio/voice/cloud/start", { method: "POST" }),
  voiceCloudStop: (sessionId: string) => {
    const f = new FormData();
    f.append("session_id", sessionId);
    return call<{ stopped: boolean }>("/studio/voice/cloud/stop", { method: "POST", body: f });
  },
};
