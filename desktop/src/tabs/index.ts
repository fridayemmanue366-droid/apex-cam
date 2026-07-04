import type { ComponentType } from "react";
import { HomeTab } from "./HomeTab";
import { FaceTab } from "./FaceTab";
import { VoiceTab } from "./VoiceTab";
import { CameraTab } from "./CameraTab";
import { AudioTab } from "./AudioTab";
import { AIModelsTab } from "./AIModelsTab";
import { PerformanceTab } from "./PerformanceTab";
import { RecordingTab } from "./RecordingTab";
import { StreamingTab } from "./StreamingTab";
import { SettingsTab } from "./SettingsTab";

export const TABS = [
  "Home",
  "Face",
  "Voice",
  "Camera",
  "Audio",
  "AI Models",
  "Performance",
  "Recording",
  "Streaming",
  "Settings",
] as const;

export type TabName = (typeof TABS)[number];

export const TAB_SUMMARY: Record<TabName, string> = {
  Home: "Overview, live preview, and system status.",
  Face: "Face detection, landmarks, pose, swapping, reenactment, expression transfer.",
  Voice: "Real-time voice cloning/conversion, pitch, noise reduction, echo cancellation.",
  Camera: "Camera input and the virtual camera device: resolution, FPS, blur, green screen.",
  Audio: "Microphone input and the virtual microphone device: gain, noise suppression.",
  "AI Models": "Model manager — download, load, and switch face/voice/lip-sync models.",
  Performance: "GPU, FPS, and latency monitors; acceleration settings.",
  Recording: "Record the processed stream and capture screenshots.",
  Streaming: "How to select EMY CAM's virtual devices in Zoom, Discord, Meet, Teams, OBS.",
  Settings: "Appearance, language, and responsible-use status.",
};

export const TAB_COMPONENTS: Record<TabName, ComponentType> = {
  Home: HomeTab,
  Face: FaceTab,
  Voice: VoiceTab,
  Camera: CameraTab,
  Audio: AudioTab,
  "AI Models": AIModelsTab,
  Performance: PerformanceTab,
  Recording: RecordingTab,
  Streaming: StreamingTab,
  Settings: SettingsTab,
};
