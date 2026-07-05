import { useEffect, useRef, useState } from "react";
import { api, type BackgroundSettings } from "../api/client";
import { AiEngineControl } from "../components/AiEngineControl";
import { DualPreview } from "../components/DualPreview";
import {
  DEFAULT_ENHANCE,
  RESOLUTIONS,
  useMedia,
  type Enhance,
} from "../context/MediaContext";

export function CameraTab() {
  const {
    cameras,
    cameraId,
    setCameraId,
    resolution,
    setResolution,
    running,
    start,
    stop,
    enhance,
    setEnhance,
    externalHold,
  } = useMedia();
  const [sharpen, setSharpen] = useState(0);
  const [beauty, setBeauty] = useState(0);
  const [bg, setBg] = useState<BackgroundSettings | null>(null);
  const bgFileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getEnhance().then((e) => {
      setSharpen(e.sharpen ?? 0);
      setBeauty(e.beautify ?? 0);
    }).catch(() => undefined);
    api.getBackground().then(setBg).catch(() => setBg(null));
  }, []);

  const changeBeauty = (v: number) => {
    setBeauty(v);
    api.setEnhance({ ...enhance, sharpen, beautify: v }).catch(() => undefined);
  };

  const updateBg = (patch: Partial<BackgroundSettings>) => {
    if (!bg) return;
    const next = { ...bg, ...patch };
    setBg(next);
    api.setBackground(next).then(setBg).catch(() => undefined);
  };

  const uploadBg = (f: File | undefined) => {
    if (!f) return;
    api.uploadBackground(f).then((s) => setBg({ ...s, mode: "image" })).catch(() => undefined);
    api.setBackground({ ...(bg as BackgroundSettings), mode: "image" }).catch(() => undefined);
  };

  // Sliders drive both the local CSS preview and the backend pipeline.
  const applyEnhance = (e: Enhance) => {
    setEnhance(e);
    api.setEnhance({ ...e, sharpen, beautify: beauty }).catch(() => undefined);
  };

  const changeSharpen = (v: number) => {
    setSharpen(v);
    api.setEnhance({ ...enhance, sharpen: v, beautify: beauty }).catch(() => undefined);
  };

  return (
    <>
      <section className="preview">
        <DualPreview />
      </section>

      <AiEngineControl />

      <section className="panel">
        <h3>Camera input</h3>
        {externalHold && (
          <p className="muted">
            The AI engine is using the camera. The panes above already show your raw input and
            the AI output — stop the AI engine to use the plain camera preview.
          </p>
        )}
        <label className="row">
          Device
          <select
            value={cameraId}
            disabled={externalHold}
            onChange={(e) => setCameraId(e.target.value)}
          >
            <option value="">Default camera</option>
            {cameras.map((c) => (
              <option key={c.deviceId} value={c.deviceId}>
                {c.label || `Camera ${c.deviceId.slice(0, 6)}`}
              </option>
            ))}
          </select>
        </label>
        <label className="row">
          Resolution
          <select
            value={resolution.label}
            disabled={externalHold}
            onChange={(e) =>
              setResolution(RESOLUTIONS.find((r) => r.label === e.target.value) ?? RESOLUTIONS[0])
            }
          >
            {RESOLUTIONS.map((r) => (
              <option key={r.label}>{r.label}</option>
            ))}
          </select>
        </label>
        <div className="row">
          {running ? (
            <button type="button" className="btn" onClick={stop}>
              Stop camera
            </button>
          ) : (
            <button type="button" className="btn primary" onClick={start} disabled={externalHold}>
              Start camera
            </button>
          )}
        </div>
      </section>

      <section className="panel">
        <h3>Picture quality (applies to Apex Cam output)</h3>
        <label className="row">
          ✨ Studio Beautify ({Math.round(beauty * 100)}%)
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={beauty}
            onChange={(e) => changeBeauty(Number(e.target.value))}
          />
        </label>
        <p className="muted">
          Cinematic HD look — cleans up a cheap/grainy webcam: balanced exposure, smooth
          natural skin (keeps eyes &amp; detail sharp), warm tone and crisp sharpening. Makes
          everyone look polished and authentic. Real-time on CPU.
        </p>
        <label className="row">
          Brightness ({enhance.brightness.toFixed(2)})
          <input
            type="range"
            min={0.5}
            max={1.8}
            step={0.02}
            value={enhance.brightness}
            onChange={(e) => applyEnhance({ ...enhance, brightness: Number(e.target.value) })}
          />
        </label>
        <label className="row">
          Contrast / sharpness ({enhance.contrast.toFixed(2)})
          <input
            type="range"
            min={0.5}
            max={1.8}
            step={0.02}
            value={enhance.contrast}
            onChange={(e) => applyEnhance({ ...enhance, contrast: Number(e.target.value) })}
          />
        </label>
        <label className="row">
          Color ({enhance.saturation.toFixed(2)})
          <input
            type="range"
            min={0}
            max={2}
            step={0.02}
            value={enhance.saturation}
            onChange={(e) => applyEnhance({ ...enhance, saturation: Number(e.target.value) })}
          />
        </label>
        <label className="row">
          Sharpen — face detail ({Math.round(sharpen * 100)}%)
          <input
            type="range"
            min={0}
            max={1.5}
            step={0.05}
            value={sharpen}
            onChange={(e) => changeSharpen(Number(e.target.value))}
          />
        </label>
        <p className="muted">
          Sharpen crisps the AI output on any machine. For a GPU-grade face restorer
          (GFPGAN), see docs/GPU_SETUP.md.
        </p>
        <div className="row">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setSharpen(0);
              setEnhance(DEFAULT_ENHANCE);
              api.setEnhance({ ...DEFAULT_ENHANCE, sharpen: 0 }).catch(() => undefined);
            }}
          >
            Reset to recommended
          </button>
        </div>
        <p className="muted">
          These adjustments show on the Apex Cam output and are burned into recordings.
        </p>
      </section>

      {bg && (
        <section className="panel">
          <h3>Background</h3>
          {!bg.available && (
            <p className="muted">
              Background model not installed. Run scripts/download_models.py (rvm) — it's a
              15 MB model and runs real-time on CPU.
            </p>
          )}
          <div className="row">
            {(["off", "blur", "color", "image"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={bg.mode === m ? "btn primary" : "btn"}
                disabled={!bg.available}
                onClick={() => (m === "image" ? bgFileRef.current?.click() : updateBg({ mode: m }))}
              >
                {m === "off" ? "Off" : m === "blur" ? "Blur" : m === "color" ? "Green screen" : "Image"}
              </button>
            ))}
            <input
              ref={bgFileRef}
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => uploadBg(e.target.files?.[0])}
            />
          </div>
          {bg.mode === "blur" && (
            <label className="row">
              Blur amount ({bg.blur_strength})
              <input
                type="range"
                min={5}
                max={99}
                step={2}
                value={bg.blur_strength}
                onChange={(e) => updateBg({ blur_strength: Number(e.target.value) })}
              />
            </label>
          )}
          {bg.mode === "color" && (
            <label className="row">
              Colour
              <input
                type="color"
                value={bg.color}
                onChange={(e) => updateBg({ color: e.target.value })}
              />
            </label>
          )}
          <p className="muted">
            Real-time background blur, green-screen, or photo replacement — your whole body
            stays, the background changes. Runs on CPU (~19 fps) or GPU. Start the AI engine to
            see it.
          </p>
        </section>
      )}
    </>
  );
}
