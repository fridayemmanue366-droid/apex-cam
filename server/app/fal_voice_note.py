"""Server-side fal calls for Voice Notes — clone any voice from a short
reference sample, then type a message to have it spoken in that voice.

Switched from F5-TTS to ElevenLabs on 2026-09-18 (owner decision: better
quality, ~29 languages vs F5-TTS's ~9, still on the SAME fal account/key —
no new billing). Uses fal-ai/elevenlabs/text-to-voice/design/eleven-v3,
which accepts a reference audio_url directly (ElevenLabs' own "Voice
Design" product, which blends a text `prompt` with the reference audio —
NOT the same thing as ElevenLabs' dedicated Instant Voice Cloning (IVC)
flow, which fal does not appear to expose as its own endpoint). We push
`prompt_strength` low so the reference audio dominates and the required
`prompt` field acts as filler, to approximate a straight clone as closely
as this endpoint allows.

IMPORTANT — UNVERIFIED, no fal credit available to live-test as of
2026-09-18 (see pricing.py comment near VOICENOTE_COST_USD_PER_1K_CHARS):
  - The exact response shape (single final audio vs a voice_id + preview)
    isn't confirmed from documentation alone. generate_voice_note() below
    handles both: uses direct audio if present, else falls back to a
    second TTS call (fal-ai/elevenlabs/tts/turbo-v2.5) using whatever
    voice id the design call returned.
  - Whether each call PERSISTS a new saved voice on the ElevenLabs account
    (it has a `voice_name` field, which implies it might) is not confirmed.
    If so, repeated Voice Note use could accumulate saved voices toward
    an ElevenLabs plan limit over time -- worth checking the ElevenLabs
    dashboard's voice list after the first few real generations.
  - The real cost of this flow (design call, possibly + a second TTS
    call) hasn't been confirmed against pricing.py's existing per-block
    credit price, which was set for F5-TTS's flat $0.05/1000-char rate.
FIRST THING TO DO once real fal credit is available: run one real Voice
Note generation and check (a) it actually sounds like the reference voice,
(b) how many ElevenLabs "voices" show up in the dashboard afterward, (c)
the real fal cost of that one call, against what pricing.py currently
charges the customer for it.

Not a live/real-time voice changer (that's Cloud Voice, cloud-voice/
clone_app.py, currently paused pending Modal billing) — this is
type-a-message-get-an-audio-file, meant for things like a WhatsApp voice
note: record or upload a reference voice once, type what you want said,
download the result and send it yourself.

ElevenLabs returns MP3 by default. WhatsApp DOES play that fine, but only
as a generic document attachment — its native "voice note" bubble
(recorded with the mic button) is specifically mono Opus-in-Ogg, nothing
else. So the result is re-encoded to that exact format before being handed
back, making it indistinguishable from an actually-recorded voice note
when a customer attaches it. Uses PyAV (bundles its own codec libs,
decodes MP3/WAV/anything the same way — no system ffmpeg needed, works
the same on Render's plain Python runtime as it does locally) rather than
shelling out to an ffmpeg binary that may not exist in this environment.

Same fal account/key as fal_image.py (env APEXCAM_LUCY_KEY), never shipped
to the app.
"""
from __future__ import annotations

import os

DESIGN_MODEL = os.environ.get("APEXCAM_FAL_VOICENOTE_MODEL", "fal-ai/elevenlabs/text-to-voice/design/eleven-v3")
TTS_MODEL = os.environ.get("APEXCAM_FAL_VOICENOTE_TTS_MODEL", "fal-ai/elevenlabs/tts/turbo-v2.5")
# How much the reference audio dominates over the (required, but here just
# filler) text prompt -- LOW so the clone leans as close to the reference
# voice as this endpoint allows. Per fal's docs: 0 = almost no prompt
# influence (reference audio dominates), 1 = almost no reference-audio
# influence. Tune this first if a real test comes out sounding too
# "generic" rather than like the reference voice.
PROMPT_STRENGTH = float(os.environ.get("APEXCAM_FAL_VOICENOTE_PROMPT_STRENGTH", "0.2"))
VOICE_PROMPT = "A natural, clear human voice, speaking normally."
MAX_CHARS = 5000   # carried over from F5-TTS's limit -- not yet confirmed against ElevenLabs' own
OPUS_RATE = 48000  # WhatsApp's own voice notes are mono Opus/Ogg at 48kHz

# Loudness normalization — a plain volume (gain) adjustment only, applied to
# the whole clip uniformly. It does NOT touch pitch, timbre, or formants, so
# it cannot change how the cloned voice actually sounds — it only makes
# different generations land at a consistent, healthy volume instead of
# some coming out quiet and others near-clipping. Matters more than it might
# seem here specifically because a customer's real use case (attach to
# WhatsApp, or play-and-re-record through a second phone's mic) puts this
# audio through a lossy real-world path where a weak signal degrades hard.
TARGET_RMS = 0.2     # a healthy, consistent loudness to aim for
PEAK_CEILING = 0.95  # hard ceiling so gain can never introduce clipping


