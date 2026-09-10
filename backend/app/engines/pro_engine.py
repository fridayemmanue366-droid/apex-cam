"""Apex Pro realtime provider switch.

Everything that needs the live Lucy engine imports `lucy_pro` from HERE, not
from lucy_pro.py or fal_pro.py directly — that's what makes switching providers
a one-line env change instead of an edit-every-call-site migration:

  APEXCAM_PRO_PROVIDER=fal     (default) — Decart's Lucy via fal's realtime relay.
                                 No visible watermark in testing; needs the
                                 reference_image_url data-URI wiring in fal_pro.py.
  APEXCAM_PRO_PROVIDER=decart  — Decart's own API directly (LiveKit). Proven,
                                 but bakes a visible "AI Generated" watermark
                                 into the output (confirmed both by Decart's own
                                 ToS/watermark policy page and a live test).

Both engines implement the same shape (process/status/prompt/set_reference/
start_cloud/stop_cloud), so nothing else needs to know which one is active.
"""
from __future__ import annotations

import os

PROVIDER = os.environ.get("APEXCAM_PRO_PROVIDER", "fal").strip().lower()

if PROVIDER == "decart":
    from app.engines.lucy_pro import lucy_pro
else:
    from app.engines.fal_pro import lucy_pro

__all__ = ["lucy_pro", "PROVIDER"]
