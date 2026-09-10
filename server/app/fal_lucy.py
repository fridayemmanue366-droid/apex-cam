"""Server-side fal calls for Apex Pro's realtime Lucy engine — the fal API key
lives HERE (env APEXCAM_LUCY_KEY), never in the shipped app.

Mirrors decart.py's pattern but simpler: fal's realtime protocol needs only a
short-lived JWT, not a held-open server-side session. We mint the JWT with our
key and hand it to the customer's app, which then talks to fal DIRECTLY — media
and prompt updates never pass through this server, only the initial mint (and
periodic billing heartbeats — see routes/studio.py's /live/tick).
"""
from __future__ import annotations

import json
import os
import urllib.request

FAL_KEY = os.environ.get("APEXCAM_LUCY_KEY", "")
TOKENS_URL = os.environ.get("APEXCAM_FAL_TOKENS_URL", "https://rest.fal.ai/tokens/")
# Keep in sync with backend/app/engines/fal_pro.py's default.
LIVE_MODEL = os.environ.get("APEXCAM_LUCY_MODEL", "decart/lucy-2-5")


def configured() -> bool:
    return bool(FAL_KEY)


def _key() -> str:
    if not FAL_KEY:
        raise RuntimeError("APEXCAM_LUCY_KEY not set on the server")
    return FAL_KEY


def mint_token(seconds: int = 300) -> str:
    """Mint a short-lived realtime JWT scoped to our model via allowed_apps. Safe
    to hand to the customer's app: expires quickly and can't be used for anything
    but streaming to this one model."""
    body = json.dumps({"allowed_apps": [LIVE_MODEL], "token_expiration": seconds}).encode()
    req = urllib.request.Request(
        TOKENS_URL, data=body,
        headers={"Authorization": f"Key {_key()}", "Content-Type": "application/json"},
        method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
