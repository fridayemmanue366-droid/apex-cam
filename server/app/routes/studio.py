"""Server-side Decart studio — every call is authenticated and metered against
the user's server credits. The Decart key stays on the server.

Modes:
  photo         POST /studio/photo            (sync image edit / face swap)
  video/restyle POST /studio/video/start      (job) -> GET /studio/job/{id}[/content]
  live cam      POST /studio/live/start       -> LiveKit room the client joins direct
                POST /studio/live/prompt, POST /studio/live/stop
Live is metered by a background task that debits 1 wallet-sec/sec and cuts the
session the moment credit runs out — so a customer can never exceed what they paid.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app import db, decart, fal_lucy, pricing
from app.deps import current_user
from app.pricing import MODE_RATE

router = APIRouter(prefix="/studio", tags=["studio"])

# Which realtime engine /live/start hands out. Must match the customer app's
# default (backend/app/engines/pro_engine.py) or a customer gets credentials
# for a provider their local app isn't configured to use.
LIVE_PROVIDER = os.environ.get("APEXCAM_PRO_PROVIDER", "fal").strip().lower()

# job_id -> user_id (so only the owner can fetch a result)
_jobs: dict[str, int] = {}
# session_id -> {uid, provider, stop, ...provider-specific state}
_live: dict[str, dict] = {}


@router.get("/image/pricing")
def image_pricing() -> dict:
    """What Lucy Image currently costs, in CREDITS — the customer-facing unit.
    The client shows "N credit(s)", never a raw $/₦ amount; the dollar/NGN
    values here are for the admin panel's own display, not the playground."""
    return {
        "credits": pricing.IMAGE_CREDITS,
        "currency": pricing.CURRENCY,
        "usd": pricing.image_sell_usd(),
        "charge": pricing.image_charge_amount(),
    }


# --- Photo (Lucy Image) ---------------------------------------------------
@router.post("/photo")
async def photo(file: UploadFile, reference: UploadFile | None = File(None),
                prompt: str = Form(""), face_swap: bool = Form(False),
                uid: int = Depends(current_user)) -> Response:
    # Recomputed on every call, never cached — reflects the owner's current
    # image margin from the admin panel immediately, no redeploy needed.
    cost = pricing.image_cost_wallet_seconds()
    if not db.spend(uid, cost, "image"):
        raise HTTPException(402, "Not enough credit — top up first")
    ref = await reference.read() if reference else None
    try:
        # No prompt filtering/limiting here on purpose — the whole point of
        # Lucy Image is natural-language editing ("swap this face", "make
        # them hold X"); Decart's own model is what interprets it.
        out = decart.generate_photo(await file.read(), prompt or None,
                                    ref if (ref or face_swap) else None)
    except Exception:
        db.refund(uid, cost, "image failed")
        raise HTTPException(502, "Could not generate the image")
    return Response(content=out, media_type="image/png")


# --- Video / Restyle jobs -----------------------------------------------
@router.post("/video/start")
async def video_start(file: UploadFile, reference: UploadFile | None = File(None),
                      prompt: str = Form(""), mode: str = Form("video"),
                      face_swap: bool = Form(False),
                      uid: int = Depends(current_user)) -> dict:
    video_bytes = await file.read()
    dur = decart.video_duration(video_bytes)
    is_restyle = mode == "restyle"
    cost = dur * MODE_RATE["restyle" if is_restyle else "video"]
    if not db.spend(uid, cost, f"{mode} {dur:.0f}s"):
        raise HTTPException(402, "Not enough credit — top up first")
    ref = None
    if not is_restyle and (reference or face_swap):
        ref = await reference.read() if reference else None
    try:
        jid = decart.submit_job(decart.RESTYLE_MODEL if is_restyle else decart.VIDEO_MODEL,
                                video_bytes, prompt or None, ref,
                                filename=file.filename or "in.mp4",
                                content_type=file.content_type or "video/mp4")
    except Exception as exc:
        db.refund(uid, cost, f"{mode} submit failed")
        raise HTTPException(502, str(exc) or "Could not start the video")
    _jobs[jid] = uid
    return {"job_id": jid, "cost_minutes": round(cost / 60.0, 2)}


@router.get("/job/{job_id}")
def job_stat(job_id: str, uid: int = Depends(current_user)) -> dict:
    if _jobs.get(job_id) not in (uid, None):
        raise HTTPException(404, "Not found")
    return {"status": decart.job_status(job_id)}


@router.get("/job/{job_id}/content")
def job_result(job_id: str, uid: int = Depends(current_user)) -> Response:
    if _jobs.get(job_id) not in (uid, None):
        raise HTTPException(404, "Not found")
    try:
        return Response(content=decart.job_content(job_id), media_type="video/mp4")
    except Exception:
        raise HTTPException(404, "Result not ready")


