// Recording + encoding helpers for Cloud Voice's "clone my voice from a
// sample" flow (AudioTab.tsx). The local backend's /audio/cloud/start
// expects the reference clip as a 16-bit mono PCM WAV at TARGET_SR — using
// that one simple, universal format means the Python side can decode it
// with the standard library alone (no extra audio-decoding dependency).

export const TARGET_SR = 48000;          // matches audio_pipeline.py's SAMPLE_RATE
export const MIN_REFERENCE_S = 10;        // shorter clips clone poorly (per Seed-VC's own guidance)
export const MAX_REFERENCE_S = 120;       // 2 minutes — what we ask customers for, and the cap the engine enforces

export class MicRecorder {
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private stream: MediaStream | null = null;

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    this.chunks = [];
    this.recorder = new MediaRecorder(this.stream);
    this.recorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.chunks.push(e.data);
    };
    this.recorder.start();
  }

  /** Stops recording and returns the raw captured audio (browser-native
   * container, e.g. webm/opus) — decode it with decodeToMonoPCM() below. */
  stop(): Promise<Blob> {
    return new Promise((resolve) => {
      if (!this.recorder) {
        resolve(new Blob());
        return;
      }
      this.recorder.onstop = () => {
        const blob = new Blob(this.chunks, { type: this.recorder?.mimeType || "audio/webm" });
        this.stream?.getTracks().forEach((t) => t.stop());
        this.stream = null;
        resolve(blob);
      };
      this.recorder.stop();
    });
  }

  cancel(): void {
    try {
      this.recorder?.stop();
    } catch { /* already stopped */ }
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }
}

/** Decodes any browser-playable audio (recorded webm/opus, or an uploaded
 * mp3/wav/m4a/etc file) into mono float32 PCM at TARGET_SR — using an
 * OfflineAudioContext to do the resample/downmix in one pass. */
export async function decodeToMonoPCM(blob: Blob): Promise<Float32Array> {
  const arrayBuffer = await blob.arrayBuffer();
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const probe = new AC();
  let decoded: AudioBuffer;
  try {
    decoded = await probe.decodeAudioData(arrayBuffer.slice(0));
  } finally {
    await probe.close();
  }
  const clippedSeconds = Math.min(decoded.duration, MAX_REFERENCE_S);
  const targetLength = Math.max(1, Math.round(clippedSeconds * TARGET_SR));
  const offline = new OfflineAudioContext(1, targetLength, TARGET_SR);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start(0, 0, clippedSeconds);
  const rendered = await offline.startRendering();
  return rendered.getChannelData(0).slice();   // copy out of the AudioBuffer's internal buffer
}

/** Encodes mono float32 PCM into a 16-bit PCM WAV Blob — the exact shape
 * backend/app/api/routes/audio.py's _decode_reference_wav expects. */
export function encodeWav(samples: Float32Array, sampleRate = TARGET_SR): Blob {
  const bytesPerSample = 2;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);

  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };

  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);           // fmt chunk size
  view.setUint16(20, 1, true);            // PCM
  view.setUint16(22, 1, true);            // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);   // byte rate
  view.setUint16(32, bytesPerSample, true);                 // block align
  view.setUint16(34, 16, true);           // bits per sample
  writeStr(36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

/** RMS level, 0..1 — for a quick "does this clip actually have sound in it"
 * sanity check before uploading (catches a muted mic / silent file early). */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}
