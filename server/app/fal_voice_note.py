"""Server-side fal calls for Voice Notes — clone any voice from a short
reference sample, then type a message to have it spoken in that voice
(F5-TTS on fal, $0.05/1000 characters, confirmed on fal's own model page).

Not a live/real-time voice changer (that's Cloud Voice, cloud-voice/
clone_app.py, currently paused pending Modal billing) — this is
type-a-message-get-an-audio-file, meant for things like a WhatsApp voice
note: record or upload a reference voice once, type what you want said,
download the result and send it yourself.

F5-TTS returns WAV. WhatsApp DOES play a WAV file fine, but only as a
generic document attachment — its native "voice note" bubble (recorded
with the mic button) is specifically mono Opus-in-Ogg, nothing else. So
the result is re-encoded to that exact format before being handed back,
making it indistinguishable from an actually-recorded voice note when a
customer attaches it. Uses PyAV (bundles its own codec libs — no system
ffmpeg needed, works the same on Render's plain Python runtime as it does
locally) rather than shelling out to an ffmpeg binary that may not exist
in this environment.

Same fal account/key as fal_image.py (env APEXCAM_LUCY_KEY), never shipped
to the app.
"""
from __future__ import annotations

import os

MODEL = os.environ.get("APEXCAM_FAL_VOICENOTE_MODEL", "fal-ai/f5-tts")
MODEL_TYPE = os.environ.get("APEXCAM_FAL_VOICENOTE_MODEL_TYPE", "F5-TTS")
MAX_CHARS = 5000   # F5-TTS's own documented limit
OPUS_RATE = 48000  # WhatsApp's own voice notes are mono Opus/Ogg at 48kHz


def _wav_to_ogg_opus(wav_bytes: bytes) -> bytes:
    """Re-encodes WAV PCM to mono Opus-in-Ogg — WhatsApp's native voice-note
    format. IMPORTANT: out_stream.layout MUST be set explicitly before
    encoding — without it, the encoder silently defaults to stereo while a
    mono-resampled frame is fed into it, corrupting the output (verified:
    produced a file that "worked" — right byte count, played without
    erroring — but decoded to near-silence and double the real duration)."""
    import io

    import av

    in_container = av.open(io.BytesIO(wav_bytes))
    in_stream = in_container.streams.audio[0]

    out_buf = io.BytesIO()
    out_container = av.open(out_buf, mode="w", format="ogg")
    out_stream = out_container.add_stream("libopus", rate=OPUS_RATE)
    out_stream.layout = "mono"

    resampler = av.AudioResampler(format="s16", layout="mono", rate=OPUS_RATE)
    for frame in in_container.decode(in_stream):
        for rframe in resampler.resample(frame):
            for packet in out_stream.encode(rframe):
                out_container.mux(packet)
    for packet in out_stream.encode(None):   # flush
        out_container.mux(packet)
    out_container.close()
    return out_buf.getvalue()


def _key() -> str:
    k = (os.environ.get("APEXCAM_LUCY_KEY") or "").strip()
    if not k:
        raise RuntimeError("APEXCAM_LUCY_KEY not set on the server")
    return k


def generate_voice_note(reference_bytes: bytes, text: str) -> bytes:
    """Clones the voice in `reference_bytes` and speaks `text` in it.
    Synchronous; raises with the real reason on failure."""
    import fal_client
    import requests

    text = text.strip()
    if not text:
        raise RuntimeError("Type something for the voice to say.")
    if len(text) > MAX_CHARS:
        raise RuntimeError(f"That message is too long — max {MAX_CHARS} characters.")

    client = fal_client.SyncClient(key=_key())
    ref_url = client.upload(reference_bytes, "audio/wav")

    result = client.subscribe(MODEL, arguments={
        "gen_text": text,
        "ref_audio_url": ref_url,
        "model_type": MODEL_TYPE,
        "remove_silence": True,
    })
    audio = result.get("audio_url") or result.get("audio") or {}
    url = audio.get("url") if isinstance(audio, dict) else None
    if not url:
        raise RuntimeError(f"F5-TTS returned no audio: {result}")
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"could not fetch generated audio: {r.status_code}")
    return _wav_to_ogg_opus(r.content)
