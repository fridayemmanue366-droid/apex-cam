import { useEffect, useRef, useState } from "react";
import { useMedia } from "../context/MediaContext";

// Simple RMS level meter for the shared stream's audio track (WebAudio analyser).
export function MicMeter() {
  const { stream } = useMedia();
  const [level, setLevel] = useState(0);
  const raf = useRef(0);

  useEffect(() => {
    if (!stream || stream.getAudioTracks().length === 0) {
      setLevel(0);
      return;
    }
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);

    const tick = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3));
      raf.current = requestAnimationFrame(tick);
    };
    tick();

    return () => {
      cancelAnimationFrame(raf.current);
      source.disconnect();
      ctx.close();
    };
  }, [stream]);

  return (
    <div className="meter" title="Microphone level">
      <div className="meter-fill" style={{ width: `${Math.round(level * 100)}%` }} />
    </div>
  );
}
