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

export interface VoiceState {
  enabled: boolean;
  model: string;
  pitch: number;
  noise_reduction: boolean;
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
  error: string | null;
}

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
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
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
  getEnhancers: () => req<{ available: string[] }>("/setup/enhancers"),
  setEnhancer: (kind: string) =>
    req<{ kind: string; active: string; available: string[] }>("/setup/enhancer", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind }),
    }),
  getVoice: () => req<VoiceState>("/voice"),
  setVoice: (s: VoiceState) => put<VoiceState>("/voice", s),
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
  pipelineStart: (cameraIndex = 0) =>
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
  setPro: (cfg: { enabled: boolean; api_key: string | null; prompt: string }) =>
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
};

export const WS_PREVIEW_URL = BASE.replace("http", "ws") + "/ws/preview";
