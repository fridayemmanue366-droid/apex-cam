"""SafeShare — the owner's second product, hosted on the SAME Render service and
the SAME Flutterwave account as Apex Cam (owner decision, 2026-10-13), but kept
fully separate so the two can never clash:

- its own tables (ss_users, ss_transactions) — never touches Apex Cam's users,
  wallets or licenses;
- its own sign-in tokens, signed with a key DERIVED for SafeShare, so a SafeShare
  token is useless on Apex Cam's routes and vice versa;
- its own routes under /safeshare/* and its own Flutterwave tx_ref prefix "ss-"
  plus meta.app="safeshare" (the shared /pay/webhook hands those to this module);
- its own settings, all prefixed "ss_" (price, trial, download link).

Only the important parts live server-side (owner: "we should not move
everything to server side"): accounts, the $1.66/month subscription, and the
public download link. SafeShare itself stays a local Wi-Fi app.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app import db
from app.security import SECRET, hash_password, verify_password

router = APIRouter(prefix="/safeshare", tags=["safeshare"])

DAY = 86400.0
MONTH_DAYS = 30
TOKEN_TTL = 60 * 60 * 24 * 90                                      # 90 days
SS_SECRET = hmac.new(SECRET, b"safeshare-token-v1", hashlib.sha256).digest()
BUNDLE = Path(__file__).resolve().parents[1] / "safeshare_bundle" / "SafeShare-Windows.zip"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- storage ---------------------------------------------------------------
def _c() -> sqlite3.Connection:
    c = db._connect()
    if not getattr(_c, "_ready", False):
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS ss_users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                paid_until    REAL NOT NULL DEFAULT 0,
                created       REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ss_transactions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                kind     TEXT NOT NULL,        -- payment | grant | trial
                days     REAL NOT NULL,
                amount   REAL NOT NULL DEFAULT 0,
                currency TEXT,
                detail   TEXT,
                tx_ref   TEXT UNIQUE,
                created  REAL NOT NULL
            );
            """)
        c.commit()
        _c._ready = True
    return c


def _setting(key: str, default: float) -> float:
    try:
        return float(db.get_setting("ss_" + key, str(default)))
    except (TypeError, ValueError):
        return default


def price_usd() -> float:
    return max(0.0, _setting("price_usd", 1.66))


def trial_days() -> float:
    return max(0.0, _setting("trial_days", 1))          # owner: 1-day free trial


def free_emails() -> set[str]:
    """Accounts that never lock (owner: "free access for the owner's own account").
    Comma-separated setting ss_free_emails; the owner's email is the default."""
    raw = db.get_setting("ss_free_emails", "fridayemmanue366@gmail.com") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _amounts() -> tuple[float, str, float, str]:
    """(display amount, display currency, pay amount, pay currency) — same
    display/charge split as Apex Cam (NGN charge keeps bank transfer/USSD)."""
    from app import pricing
    usd = price_usd()
    shown = float(round(usd * pricing.effective_rate())) if pricing.currency() == "NGN" else usd
    pay = float(round(usd * pricing.effective_rate())) if pricing.pay_currency() == "NGN" else usd
    return shown, pricing.currency(), pay, pricing.pay_currency()


def _extend(uid: int, days: float, kind: str, tx_ref: str | None, detail: str,
            amount: float = 0.0, currency: str | None = None) -> bool:
    c = _c()
    with db._lock:
        try:
            c.execute("INSERT INTO ss_transactions(user_id,kind,days,amount,currency,detail,tx_ref,created)"
                      " VALUES(?,?,?,?,?,?,?,?)",
                      (uid, kind, days, amount, currency, detail, tx_ref, time.time()))
        except sqlite3.IntegrityError:
            return False                                   # tx_ref already applied
        row = c.execute("SELECT paid_until FROM ss_users WHERE id=?", (uid,)).fetchone()
        base = max(time.time(), float(row["paid_until"]) if row else 0.0)
        c.execute("UPDATE ss_users SET paid_until=? WHERE id=?", (base + days * DAY, uid))
        c.commit()
    return True


# --- tokens (SafeShare-only signing key) -------------------------------------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def make_token(uid: int) -> str:
    payload = _b64(json.dumps({"ssuid": uid, "exp": int(time.time()) + TOKEN_TTL}).encode())
    return payload + "." + _b64(hmac.new(SS_SECRET, payload.encode(), hashlib.sha256).digest())


def _uid(authorization: str) -> int:
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    try:
        payload, sig = token.split(".")
        good = _b64(hmac.new(SS_SECRET, payload.encode(), hashlib.sha256).digest())
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if hmac.compare_digest(sig, good) and data.get("exp", 0) > time.time():
            uid = int(data["ssuid"])
            if _c().execute("SELECT 1 FROM ss_users WHERE id=?", (uid,)).fetchone():
                return uid
    except Exception:
        pass
    raise HTTPException(401, "Please sign in to SafeShare again")


