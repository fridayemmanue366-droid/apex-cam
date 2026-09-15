// Typed client for the local AI backend (REST now, WebSocket telemetry later).
// Must match the backend port (APEXCAM_PORT / config.py default).
const BASE = "http://127.0.0.1:8790";

export interface Health {
  status: string;
  version: string;
  env: string;
}

export interface Capabilities {
  gpu: { available: boolean; name: string | null };
  target: { width: number; height: number; fps: number };
  responsible_use: { label_output: boolean; audit_log: boolean };
  swap_backend: "neural" | "landmark";
}

export interface Metrics {
  fps: number;
  latency_ms: number;
  gpu_util: number;
  gpu_mem_mb: number;
}

export interface FaceState {
  enabled: boolean;
  model: string;
  swap_enabled: boolean;
  profile_id: string | null;
}

export interface SetupStatus {
  python: string;
  has_nvidia_gpu: boolean;
  gpu_name: string | null;
  onnx_providers: string[];
  on_gpu: boolean;
  neural_available: boolean;
  enhancer_available: boolean;
  installing: boolean;
  install_done: boolean;
  install_ok: boolean;
  install_tail: string[];
}

export interface SwapMode {
  mode: "fast" | "neural" | "avatar";
  neural_available: boolean;
  avatar_available: boolean;
  backend: "landmark" | "neural";
}

export interface FaceProfile {
  id: string;
  name: string;
  created: number;
  builtin: boolean;
}

export interface PipelineStatus {
  running: boolean;
  fps: number;
  latency_ms: number;
  frames: number;
  camera_index: number;
  proc_width: number;
  error: string | null;
  vcam_active: boolean;
  vcam_device: string | null;
  vcam_error: string | null;
  faces_detected: number;
}

export interface CameraInfo {
  index: number;
  name: string;
  virtual: boolean;
}

export interface CameraList {
  cameras: CameraInfo[];
  auto: number;
}

// Which camera the AI engine reads. -1 = auto-pick the real webcam (skip
// YouCam/virtual/IR). Persisted so the customer's choice sticks across restarts.
const AI_CAM_KEY = "apexcam.aiCameraIndex";

export function getAiCameraIndex(): number {
  const v = localStorage.getItem(AI_CAM_KEY);
  return v == null || v === "" ? -1 : Number(v);
}

export function setAiCameraIndex(index: number): void {
  localStorage.setItem(AI_CAM_KEY, String(index));
}

export interface PerformanceSettings {
  proc_width: number;
}

export interface TrackingSettings {
  enabled: boolean;
  show_overlay: boolean;
  available: boolean;
  lip_sync: boolean;
  body_pose: boolean;
  body_pose_available: boolean;
}

export interface BackgroundSettings {
  mode: "off" | "blur" | "color" | "image";
  blur_strength: number;
  color: string;
  available: boolean;
  has_image: boolean;
}

export interface AudioDevice {
  index: number;
  name: string;
}

export interface AudioDevices {
  inputs: AudioDevice[];
  outputs: AudioDevice[];
  cable_output: number | null;
}

export interface AudioStatus {
  running: boolean;
  level: number;
  input_device: number | null;
  output_device: number | null;
  output_name: string | null;
  error: string | null;
}

export interface VoiceParams {
  enabled: boolean;
  noise_reduction: boolean;
  gain: number;
  pitch: number;
}

export interface RVCStatus {
  base_present: boolean;
  voices: string[];
  enabled: boolean;
  voice: string | null;
  pitch_shift: number;
  on_gpu: boolean;
}

export interface CloudVoiceStatus {
  enabled: boolean;
  connected: boolean;
  error: string | null;
}

export interface EnhanceSettings {
  brightness: number;
  contrast: number;
  saturation: number;
  sharpen?: number;
  beautify?: number;
}

export interface ProStatus {
  enabled: boolean;
  configured: boolean;
  model: string;
  has_reference: boolean;
  prompt: string;
  live?: boolean;
  minutes_remaining: number;
  has_credit: boolean;
  error: string | null;
}

export interface Credits {
  minutes_remaining: number;
  has_credit: boolean;
}

/** What the reference image is used for.
 *  face  = become that person (identity / face swap)
 *  style = keep your face, copy the look/outfit/style
 *  none  = ignore any reference; the prompt alone drives the edit */
