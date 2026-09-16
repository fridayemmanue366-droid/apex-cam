"""Server-side fal calls for Voice Notes — clone any voice from a short
reference sample, then type a message to have it spoken in that voice
(F5-TTS on fal, $0.05/1000 characters, confirmed on fal's own model page).

Not a live/real-time voice changer (that's Cloud Voice, cloud-voice/
clone_app.py, currently paused pending Modal billing) — this is
type-a-message-get-an-audio-file, meant for things like a WhatsApp voice
note: record or upload a reference voice once, type what you want said,
download the result and send it yourself.

Same fal account/key as fal_image.py (env APEXCAM_LUCY_KEY), never shipped
to the app.
"""
from __future__ import annotations

import os

MODEL = os.environ.get("APEXCAM_FAL_VOICENOTE_MODEL", "fal-ai/f5-tts")
MODEL_TYPE = os.environ.get("APEXCAM_FAL_VOICENOTE_MODEL_TYPE", "F5-TTS")
MAX_CHARS = 5000   # F5-TTS's own documented limit


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
    return r.content
