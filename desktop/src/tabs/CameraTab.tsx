import { useEffect, useState } from "react";
import { api } from "../api/client";
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

  useEffect(() => {
    api.getEnhance().then((e) => setSharpen(e.sharpen ?? 0)).catch(() => undefined);
  }, []);

  // Sliders drive both the local CSS preview and the backend pipeline.
  const applyEnhance = (e: Enhance) => {
    setEnhance(e);
    api.setEnhance({ ...e, sharpen }).catch(() => undefined);
  };

  const changeSharpen = (v: number) => {
    setSharpen(v);
    api.setEnhance({ ...enhance, sharpen: v }).catch(() => undefined);
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
        <h3>Picture quality (applies to EMY CAM output)</h3>
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
          These adjustments show on the output pane and are burned into recordings. True
          GPU sharpening/denoising (and background blur / green screen) arrive with the AI
          pipeline and virtual camera phases.
        </p>
      </section>
    </>
  );
}
