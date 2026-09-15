"""Server-side Decart studio — every call is authenticated and metered against
the user's server credits. The Decart key stays on the server.

Modes:
  photo         POST /studio/photo/start      (job) -> GET /studio/photo/{id}[/content]
  video/restyle POST /studio/video/start      (job) -> GET /studio/job/{id}[/content]
  live cam      POST /studio/live/start       -> LiveKit room the client joins direct
                POST /studio/live/prompt, POST /studio/live/stop
Live is metered by a background task that debits 1 wallet-sec/sec and cuts the
session the moment credit runs out — so a customer can never exceed what they paid.

Photo is a JOB, not a plain synchronous POST, even though Decart's own image
endpoint has no async variant to delegate to (video/restyle do). A real photo
can take Decart 30–90+ seconds, and holding one client HTTP connection open
that whole time behind Render's reverse proxy is fragile — a dropped
connection loses nothing here (the job keeps running server-side; the client
just polls the same job id again), and it stops one slow generation from
blocking FastAPI's event loop for every other request in flight.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app import db, decart, fal_image, fal_lucy, pricing
from app.deps import current_user

router = APIRouter(prefix="/studio", tags=["studio"])

# Which realtime engine /live/start hands out. Must match the customer app's
# default (backend/app/engines/pro_engine.py) or a customer gets credentials
# for a provider their local app isn't configured to use.
LIVE_PROVIDER = os.environ.get("APEXCAM_PRO_PROVIDER", "fal").strip().lower()
# Which engine Lucy Image runs on. "fal" (Nano Banana 2 — better instruction-
# following and identity-preserving face swap) is the default; "decart" stays
# a one-env-var rollback if fal ever needs to be pulled.
IMAGE_PROVIDER = os.environ.get("APEXCAM_IMAGE_PROVIDER", "fal").strip().lower()

# job_id -> user_id (so only the owner can fetch a result)
_jobs: dict[str, int] = {}
# session_id -> {uid, provider, stop, ...provider-specific state}
_live: dict[str, dict] = {}

# Photo jobs live on the SAME persistent disk as the database (db.DB_PATH),
# not in memory — a job started right before a deploy/restart (which happens
# routinely: every push redeploys the service) would otherwise vanish mid-air,
# surfacing as a 404 "Not found" to a client that's still polling the id it
# was given. Unlike video/restyle, Decart itself has no async job store to
# fall back to for a photo edit, so WE are the only place the result exists —
# it has to survive a restart on our own.
PHOTO_DIR = db.DB_PATH.parent / "photo_jobs"
PHOTO_JOB_TTL_S = 15 * 60      # an unfetched result older than this is dropped
PHOTO_STUCK_S = 4 * 60         # a "processing" job stuck this long self-heals to "error"


def _photo_meta_path(job_id: str) -> Path:
    return PHOTO_DIR / f"{job_id}.json"


def _photo_data_path(job_id: str) -> Path:
    return PHOTO_DIR / f"{job_id}.png"


def _write_photo_meta(job_id: str, uid: int, status: str, cost: float) -> None:
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    _photo_meta_path(job_id).write_text(json.dumps(
        {"uid": uid, "status": status, "cost": cost, "created": time.time()}))


def _read_photo_meta(job_id: str) -> dict | None:
    try:
        return json.loads(_photo_meta_path(job_id).read_text())
    except Exception:
        return None


def _drop_photo_job(job_id: str) -> None:
    _photo_meta_path(job_id).unlink(missing_ok=True)
    _photo_data_path(job_id).unlink(missing_ok=True)


def _drop_stale_photo_jobs() -> None:
    if not PHOTO_DIR.exists():
        return
    now = time.time()
    for p in PHOTO_DIR.glob("*.json"):
        meta = None
        try:
            meta = json.loads(p.read_text())
        except Exception:
            pass
        if not meta or now - meta.get("created", 0) > PHOTO_JOB_TTL_S:
            _drop_photo_job(p.stem)


@router.get("/pricing")
def studio_pricing() -> dict:
    """Live per-second sell rate for every metered mode — the client reads
    THIS instead of hardcoding a ratio to the live rate (video/restyle each
    have their own independent, admin-tunable margin now, not a fixed
    multiple of live's)."""
    return {
        "currency": pricing.CURRENCY,
        "live_usd_per_sec": pricing.mode_sell_usd_per_sec("live"),
        "video_usd_per_sec": pricing.mode_sell_usd_per_sec("video"),
        "restyle_usd_per_sec": pricing.mode_sell_usd_per_sec("restyle"),
        "cloud_voice_usd_per_sec": pricing.mode_sell_usd_per_sec("cloud_voice"),
    }


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
@router.post("/photo/start")
async def photo_start(file: UploadFile, reference: UploadFile | None = File(None),
                      prompt: str = Form(""), face_swap: bool = Form(False),
                      uid: int = Depends(current_user)) -> dict:
    # Recomputed on every call, never cached — reflects the owner's current
    # credit price from the admin panel immediately, no redeploy needed.
    cost = pricing.image_cost_wallet_seconds()
    if not db.spend(uid, cost, "image"):
        raise HTTPException(402, "Not enough credit — top up first")
    img = await file.read()
    ref = await reference.read() if reference else None
    _drop_stale_photo_jobs()
    jid = uuid.uuid4().hex
    _write_photo_meta(jid, uid, "processing", cost)

    async def run() -> None:
        try:
            # No prompt filtering/limiting here on purpose — the whole point of
            # Lucy Image is natural-language editing ("swap this face", "make
            # them hold X"); the provider's own model is what interprets it.
            # Runs off the event loop thread — both providers' clients here are
            # synchronous, and this can take well over a minute for a big photo.
            engine = fal_image if IMAGE_PROVIDER == "fal" else decart
            out = await asyncio.to_thread(engine.generate_photo, img, prompt or None,
                                          ref if (ref or face_swap) else None)
            _photo_data_path(jid).write_bytes(out)
            _write_photo_meta(jid, uid, "done", cost)
        except Exception:
            db.refund(uid, cost, "image failed")
            _write_photo_meta(jid, uid, "error", cost)

    asyncio.create_task(run())
    return {"job_id": jid}


@router.get("/photo/{job_id}")
def photo_status(job_id: str, uid: int = Depends(current_user)) -> dict:
    meta = _read_photo_meta(job_id)
    if not meta or meta["uid"] != uid:
        raise HTTPException(404, "Not found")
    # Self-heal: the ONLY way a job can stay "processing" forever is if the
    # server restarted mid-generation (a deploy lands while Decart is still
    # working) — the in-flight asyncio task is gone with it. Refund rather
    # than leave the customer's credit stuck on a job that will never finish.
    if meta["status"] == "processing" and time.time() - meta["created"] > PHOTO_STUCK_S:
        db.refund(uid, meta["cost"], "image job lost in a restart")
        meta["status"] = "error"
        _write_photo_meta(job_id, uid, "error", meta["cost"])
    return {"status": meta["status"]}


@router.get("/photo/{job_id}/content")
def photo_content(job_id: str, uid: int = Depends(current_user)) -> Response:
    meta = _read_photo_meta(job_id)
    if not meta or meta["uid"] != uid:
        raise HTTPException(404, "Not found")
    if meta["status"] != "done":
        raise HTTPException(404, "Not ready")
    try:
        data = _photo_data_path(job_id).read_bytes()
    except Exception:
        raise HTTPException(404, "Not ready")
    _drop_photo_job(job_id)
    return Response(content=data, media_type="image/png")


# --- Video / Restyle jobs -------------------------------------------------
@router.post("/video/start")
async def video_start(file: UploadFile, reference: UploadFile | None = File(None),
                      prompt: str = Form(""), mode: str = Form("video"),
                      face_swap: bool = Form(False),
                      uid: int = Depends(current_user)) -> dict:
    if mode not in ("video", "restyle"):
        raise HTTPException(400, "Unknown mode")
    video_bytes = await file.read()
    dur = decart.video_duration(video_bytes)
    cost = dur * pricing.mode_rate(mode)
    if not db.spend(uid, cost, f"{mode} {dur:.0f}s"):
        raise HTTPException(402, "Not enough credit — top up first")
    # video: reference is a face (only meaningful when actually swapping).
    # restyle: a style source. Both legitimately use it, unlike before, where
    # restyle's reference upload was silently dropped.
    ref = None
    if reference and (mode == "restyle" or face_swap):
        ref = await reference.read()
    model = {"video": decart.VIDEO_MODEL, "restyle": decart.RESTYLE_MODEL}[mode]
    try:
        jid = decart.submit_job(model, video_bytes, prompt or None, ref,
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


# --- Cloud voice cloning (Modal GPU) --------------------------------------
# Same broker shape as /live/*: this server mints a short-lived credential
# and hands off a URL; the actual audio stream goes customer<->Modal
# directly, never through here. Billed the same way as the fal live path for
# the same reason — this server holds nothing open, so it can't meter time
# directly; the client's app heartbeats /voice/cloud/tick every ~2s.
CLOUD_VOICE_WS_URL = os.environ.get(
    "APEXCAM_CLOUD_VOICE_WS",
    "wss://fridayemmanue366--apexcam-voice-voiceserver-web.modal.run/ws")

_voice_sessions: dict[str, dict] = {}   # session_id -> {uid, last_tick}


@router.post("/voice/cloud/start")
async def voice_cloud_start(uid: int = Depends(current_user)) -> dict:
    """Mint a short-lived (5 min) token scoped to the cloud-voice Modal
    service and hand back its URL. The token is signed with a SEPARATE
    secret from the main session token (security.make_voice_token) — Modal
    verifies it independently, no callback to this server."""
    if db.credit_seconds(uid) < 1.0:
        raise HTTPException(402, "Not enough credit — top up first")
    from app import security
    token = security.make_voice_token(uid)
    sid = uuid.uuid4().hex
    _voice_sessions[sid] = {"uid": uid, "last_tick": time.monotonic()}
    return {"session_id": sid, "token": token, "url": CLOUD_VOICE_WS_URL}


@router.post("/voice/cloud/tick")
async def voice_cloud_tick(session_id: str = Form(...), streaming: str = Form("false"),
                           uid: int = Depends(current_user)) -> dict:
    """Heartbeat metering, same pattern as /live/tick: the client posts this
    ~every 2s reporting whether audio is actually flowing. Debits only the
    elapsed time since the last tick, converted into wallet-seconds at
    cloud_voice's own rate (pricing.mode_rate) — connecting/idle time costs
    nothing."""
    sess = _voice_sessions.get(session_id)
    if not sess or sess["uid"] != uid:
        raise HTTPException(404, "No such session")
    now = time.monotonic()
    dt = min(now - sess.get("last_tick", now), 5.0)
    sess["last_tick"] = now
    if streaming == "true" and dt > 0:
        if not db.spend(uid, dt * pricing.mode_rate("cloud_voice"), "cloud_voice"):
            _voice_sessions.pop(session_id, None)
            return {"stopped": True}
    return {"stopped": False}


@router.post("/voice/cloud/stop")
async def voice_cloud_stop(session_id: str = Form(...), uid: int = Depends(current_user)) -> dict:
    sess = _voice_sessions.pop(session_id, None)
    return {"stopped": bool(sess and sess["uid"] == uid)}
