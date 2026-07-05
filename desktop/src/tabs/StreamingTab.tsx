import { useEffect, useState } from "react";
import { api, type AudioStatus } from "../api/client";
import { usePipeline } from "../context/PipelineContext";

export function StreamingTab() {
  const { vcamActive, vcamDevice, vcamError, goLive, stopLive } = usePipeline();
  const [audio, setAudio] = useState<AudioStatus | null>(null);

  useEffect(() => {
    const id = setInterval(() => {
      api.audioStatus().then(setAudio).catch(() => setAudio(null));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <>
      <section className="panel">
        <h3>Virtual devices</h3>
        <div className="kv">
          <span>Virtual camera</span>
          <span>
            {vcamActive ? (
              <span className="pill ok">LIVE — “{vcamDevice}”</span>
            ) : (
              <span className="pill">Off</span>
            )}
          </span>
          <span>Virtual microphone</span>
          <span>
            {audio?.running ? (
              <span className="pill ok">LIVE — “{audio.output_name}”</span>
            ) : (
              <span className="pill">Off — start it on the Audio tab</span>
            )}
          </span>
        </div>
        <div className="row">
          {vcamActive ? (
            <button type="button" className="btn danger" onClick={() => void stopLive()}>
              Stop virtual camera
            </button>
          ) : (
            <button type="button" className="btn primary" onClick={() => void goLive()}>
              📡 Go Live
            </button>
          )}
        </div>
        {vcamError && <p className="error">{vcamError}</p>}
        <p className="muted">
          Going live starts the AI engine (if needed) and publishes its output as a standard
          system camera. On Windows this uses the OBS Virtual Camera driver — if you see a
          driver error above, install OBS Studio (free) from obsproject.com once, then retry.
        </p>
        <p className="muted">
          <strong>Privacy by design:</strong> the virtual devices carry only the AI-processed
          Apex Cam output (with its AI-GENERATED label). Your raw camera feed is never exposed
          to WhatsApp, Telegram, Zoom, or any other app.
        </p>
      </section>

      <section className="panel">
        <h3>Selecting Apex Cam in your apps</h3>
        <ul className="list">
          <li><strong>Zoom</strong> — Settings → Video → Camera → “OBS Virtual Camera”.</li>
          <li><strong>Discord</strong> — User Settings → Voice &amp; Video → Camera.</li>
          <li><strong>Google Meet</strong> — gear icon → Video → Camera.</li>
          <li><strong>Microsoft Teams</strong> — Settings → Devices → Camera.</li>
          <li><strong>WhatsApp Desktop</strong> — Settings → Video &amp; audio → Camera.</li>
          <li><strong>Telegram Desktop</strong> — Settings → Calls → Camera.</li>
          <li><strong>OBS Studio</strong> — add a Video Capture Device source.</li>
        </ul>
        <p className="muted">
          For the processed voice, also set the app's <strong>microphone</strong> to
          “CABLE Output (VB-Audio Virtual Cable)” after starting the virtual microphone on the
          Audio tab.
        </p>
        <p className="muted">
          While Apex Cam is live, pick the virtual camera device in the app's camera list. The
          device is provided through standard OS camera interfaces only — Apex Cam never hooks
          into or reverse-engineers specific apps.
        </p>
      </section>
    </>
  );
}
