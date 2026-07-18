import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, WS_PREVIEW_URL } from "../api/client";
import { useMedia } from "./MediaContext";

// AI-engine mode: the backend owns the camera and runs the full processing
// pipeline; the UI receives raw+processed preview frames over WebSocket.
// This processed output is exactly what the virtual camera (Phase 4) will
// hand to WhatsApp/Telegram/Zoom — external apps never see the raw camera.

interface PipelineState {
  active: boolean;
  starting: boolean;
  fps: number;
  latencyMs: number;
  error: string | null;
  rawSrc: string | null;
  outSrc: string | null;
  faces: number;
  vcamActive: boolean;
  vcamDevice: string | null;
  vcamError: string | null;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  goLive: () => Promise<void>;
  stopLive: () => Promise<void>;
  resetAll: () => Promise<void>;
  resetting: boolean;
}

const Ctx = createContext<PipelineState | null>(null);

export function PipelineProvider({ children }: { children: ReactNode }) {
  const media = useMedia();
  const [active, setActive] = useState(false);
  const [starting, setStarting] = useState(false);
  const [fps, setFps] = useState(0);
  const [latencyMs, setLatencyMs] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [rawSrc, setRawSrc] = useState<string | null>(null);
  const [outSrc, setOutSrc] = useState<string | null>(null);
  const [faces, setFaces] = useState(0);
  const [vcamActive, setVcamActive] = useState(false);
  const [vcamDevice, setVcamDevice] = useState<string | null>(null);
  const [vcamError, setVcamError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const closeWs = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setRawSrc(null);
    setOutSrc(null);
    setFps(0);
    setLatencyMs(0);
    setFaces(0);
  }, []);

  const applyStatus = useCallback((s: { vcam_active: boolean; vcam_device: string | null; vcam_error: string | null }) => {
    setVcamActive(s.vcam_active);
    setVcamDevice(s.vcam_device);
    setVcamError(s.vcam_error);
  }, []);

  const stop = useCallback(async () => {
    closeWs();
    setActive(false);
    setVcamActive(false);
    setVcamDevice(null);
    await api.pipelineStop().catch(() => undefined);
    media.setExternalHold(false);
  }, [closeWs, media]);

  const start = useCallback(async () => {
    setStarting(true);
    setError(null);
    try {
      // The backend needs exclusive camera access — take the hold, which stops
      // and locks out the UI's local preview, then wait for Windows to fully
      // release the device. Too short a wait = the backend opens it mid-teardown
      // and negotiates a broken format (coloured static); the backend also detects
      // and reopens on static, but giving Windows ~900ms first avoids it entirely.
      media.setExternalHold(true);
      await new Promise((r) => setTimeout(r, 900));
      const status = await api.pipelineStart();   // auto-pick the real webcam
      if (status.error) throw new Error(status.error);

      const ws = new WebSocket(WS_PREVIEW_URL);
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data as string);
        if (msg.type === "frame") {
          setRawSrc(`data:image/jpeg;base64,${msg.raw}`);
          setOutSrc(`data:image/jpeg;base64,${msg.out}`);
          setFps(msg.fps);
          setLatencyMs(msg.latency_ms);
          setFaces(msg.faces ?? 0);
          setError(null);
        } else if (msg.type === "idle" && msg.error) {
          setError(msg.error);
        }
      };
      ws.onclose = () => setActive(false);
      wsRef.current = ws;
      setActive(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      await api.pipelineStop().catch(() => undefined);
      setActive(false);
      media.setExternalHold(false);
    } finally {
      setStarting(false);
    }
  }, [media]);

  const goLive = useCallback(async () => {
    setVcamError(null);
    try {
      // Make sure the AI engine (and its preview) is up, then attach the
      // virtual camera to it.
      if (!wsRef.current) await start();
      applyStatus(await api.vcamStart());
      // Give the worker a moment to open the device, then refresh.
      await new Promise((r) => setTimeout(r, 1500));
      applyStatus(await api.pipelineStatus());
    } catch (e) {
      setVcamError(e instanceof Error ? e.message : String(e));
    }
  }, [applyStatus, start]);

  const stopLive = useCallback(async () => {
    try {
      applyStatus(await api.vcamStop());
    } catch {
      setVcamActive(false);
      setVcamDevice(null);
    }
  }, [applyStatus]);

  // Emergency stop: tear down the WebSocket, force the backend to release
  // everything (pipeline + virtual camera + physical camera), release the UI's
  // local camera too, and clear all error/running state. Recovers any stuck
  // state without the user needing to restart anything.
  const resetAll = useCallback(async () => {
    setResetting(true);
    try {
      closeWs();
      setActive(false);
      setError(null);
      setVcamError(null);
      setVcamActive(false);
      setVcamDevice(null);
      await api.reset().catch(() => undefined);
      media.setExternalHold(false);
      media.stop();
    } finally {
      setResetting(false);
    }
  }, [closeWs, media]);

  // Keep vcam status fresh while the pipeline runs (device can fail async).
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => {
      api.pipelineStatus().then(applyStatus).catch(() => undefined);
    }, 2000);
    return () => clearInterval(id);
  }, [active, applyStatus]);

  useEffect(() => () => closeWs(), [closeWs]);

  return (
    <Ctx.Provider
      value={{
        active,
        starting,
        fps,
        latencyMs,
        error,
        rawSrc,
        outSrc,
        faces,
        vcamActive,
        vcamDevice,
        vcamError,
        start,
        stop,
        goLive,
        stopLive,
        resetAll,
        resetting,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function usePipeline(): PipelineState {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePipeline must be used inside <PipelineProvider>");
  return v;
}