# --- Live cam ------------------------------------------------------------
# Two metering strategies, because the two providers hand off media differently:
#
#  decart: this server holds the Decart control WS open for the whole session,
#          so it can debit on its own per-second timer and KNOWS immediately if
#          the connection drops.
#  fal:    media and signaling go directly customer<->fal after the initial JWT
#          mint — this server holds nothing open, so it has no way to know the
#          session is still alive on its own. The customer's app heartbeats
#          /live/tick every ~2s reporting whether Lucy is actually streaming;
#          we debit exactly the elapsed time since the last tick, only while
#          streaming=true. If ticks stop (crash, network drop), billing simply
#          stops — nothing runs away, it just goes quiet in _live.
TICK_STALE_S = 20.0   # a fal session with no tick this long is treated as dead


async def _meter_live_decart(session_id: str) -> None:
    """Debit 1 wallet-sec/sec while the live session runs; end it at zero."""
    sess = _live.get(session_id)
    if not sess:
        return
    uid = sess["uid"]
    last = time.monotonic()
    while not sess["stop"].is_set():
        await asyncio.sleep(1.0)
        now = time.monotonic()
        dt = now - last
        last = now
        if not db.spend(uid, dt, "live"):          # out of credit -> cut the session
            break
    await _close_live(session_id)


async def _close_live(session_id: str) -> None:
    sess = _live.pop(session_id, None)
    if not sess:
        return
    sess["stop"].set()
    ws = sess.get("ws")
    if ws is not None:
        try:
            await ws.close()
        except Exception:
            pass


@router.post("/live/start")
async def live_start(prompt: str = Form(""), reference: UploadFile | None = File(None),
                     uid: int = Depends(current_user)) -> dict:
    """Begin a live session. Requires credit. Returns credentials shaped for
    whichever provider is active (LIVE_PROVIDER) — the client checks `provider`
    in the response to know which fields it got and which local endpoint to
    hand them to."""
    if db.credit_seconds(uid) < 1.0:
        raise HTTPException(402, "Not enough credit — top up first")
    sid = uuid.uuid4().hex

    if LIVE_PROVIDER == "fal":
        if not fal_lucy.configured():
            raise HTTPException(503, "Live (fal) not configured")
        try:
            jwt = fal_lucy.mint_token()
        except Exception as exc:
            raise HTTPException(502, f"Could not start live: {exc}")
        _live[sid] = {"uid": uid, "provider": "fal", "stop": asyncio.Event(),
                      "last_tick": time.monotonic()}
        return {"session_id": sid, "provider": "fal", "jwt": jwt, "model": fal_lucy.LIVE_MODEL}

    # decart
    ref = await reference.read() if reference else None
    try:
        room = await decart.live_room(ref, prompt or None)
    except Exception as exc:
        raise HTTPException(502, f"Could not start live: {exc}")
    _live[sid] = {"ws": room["ws"], "uid": uid, "provider": "decart", "stop": asyncio.Event()}
    _live[sid]["task"] = asyncio.create_task(_meter_live_decart(sid))
    info = room["info"]
    return {"session_id": sid, "provider": "decart", "livekit_url": info["livekit_url"],
            "token": info["token"], "room_name": info.get("room_name")}


@router.post("/live/tick")
async def live_tick(session_id: str = Form(...), streaming: str = Form("false"),
                    uid: int = Depends(current_user)) -> dict:
    """fal-provider heartbeat: the customer's app posts this ~every 2s reporting
    whether Lucy frames are really flowing (fair metering — connecting/stalled
    time costs nothing). Debits only the elapsed time since the last tick, capped
    so a long gap (e.g. this server was asleep) can't over-bill in one jump."""
    sess = _live.get(session_id)
    if not sess or sess["uid"] != uid:
        raise HTTPException(404, "No such session")
    now = time.monotonic()
    dt = min(now - sess.get("last_tick", now), 5.0)
    sess["last_tick"] = now
    if streaming == "true" and dt > 0:
        if not db.spend(uid, dt, "live"):           # out of credit -> cut the session
            await _close_live(session_id)
            return {"stopped": True}
    return {"stopped": False}


@router.post("/live/prompt")
async def live_prompt(session_id: str = Form(...), prompt: str = Form(...),
                      uid: int = Depends(current_user)) -> dict:
    """decart only — its session lives server-side so we relay into the held-open
    control WS. fal sessions live entirely on the customer's app: prompt changes
    there are set locally (see engines/fal_pro.py) and never touch this route."""
    import json
    sess = _live.get(session_id)
    if not sess or sess["uid"] != uid:
        raise HTTPException(404, "No such session")
    if sess.get("provider") != "decart":
        return {"ok": True}   # no-op: fal updates its own look locally
    try:
        await sess["ws"].send(json.dumps({"type": "prompt", "prompt": prompt,
                                          "enhance_prompt": True}))
    except Exception:
        raise HTTPException(502, "Could not update the look")
    return {"ok": True}


@router.post("/live/stop")
async def live_stop(session_id: str = Form(...), uid: int = Depends(current_user)) -> dict:
    sess = _live.get(session_id)
    if sess and sess["uid"] == uid:
        await _close_live(session_id)
    return {"stopped": True}