# --- accounts ------------------------------------------------------------------
class Creds(BaseModel):
    email: str
    password: str


@router.post("/auth/register")
def register(cr: Creds) -> dict:
    email = cr.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email")
    if len(cr.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    c = _c()
    with db._lock:
        try:
            cur = c.execute("INSERT INTO ss_users(email,password_hash,created) VALUES(?,?,?)",
                            (email, hash_password(cr.password), time.time()))
            c.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That email already has a SafeShare account — log in")
    uid = int(cur.lastrowid)
    if trial_days() > 0:
        _extend(uid, trial_days(), "trial", f"ss-trial-{uid}", "free trial")
    return {"token": make_token(uid), "email": email}


@router.post("/auth/login")
def login(cr: Creds) -> dict:
    email = cr.email.strip().lower()
    u = _c().execute("SELECT id,password_hash FROM ss_users WHERE email=?", (email,)).fetchone()
    if not u or not verify_password(cr.password, u["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return {"token": make_token(int(u["id"])), "email": email}


def _status(uid: int) -> dict:
    u = _c().execute("SELECT email,paid_until FROM ss_users WHERE id=?", (uid,)).fetchone()
    until = float(u["paid_until"])
    shown, cur, _, _ = _amounts()
    left = max(0.0, until - time.time())
    if u["email"] in free_emails():
        until, left = time.time() + 3650 * DAY, 3650 * DAY     # free forever (shows ~10 years)
    return {"email": u["email"], "active": left > 0, "paid_until": until,
            "days_left": round(left / DAY, 1), "price": shown, "currency": cur,
            "price_usd": price_usd(), "period_days": MONTH_DAYS}


@router.get("/me")
def me(authorization: str = Header(default="")) -> dict:
    """What the SafeShare app checks: is this account paid up?"""
    return _status(_uid(authorization))


# --- payment ($1.66 / month via the shared Flutterwave account) --------------
@router.post("/pay/start")
def pay_start(authorization: str = Header(default="")) -> dict:
    from app.routes import pay
    uid = _uid(authorization)
    if not pay.FLW_SECRET:
        raise HTTPException(503, "Payments not configured")
    email = _status(uid)["email"]
    _, _, amount, currency = _amounts()
    tx_ref = f"ss-{uid}-{uuid.uuid4().hex[:12]}"
    resp = pay._flw("POST", "/payments", {
        "tx_ref": tx_ref, "amount": amount, "currency": currency,
        "redirect_url": f"{pay.PUBLIC_URL}/safeshare/pay/callback",
        "customer": {"email": email},
        "customizations": {"title": "SafeShare", "description": f"{MONTH_DAYS} days of SafeShare"},
        "meta": {"app": "safeshare", "ss_user_id": uid, "days": MONTH_DAYS, "charge": amount,
                 "cur": currency},
    })
    if resp.get("status") != "success":
        raise HTTPException(502, "Could not start payment")
    return {"link": resp["data"]["link"], "tx_ref": tx_ref}


def apply_verified(d: dict) -> bool:
    """Called by the shared /pay/webhook + our callback with a Flutterwave
    transaction ALREADY verified as successful. Idempotent by tx_ref."""
    meta = d.get("meta") or {}
    uid = int(meta.get("ss_user_id", 0) or 0)
    tx_ref = str(d.get("tx_ref", ""))
    if uid <= 0 or not tx_ref.startswith("ss-"):
        return False
    expected = float(meta.get("charge", 0) or 0)
    amount = float(d.get("amount", 0) or 0)
    if expected <= 0 or abs(amount - expected) > max(1.0, expected * 0.02):
        return False
    if meta.get("cur") and d.get("currency") != meta.get("cur"):
        return False                                   # paid in a different currency than quoted
    _extend(uid, float(meta.get("days", MONTH_DAYS)), "payment", tx_ref,
            f"{MONTH_DAYS} days", amount, d.get("currency"))
    return True


@router.get("/pay/callback")
def pay_callback(status: str = "", transaction_id: str = "") -> HTMLResponse:
    from app.routes import pay
    ok = False
    if status in ("successful", "completed") and transaction_id:
        try:
            r = pay._flw("GET", f"/transactions/{transaction_id}/verify")
            d = r.get("data", {}) if r.get("status") == "success" else {}
            ok = d.get("status") == "successful" and apply_verified(d)
        except Exception:
            ok = False
    msg = ("<h1 style='color:#3ecf8e'>Payment successful ✓</h1><p>SafeShare is active for "
           f"{MONTH_DAYS} more days. Return to SafeShare.</p>") if ok else \
          "<h1 style='color:#e66'>Payment not completed</h1><p>Nothing was charged to your SafeShare account.</p>"
    return HTMLResponse(f"<html><body style='font-family:sans-serif;background:#10161d;color:#e8eef3;"
                        f"text-align:center;padding:60px'>{msg}</body></html>")


# --- public download link ------------------------------------------------------
@router.get("/download", response_class=HTMLResponse)
def download_page() -> HTMLResponse:
    have = BUNDLE.exists()
    size = f"{BUNDLE.stat().st_size / 1e6:.1f} MB" if have else ""
    shown, cur, _, _ = _amounts()
    price = f"${price_usd():.2f}" if cur == "USD" else f"₦{shown:,.0f}"
    btn = (f"<a class='b' href='/safeshare/download/file'>⬇ Download SafeShare for Windows ({size})</a>"
           if have else "<p>Download coming soon.</p>")
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SafeShare</title>
<style>body{{font-family:system-ui,sans-serif;background:#10161d;color:#e8eef3;margin:0;padding:40px 16px;text-align:center}}
.c{{max-width:560px;margin:auto}} h1{{font-size:34px;margin:0 0 8px}} p{{color:#9fb0bf;line-height:1.5}}
.b{{display:inline-block;margin:22px 0;padding:14px 26px;background:#3ecf8e;color:#08130d;border-radius:10px;
text-decoration:none;font-weight:700}} ul{{text-align:left;color:#c6d3de;line-height:1.7}}</style></head>
<body><div class="c"><h1>SafeShare</h1>
<p>Send photos, videos, apps and documents between your PCs and phones over your own Wi-Fi —
byte-for-byte, no quality loss, nothing uploaded to the internet.</p>{btn}
<ul><li>Unzip it, then double-click <b>SafeShare.exe</b>.</li>
<li>If Windows says "Windows protected your PC", click <b>More info → Run anyway</b>.</li>
<li>Phones need nothing installed — scan the QR code SafeShare shows.</li></ul>
<p>{price} per month.</p></div></body></html>""")


@router.get("/download/file")
def download_file() -> FileResponse:
    if not BUNDLE.exists():
        raise HTTPException(404, "SafeShare download not available yet")
    return FileResponse(BUNDLE, filename="SafeShare-Windows.zip", media_type="application/zip")


# --- owner (admin panel) --------------------------------------------------------
def admin_overview() -> dict:
    c = _c()
    now = time.time()
    users = [{"email": r["email"], "active": float(r["paid_until"]) > now,
              "paid_until": float(r["paid_until"]), "created": float(r["created"])}
             for r in c.execute("SELECT email,paid_until,created FROM ss_users ORDER BY created DESC")]
    tx = [dict(r) for r in c.execute(
        "SELECT t.kind,t.days,t.amount,t.currency,t.detail,t.created,u.email FROM ss_transactions t"
        " LEFT JOIN ss_users u ON u.id=t.user_id ORDER BY t.created DESC LIMIT 60")]
    revenue = c.execute("SELECT currency, SUM(amount) s FROM ss_transactions WHERE kind='payment'"
                        " GROUP BY currency").fetchall()
    shown, cur, _, _ = _amounts()
    return {"price_usd": price_usd(), "price": shown, "currency": cur, "trial_days": trial_days(),
            "download_ready": BUNDLE.exists(), "users": users, "transactions": tx,
            "revenue": {r["currency"]: r["s"] for r in revenue}}


def admin_grant(email: str, days: float) -> dict:
    u = _c().execute("SELECT id FROM ss_users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not u:
        raise HTTPException(404, f"No SafeShare account for {email}")
    _extend(int(u["id"]), days, "grant", f"ss-admin-{uuid.uuid4().hex[:12]}", f"owner grant {days:g} days")
    return _status(int(u["id"]))


def admin_delete(email: str) -> dict:
    """Remove a SafeShare account and its history (e.g. test accounts)."""
    c = _c()
    u = c.execute("SELECT id FROM ss_users WHERE email=?", (email.strip().lower(),)).fetchone()
    if not u:
        raise HTTPException(404, f"No SafeShare account for {email}")
    with db._lock:
        c.execute("DELETE FROM ss_transactions WHERE user_id=?", (u["id"],))
        c.execute("DELETE FROM ss_users WHERE id=?", (u["id"],))
        c.commit()
    return {"deleted": email}


def admin_settings(price: float | None, trial: float | None) -> dict:
    if price is not None:
        if not 0 <= price <= 100:
            raise HTTPException(400, "price must be between 0 and 100 USD")
        db.set_setting("ss_price_usd", str(price))
    if trial is not None:
        if not 0 <= trial <= 365:
            raise HTTPException(400, "trial must be between 0 and 365 days")
        db.set_setting("ss_trial_days", str(trial))
    return admin_overview()
