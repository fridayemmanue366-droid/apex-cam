"""Server-side Decart studio — every call is authenticated and metered against
the user's server credits. The Decart key stays on the server.

Modes:
  photo         POST /studio/photo            (sync image edit / face swap)
  video/restyle POST /studio/video/start      (job) -> GET /studio/job/{id}[/content]
  live cam      POST /studio/live/start       -> LiveKit room the client joins direct
                POST /studio/live/prompt, POST /studio/live/tick, POST /studio/live/stop
Live is metered by the client's heartbeat (/live/tick): we debit only the seconds
Lucy is really streaming frames back, and cut the session the moment credit runs
out — so a customer never pays for connecting/stalls, nor exceeds what they paid.
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app import db, decart
from app.deps import current_user
from app.pricing import IMAGE_COST_SECONDS, MODE_RATE

router = APIRouter(prefix="/studio", tags=["studio"])

# job_id -> user_id (so only the owner can fetch a result)
_jobs: dict[str, int] = {}
# session_id -> {ws, uid, task, stop}
_live: dict[str, dict] = {}


# --- Photo ---------------------------------------------------------------
@router.post("/photo")
async def photo(file: UploadFile, reference: UploadFile | None = File(None),
                prompt: str = Form(""), face_swap: bool = Form(False),
                uid: int = Depends(current_user)) -> Response:
    if not db.spend(uid, IMAGE_COST_SECONDS, "photo"):
        raise HTTPException(402, "Not enough credit — top up first")
    ref = await reference.read() if reference else None
    try:
        out = decart.generate_photo(await file.read(), prompt or None,
                                    ref if (ref or face_swap) else None)
    except Exception:
        db.refund(uid, IMAGE_COST_SECONDS, "photo failed")
        raise HTTPException(502, "Could not generate the photo")
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
# The customer is billed ONLY while Lucy is really sending transformed frames
# back — not during the 3-8s of connecting to Decart/LiveKit. The server can't
# see those frames (they go Decart -> the customer's PC directly), so the PC
# sends a heartbeat (/live/tick, streaming=true|false) every ~2s and we debit
# the elapsed streaming time between heartbeats. No heartbeat, no charge.
TICK_CAP = 5.0        # never bill more than this per heartbeat (covers a lag spike)
STALL_TIMEOUT = 20.0  # streaming then PC goes silent this long -> reap
START_TIMEOUT = 90.0  # heartbeating but never a real frame in this long -> give up
LEGACY_GRACE = 12.0   # no heartbeat AT ALL this long after start -> old build that
                      # can't heartbeat; fall back to the old wall-clock billing so
                      # customers on the previous installer keep working.


async def _meter_live(session_id: str) -> None:
    """Watchdog + legacy fallback — normal billing happens in /live/tick.
    Reaps the session (and stops paying Decart) if the PC goes silent, and if a
    session NEVER heartbeats (an old installer) it bills the old wall-clock way."""
    started_at = time.monotonic()
    legacy = False
    legacy_last = 0.0
    while True:
        await asyncio.sleep(2.0)
        sess = _live.get(session_id)
        if not sess or sess["stop"].is_set():
            return
        uid = sess["uid"]
        now = time.monotonic()
        if legacy:                                 # old build: debit wall-clock
            if not db.spend(uid, now - legacy_last, "live"):
                break
            legacy_last = now
            continue
        if not sess["ever_ticked"] and (now - started_at) > LEGACY_GRACE:
            legacy, legacy_last = True, now        # never heard a beat -> old build
            continue
        if sess["started"]:                        # real streaming has begun
            if now - sess["last_stream"] > STALL_TIMEOUT:   # frames stopped -> reap
                break
        elif (now - started_at) > START_TIMEOUT:   # heartbeating but never streamed
            break
    await _close_live(session_id)


async def _close_live(session_id: str) -> None:
    sess = _live.pop(session_id, None)
    if not sess:
        return
    sess["stop"].set()
    try:
        await sess["ws"].close()
    except Exception:
        pass


@router.post("/live/start")
async def live_start(prompt: str = Form(""), reference: UploadFile | None = File(None),
                     uid: int = Depends(current_user)) -> dict:
    """Begin a live session. Requires credit; returns the LiveKit room the client
    joins directly (Decart key stays here). Metering starts immediately."""
    if db.credit_seconds(uid) < 1.0:
        raise HTTPException(402, "Not enough credit — top up first")
    ref = await reference.read() if reference else None
    try:
        room = await decart.live_room(ref, prompt or None)
    except Exception as exc:
        raise HTTPException(502, f"Could not start live: {exc}")
    sid = uuid.uuid4().hex
    now = time.monotonic()
    _live[sid] = {"ws": room["ws"], "uid": uid, "stop": asyncio.Event(),
                  "last_tick": now,      # last heartbeat of ANY kind (watchdog)
                  "last_bill": now,      # last time we debited (streaming clock)
                  "last_stream": now,    # last heartbeat that was actually streaming
                  "started": False,      # has real streaming begun yet?
                  "streaming": False,    # was the previous heartbeat streaming?
                  "ever_ticked": False}  # any heartbeat at all? (else old build)
    _live[sid]["task"] = asyncio.create_task(_meter_live(sid))
    info = room["info"]
    return {"session_id": sid, "livekit_url": info["livekit_url"],
            "token": info["token"], "room_name": info.get("room_name")}


@router.post("/live/tick")
async def live_tick(session_id: str = Form(...), streaming: bool = Form(True),
                    uid: int = Depends(current_user)) -> dict:
    """Heartbeat from the customer's PC (~every 2s). `streaming` is True only while
    Lucy is really delivering transformed frames. We bill the elapsed streaming
    time since the last billed heartbeat — so connecting/stalls are never charged."""
    sess = _live.get(session_id)
    if not sess or sess["uid"] != uid:
        raise HTTPException(404, "No such session")
    now = time.monotonic()
    sess["ever_ticked"] = True
    charged = False
    # Only bill a CONTIGUOUS streaming interval (this tick and the last were both
    # streaming). The first streaming tick, and any tick resuming after a stall,
    # starts a fresh interval with no charge.
    if streaming and sess["streaming"]:
        dt = min(now - sess["last_bill"], TICK_CAP)
        if not db.spend(uid, dt, "live"):          # out of credit -> cut the session
            await _close_live(session_id)
            return {"ok": False, "stopped": True, "reason": "out_of_credit"}
        charged = True
    if streaming:
        sess["started"] = True
        sess["last_bill"] = now
        sess["last_stream"] = now
    sess["streaming"] = streaming
    sess["last_tick"] = now
    return {"ok": True, "charged": charged}


@router.post("/live/prompt")
async def live_prompt(session_id: str = Form(...), prompt: str = Form(...),
                      uid: int = Depends(current_user)) -> dict:
    import json
    sess = _live.get(session_id)
    if not sess or sess["uid"] != uid:
        raise HTTPException(404, "No such session")
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