def _wav_to_ogg_opus(wav_bytes: bytes) -> bytes:
    """Normalizes loudness, then re-encodes to mono Opus-in-Ogg — WhatsApp's
    native voice-note format. IMPORTANT: out_stream.layout MUST be set
    explicitly before encoding — without it, the encoder silently defaults
    to stereo while a mono-resampled frame is fed into it, corrupting the
    output (verified: produced a file that "worked" — right byte count,
    played without erroring — but decoded to near-silence and double the
    real duration)."""
    import io

    import av
    import numpy as np

    in_container = av.open(io.BytesIO(wav_bytes))
    in_stream = in_container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=OPUS_RATE)
    chunks = []
    for frame in in_container.decode(in_stream):
        for rframe in resampler.resample(frame):
            chunks.append(rframe.to_ndarray())
    if not chunks:
        raise RuntimeError("generated audio was empty")
    samples = np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0

    rms = float(np.sqrt(np.mean(samples ** 2)))
    if rms > 1e-6:
        gain = TARGET_RMS / rms
        peak = float(np.max(np.abs(samples)))
        if peak * gain > PEAK_CEILING:
            gain = PEAK_CEILING / peak   # never clip, even if that means missing the RMS target
        samples = samples * gain
    pcm16 = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)

    out_buf = io.BytesIO()
    out_container = av.open(out_buf, mode="w", format="ogg")
    out_stream = out_container.add_stream("libopus", rate=OPUS_RATE)
    out_stream.layout = "mono"

    frame = av.AudioFrame.from_ndarray(pcm16.reshape(1, -1), format="s16", layout="mono")
    frame.rate = OPUS_RATE
    frame.pts = 0
    for packet in out_stream.encode(frame):
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


def _find_audio_url(d: dict) -> str | None:
    """Best-effort extraction of a playable audio URL from an ElevenLabs-via-fal
    response -- the exact shape isn't confirmed (see module docstring), so this
    checks every plausible field/nesting rather than assuming one."""
    audio = d.get("audio")
    if isinstance(audio, dict) and audio.get("url"):
        return audio["url"]
    if isinstance(d.get("audio_url"), str):
        return d["audio_url"]
    previews = d.get("previews")
    if isinstance(previews, list) and previews:
        first = previews[0]
        if isinstance(first, dict):
            a = first.get("audio")
            if isinstance(a, dict) and a.get("url"):
                return a["url"]
            if isinstance(first.get("audio_url"), str):
                return first["audio_url"]
    return None


def _find_audio_b64(d: dict) -> str | None:
    """Same as _find_audio_url but for an inline base64 field -- ElevenLabs'
    own native API returns voice-design previews this way (audio_base_64),
    so fal's wrapper may too."""
    if isinstance(d.get("audio_base_64"), str):
        return d["audio_base_64"]
    previews = d.get("previews")
    if isinstance(previews, list) and previews:
        first = previews[0]
        if isinstance(first, dict) and isinstance(first.get("audio_base_64"), str):
            return first["audio_base_64"]
    return None


def _find_voice_id(d: dict) -> str | None:
    for key in ("generated_voice_id", "voice_id"):
        if isinstance(d.get(key), str):
            return d[key]
    previews = d.get("previews")
    if isinstance(previews, list) and previews:
        first = previews[0]
        if isinstance(first, dict) and isinstance(first.get("generated_voice_id"), str):
            return first["generated_voice_id"]
    return None


def generate_voice_note(reference_bytes: bytes, text: str) -> bytes:
    """Clones the voice in `reference_bytes` and speaks `text` in it, via
    ElevenLabs' Voice Design endpoint on fal. Synchronous; raises with the
    real reason on failure.

    Two-step fallback because the exact response shape isn't confirmed
    without a live test (see module docstring): if the design call itself
    returns finished audio, use it directly; otherwise fall back to a
    second TTS call using whatever voice id it handed back."""
    import base64

    import fal_client
    import requests

    text = text.strip()
    if not text:
        raise RuntimeError("Type something for the voice to say.")
    if len(text) > MAX_CHARS:
        raise RuntimeError(f"That message is too long — max {MAX_CHARS} characters.")

    client = fal_client.SyncClient(key=_key())
    ref_url = client.upload(reference_bytes, "audio/wav")

    result = client.subscribe(DESIGN_MODEL, arguments={
        "prompt": VOICE_PROMPT,
        "audio_url": ref_url,
        "prompt_strength": PROMPT_STRENGTH,
        "text": text,
        "auto_generate_text": False,
    })

    audio_bytes: bytes | None = None
    url = _find_audio_url(result)
    if url:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            audio_bytes = r.content
    if audio_bytes is None:
        b64 = _find_audio_b64(result)
        if b64:
            audio_bytes = base64.b64decode(b64)

    if audio_bytes is None:
        # The design call didn't hand back finished audio directly -- fall
        # back to an explicit TTS call using the voice id it created.
        voice_id = _find_voice_id(result)
        if not voice_id:
            raise RuntimeError(f"unexpected response from voice design: {result}")
        tts_result = client.subscribe(TTS_MODEL, arguments={
            "text": text,
            "voice": voice_id,
        })
        url = _find_audio_url(tts_result)
        if url:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                audio_bytes = r.content
        if audio_bytes is None:
            b64 = _find_audio_b64(tts_result)
            if b64:
                audio_bytes = base64.b64decode(b64)
        if audio_bytes is None:
            raise RuntimeError(f"unexpected response from tts: {tts_result}")

    return _wav_to_ogg_opus(audio_bytes)
