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

async function callBlob(path: string, body: FormData): Promise<string> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${CLOUD}${path}`, { method: "POST", body, headers });
  // Same handling as call(): a stale/expired token must be cleared here too,
  // otherwise the UI keeps showing a cached "signed in" account while every
  // photo/video call keeps failing silently underneath it.
  if (res.status === 401) {
    setToken(null);
    throw new Error("Your session expired — sign out and sign in again");
  }
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(j.detail || "Request failed");
  }
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
  // Live, admin-panel-driven — never hardcode this in the UI. `credits` is what
  // the customer sees; currency/usd are for reference only, not shown to them.
  imagePricing: () =>
    call<{ credits: number; currency: string; usd: number; charge: number }>("/studio/image/pricing"),
  photo: (file: File, prompt: string, faceSwap: boolean, reference?: File | null) => {
    const f = new FormData();
    f.append("file", file);
    f.append("prompt", prompt);
    f.append("face_swap", String(faceSwap));
    if (reference) f.append("reference", reference);
    return callBlob("/studio/photo", f);
  },
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
  jobContent: async (id: string): Promise<string> => {
    const token = getToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(`${CLOUD}/studio/job/${id}/content`, { headers });
    if (!res.ok) throw new Error("Result not ready");
    return URL.createObjectURL(await res.blob());
  },

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
};
