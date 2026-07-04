import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

// One shared capture stream for the whole app. Windows webcams usually can't be
// opened by two consumers at once, so every tab that needs live video/audio
// reads from this context instead of calling getUserMedia itself.

export interface Resolution {
  label: string;
  width: number;
  height: number;
}

export const RESOLUTIONS: Resolution[] = [
  { label: "720p (1280×720)", width: 1280, height: 720 },
  { label: "1080p (1920×1080)", width: 1920, height: 1080 },
];

// Video enhancement applied to the AI-output preview and burned into
// recordings. Defaults are slightly brightened/sharpened for a crisper image;
// real GPU-side enhancement (unsharp mask, denoise) comes with the AI pipeline.
export interface Enhance {
  brightness: number; // 1 = neutral
  contrast: number;
  saturation: number;
}

export const DEFAULT_ENHANCE: Enhance = { brightness: 1.12, contrast: 1.1, saturation: 1.08 };

export const enhanceFilter = (e: Enhance) =>
  `brightness(${e.brightness}) contrast(${e.contrast}) saturate(${e.saturation})`;

export interface PendingConfirm {
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
}

interface MediaState {
  cameras: MediaDeviceInfo[];
  mics: MediaDeviceInfo[];
  cameraId: string;
  micId: string;
  resolution: Resolution;
  stream: MediaStream | null;
  error: string | null;
  running: boolean;
  enhance: Enhance;
  setEnhance: (e: Enhance) => void;
  setCameraId: (id: string) => void;
  setMicId: (id: string) => void;
  setResolution: (r: Resolution) => void;
  start: () => void;
  stop: () => void;
  pendingConfirm: PendingConfirm | null;
  resolvePendingConfirm: (ok: boolean) => void;
  // True while the backend AI engine owns the physical camera. The local
  // preview must not touch the camera during this — they'd fight over the one
  // device. Set by PipelineContext.
  externalHold: boolean;
  setExternalHold: (held: boolean) => void;
}

const Ctx = createContext<MediaState | null>(null);

export function MediaProvider({ children }: { children: ReactNode }) {
  const [cameras, setCameras] = useState<MediaDeviceInfo[]>([]);
  const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
  const [cameraId, setCameraIdState] = useState("");
  const [micId, setMicIdState] = useState("");
  const [resolution, setResolutionState] = useState<Resolution>(RESOLUTIONS[0]);
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [enhance, setEnhance] = useState<Enhance>(DEFAULT_ENHANCE);
  const [externalHold, setExternalHoldState] = useState(false);
  const streamRef = useRef<MediaStream | null>(null);
  const externalHoldRef = useRef(false);

  const refreshDevices = useCallback(async () => {
    const devices = await navigator.mediaDevices.enumerateDevices();
    setCameras(devices.filter((d) => d.kind === "videoinput"));
    setMics(devices.filter((d) => d.kind === "audioinput"));
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setStream(null);
    setRunning(false);
  }, []);

  const start = useCallback(() => {
    if (externalHoldRef.current) {
      setError("The AI engine is using the camera. Stop the AI engine to use the plain preview.");
      return;
    }
    setError(null);
    setRunning(true);
  }, []);

  // When the AI engine takes the camera, immediately release the local preview
  // so the backend can open the device; when it lets go, just clear the flag.
  const setExternalHold = useCallback((held: boolean) => {
    externalHoldRef.current = held;
    setExternalHoldState(held);
    if (held) {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setStream(null);
      setRunning(false);
      setError(null);
    }
  }, []);

  // Changing the camera/mic/resolution while live means tearing down and
  // reopening the device. When something is running, ask first (the user
  // requested this) instead of silently restarting — which could also error
  // with "device in use" mid-switch. When nothing is running, apply directly.
  const guardedChange = useCallback(
    (apply: () => void, what: string) => {
      if (!streamRef.current) {
        apply();
        return;
      }
      setPendingConfirm({
        message: `Changing the ${what} needs to restart the camera. Stop the current camera and continue?`,
        confirmLabel: "Stop & continue",
        onConfirm: apply,
      });
    },
    [],
  );

  const setCameraId = useCallback(
    (id: string) => guardedChange(() => setCameraIdState(id), "camera"),
    [guardedChange],
  );
  const setMicId = useCallback(
    (id: string) => guardedChange(() => setMicIdState(id), "microphone"),
    [guardedChange],
  );
  const setResolution = useCallback(
    (r: Resolution) => guardedChange(() => setResolutionState(r), "resolution"),
    [guardedChange],
  );

  const resolvePendingConfirm = useCallback((ok: boolean) => {
    setPendingConfirm((current) => {
      if (ok) current?.onConfirm();
      return null;
    });
  }, []);

  // (Re)open the stream whenever it should be running and a selection changes.
  useEffect(() => {
    if (!running || externalHold) return;
    let cancelled = false;

    const constraints: MediaStreamConstraints = {
      video: {
        deviceId: cameraId ? { exact: cameraId } : undefined,
        width: { ideal: resolution.width },
        height: { ideal: resolution.height },
        frameRate: { ideal: 30 },
      },
      audio: micId ? { deviceId: { exact: micId } } : true,
    };

    (async () => {
      // Release the current device first, then give Windows a moment to fully
      // free it before reopening — otherwise the reopen can hit "device in use".
      const hadStream = !!streamRef.current;
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      if (hadStream) await new Promise((r) => setTimeout(r, 250));
      if (cancelled) return;

      const open = () => navigator.mediaDevices.getUserMedia(constraints);
      try {
        let s: MediaStream;
        try {
          s = await open();
        } catch (err) {
          // One retry after a longer wait handles the common release race.
          if ((err as DOMException).name === "NotReadableError") {
            await new Promise((r) => setTimeout(r, 600));
            if (cancelled) return;
            s = await open();
          } else {
            throw err;
          }
        }
        if (cancelled) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = s;
        setStream(s);
        setError(null);
        await refreshDevices(); // labels become visible after permission
      } catch (e) {
        if (!cancelled) {
          const err = e as DOMException;
          const friendly =
            err.name === "NotReadableError"
              ? "Camera is in use by another app (or the AI engine). Close it and try again."
              : err.message || String(e);
          setError(friendly);
          setStream(null);
          setRunning(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [running, externalHold, cameraId, micId, resolution, refreshDevices]);

  useEffect(() => {
    refreshDevices();
    return () => streamRef.current?.getTracks().forEach((t) => t.stop());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Ctx.Provider
      value={{
        cameras,
        mics,
        cameraId,
        micId,
        resolution,
        stream,
        error,
        running,
        enhance,
        setEnhance,
        setCameraId,
        setMicId,
        setResolution,
        start,
        stop,
        pendingConfirm,
        resolvePendingConfirm,
        externalHold,
        setExternalHold,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useMedia(): MediaState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useMedia must be used inside <MediaProvider>");
  return v;
}
