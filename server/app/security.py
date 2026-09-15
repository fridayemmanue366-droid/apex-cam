"""Auth primitives — password hashing + signed session tokens, pure stdlib.

Passwords: PBKDF2-HMAC-SHA256 with a per-password salt (no external bcrypt dep).
Tokens: a compact HMAC-signed token (like a mini-JWT) — the server signs
{user_id, exp} with APEXCAM_SECRET; tampering or expiry is rejected. Set a strong
APEXCAM_SECRET in production.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

SECRET = os.environ.get("APEXCAM_SECRET", "dev-insecure-change-me").encode()
TOKEN_TTL = int(os.environ.get("APEXCAM_TOKEN_TTL", str(60 * 60 * 24 * 30)))  # 30 days

# A SEPARATE signing secret for short-lived, narrowly-scoped tokens handed to
# third-party services (e.g. the cloud-voice Modal app) — deliberately NOT
# the same secret as the 30-day account session token above. If a scoped
# token ever leaked (logged by a provider, etc.) the blast radius is "5
# minutes of one feature" instead of "this customer's whole account".
VOICE_SECRET = os.environ.get("APEXCAM_VOICE_SECRET", "dev-insecure-change-me-voice").encode()


# --- passwords -----------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2$200000${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, dk_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


# --- tokens --------------------------------------------------------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id: int) -> str:
    payload = _b64(json.dumps({"uid": user_id, "exp": int(time.time()) + TOKEN_TTL}).encode())
    sig = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str) -> int | None:
    """Return the user id if the token is valid and unexpired, else None."""
    try:
        payload, sig = token.split(".")
        expected = _b64(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        if data.get("exp", 0) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None


def make_voice_token(user_id: int, ttl: int = 300) -> str:
    """Short-lived token (default 5 min) scoped to the cloud-voice Modal
    service, signed with VOICE_SECRET — NOT the main session token. The
    Modal app verifies this independently (same secret, shared via a Modal
    Secret) without ever calling back to this server, so there's no added
    latency on the hot path."""
    payload = _b64(json.dumps({"uid": user_id, "scope": "cloud_voice",
                               "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(VOICE_SECRET, payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"