export type RefMode = "face" | "style" | "none";

export interface ModelInfo {
  id: string;
  name: string;
  kind: "face" | "voice" | "lipsync" | "enhance";
  status: "active" | "not_installed" | "planned";
  phase: number;
  description: string;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    // Surface the server's real explanation (FastAPI puts it in `detail`) instead
    // of a bare status code — "…busy, try again", "Lost the internet connection", etc.
    let msg = "";
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") msg = body.detail;
      else if (body?.detail) msg = JSON.stringify(body.detail);
    } catch { /* non-JSON body — fall back below */ }
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

const put = <T,>(path: string, body: T) =>
  req<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  health: () => req<Health>("/health"),
  capabilities: () => req<Capabilities>("/capabilities"),
  metrics: () => req<Metrics>("/metrics"),
  getFace: () => req<FaceState>("/face"),
  setFace: (s: FaceState) => put<FaceState>("/face", s),
  getSwapStrength: () => req<{ strength: number }>("/face/swap/strength"),
  setSwapStrength: (strength: number) =>
    put<{ strength: number }>("/face/swap/strength", { strength }),
  getSkinMatch: () => req<{ strength: number }>("/face/swap/skin-match"),
  setSkinMatch: (strength: number) =>
    put<{ strength: number }>("/face/swap/skin-match", { strength }),
  getSwapMode: () => req<SwapMode>("/face/swap/mode"),
  setSwapMode: (mode: "fast" | "neural" | "avatar") =>
    put<SwapMode>("/face/swap/mode", {
      mode,
      neural_available: false,
      avatar_available: false,
      backend: "landmark",
    }),

  setupStatus: () => req<SetupStatus>("/setup"),
  installGpu: () => req<{ started: boolean }>("/setup/gpu", { method: "POST" }),
  installDirectml: () => req<{ started: boolean }>("/setup/directml", { method: "POST" }),
  getEnhancers: () => req<{ available: string[] }>("/setup/enhancers"),
  setEnhancer: (kind: string) =>
    req<{ kind: string; active: string; available: string[] }>("/setup/enhancer", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
  models: () => req<ModelInfo[]>("/models"),

  listProfiles: () => req<FaceProfile[]>("/face/profiles"),
  uploadProfile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<FaceProfile>("/face/profiles", { method: "POST", body: form });
  },
  deleteProfile: (id: string) => req<{ deleted: boolean }>(`/face/profiles/${id}`, { method: "DELETE" }),
  profileImageUrl: (id: string) => `${BASE}/face/profiles/${id}/image`,

  pipelineStatus: () => req<PipelineStatus>("/pipeline"),
  listCameras: () => req<CameraList>("/pipeline/cameras"),
  // -1 = let the backend auto-pick the real webcam (skip YouCam/virtual/IR).
  pipelineStart: (cameraIndex = -1) =>
    req<PipelineStatus>("/pipeline/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_index: cameraIndex }),
    }),
  pipelineStop: () => req<PipelineStatus>("/pipeline/stop", { method: "POST" }),
  getEnhance: () => req<EnhanceSettings>("/enhance"),
  setEnhance: (e: EnhanceSettings) => put<EnhanceSettings>("/enhance", e),
  vcamStart: () => req<PipelineStatus>("/vcam/start", { method: "POST" }),
  vcamStop: () => req<PipelineStatus>("/vcam/stop", { method: "POST" }),
  reset: () => req<PipelineStatus>("/reset", { method: "POST" }),
  getPerformance: () => req<PerformanceSettings>("/performance"),
  setPerformance: (p: PerformanceSettings) => put<PerformanceSettings>("/performance", p),
  getTracking: () => req<TrackingSettings>("/tracking"),
  setTracking: (t: TrackingSettings) => put<TrackingSettings>("/tracking", t),
  getBackground: () => req<BackgroundSettings>("/background"),
  setBackground: (s: BackgroundSettings) => put<BackgroundSettings>("/background", s),
  uploadBackground: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<BackgroundSettings>("/background/image", { method: "POST", body: form });
  },

  getPro: () => req<ProStatus>("/pro"),
  setPro: (cfg: { enabled: boolean; prompt: string }) =>
    req<ProStatus>("/pro", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg),
    }),
  uploadProReference: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return req<ProStatus>("/pro/reference", { method: "POST", body: form });
  },
  getProPricing: () => req<{ usd_per_minute: number; charge_currency: string; charge_per_minute: number }>("/pro/pricing"),
  getProCredits: () => req<Credits>("/pro/credits"),
  addProCredits: (minutes: number) =>
    req<Credits>("/pro/credits/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes }),
    }),
  startPayment: (minutes: number) =>
    req<{ link: string; tx_ref: string; amount: number }>("/pro/pay/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes }),
    }),
  makeProPhoto: async (file: File, prompt: string, refMode: RefMode,
                       reference?: File | null): Promise<string> => {
    const form = new FormData();
    form.append("file", file);
    form.append("prompt", prompt);
    form.append("ref_mode", refMode);
    if (reference) form.append("reference", reference);
    const res = await fetch(`${BASE}/pro/photo`, { method: "POST", body: form });
    if (!res.ok) {
      const j = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(j.detail || "Photo failed");
    }
    return URL.createObjectURL(await res.blob());
  },
  startProVideo: (file: File, prompt: string, mode: "video" | "restyle",
                  reference?: File | null, refMode: RefMode = "none") => {
    const form = new FormData();
    form.append("file", file);
    form.append("prompt", prompt);
    form.append("mode", mode);
    form.append("ref_mode", refMode);
    if (reference) form.append("reference", reference);
    return req<{ job_id: string; cost_minutes: number }>("/pro/video/start", { method: "POST", body: form });
  },
  getProJob: (jobId: string) => req<{ status: string }>(`/pro/job/${jobId}`),
  proJobContentUrl: (jobId: string) => `${BASE}/pro/job/${jobId}/content`,
  // Cloud live cam: the CLOUD server does the Decart handshake (key stays there)
  // and returns a LiveKit room; the local engine just joins it with the token.
  proReferenceUrl: `${BASE}/pro/reference`,
  // room shape depends on which provider the server minted credentials for
  // (see cloud.ts liveStart) — decart gives livekit_url/token, fal gives jwt/model.
  proLiveCloud: (room: {
    provider: "fal" | "decart";
    livekit_url?: string;
    token?: string;
    jwt?: string;
    model?: string;
    session_id?: string;
    cloud_url?: string;
    auth?: string;
  }) =>
    req<ProStatus>("/pro/live/cloud", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // session_id/cloud_url/auth let the PC heartbeat the cloud meter so the
      // customer is billed only while Lucy is really streaming (not connecting).
      body: JSON.stringify(room),
    }),
  proLiveCloudStop: () => req<ProStatus>("/pro/live/cloud/stop", { method: "POST" }),

  audioDevices: () => req<AudioDevices>("/audio/devices"),
  audioStatus: () => req<AudioStatus>("/audio"),
  audioStart: (input_device: number | null, output_device: number | null) =>
    req<AudioStatus>("/audio/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_device, output_device }),
    }),
  audioStop: () => req<AudioStatus>("/audio/stop", { method: "POST" }),
  getVoiceParams: () => req<VoiceParams>("/audio/params"),
  setVoiceParams: (p: VoiceParams) => put<VoiceParams>("/audio/params", p),
  getRvc: () => req<RVCStatus>("/audio/rvc"),
  setRvc: (s: { enabled: boolean; voice: string | null; pitch_shift: number }) =>
    req<RVCStatus>("/audio/rvc", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(s),
    }),

  // Cloud voice (Apex Pro): this machine gets a Modal ws_url + token from the
  // CLOUD server (cloud.ts's voiceCloudStart) and hands them here — the local
  // backend opens the actual WebSocket and heartbeats cloud_url/auth/session_id
  // on its own, same broker split as proLiveCloud.
  audioCloudStatus: () => req<CloudVoiceStatus>("/audio/cloud"),
  audioCloudStart: (room: {
    ws_url: string;
    token: string;
    voice: string;
    pitch_shift: number;
    session_id: string;
    cloud_url: string;
    auth: string;
  }) =>
    req<CloudVoiceStatus>("/audio/cloud/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(room),
    }),
  audioCloudStop: () => req<CloudVoiceStatus>("/audio/cloud/stop", { method: "POST" }),
};

export const WS_PREVIEW_URL = BASE.replace("http", "ws") + "/ws/preview";
