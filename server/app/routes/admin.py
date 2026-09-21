"""Owner-only admin actions — granting promo/test credit and comping customers.

SECURITY: this endpoint hands out real money's worth of credit, so it is locked
down three ways:
  1. FAIL CLOSED — if APEXCAM_ADMIN_KEY is not set in the environment the route
     returns 404 and cannot be used at all. A forgotten config can never expose it.
  2. Constant-time key comparison (no timing oracle).
  3. A hard per-call cap, so even a leaked key can't drain the account in one shot.
Every grant is written to the transactions table (kind=topup, tx_ref=admin-…) so
the books stay auditable — a grant looks exactly like a payment, minus the payment.
"""
from __future__ import annotations

import hmac
import os
import uuid

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from app import db

router = APIRouter(prefix="/admin", tags=["admin"])

MAX_GRANT_MINUTES = 600.0        # blast-radius cap for a single call


def _require_admin(key: str) -> None:
    admin = os.environ.get("APEXCAM_ADMIN_KEY") or ""
    if not admin:
        # Not configured -> behave as if the route does not exist.
        raise HTTPException(404, "Not found")
    if not hmac.compare_digest(key or "", admin):
        raise HTTPException(403, "Forbidden")


@router.post("/grant")
def grant(key: str = Form(...), email: str = Form(...), minutes: float = Form(...),
          note: str = Form("")) -> dict:
    """Add cloud credit to an account without a payment (promo, testing, refund)."""
    _require_admin(key)
    if minutes <= 0 or minutes > MAX_GRANT_MINUTES:
        raise HTTPException(400, f"minutes must be between 0 and {MAX_GRANT_MINUTES:g}")
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No account for {email}")
    uid = int(user["id"])
    seconds = float(minutes) * 60.0
    db.topup(uid, seconds, tx_ref=f"admin-{uuid.uuid4().hex}",
             detail=note or f"admin grant {minutes:g} min")
    total = db.credit_seconds(uid)
    return {"email": email, "granted_minutes": minutes,
            "credit_seconds": total, "credit_minutes": round(total / 60.0, 2)}


@router.post("/balance")
def balance(key: str = Form(...), email: str = Form(...)) -> dict:
    """Look up an account's credit (support: 'my credit didn't show')."""
    _require_admin(key)
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No account for {email}")
    secs = db.credit_seconds(int(user["id"]))
    return {"email": email, "credit_seconds": secs, "credit_minutes": round(secs / 60.0, 2)}


@router.post("/subscription")
def grant_subscription(key: str = Form(...), email: str = Form(...),
                       days: float = Form(...), note: str = Form("")) -> dict:
    """Extend a customer's LOCAL-APP access (the monthly subscription) by hand —
    for comping, fixing a failed payment, or giving a longer trial."""
    _require_admin(key)
    if days <= 0 or days > 400:
        raise HTTPException(400, "days must be between 0 and 400")
    user = db.get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No account for {email}")
    uid = int(user["id"])
    db.extend_subscription(uid, days, tx_ref=f"admin-{uuid.uuid4().hex}",
                           detail=note or f"admin grant {days:g} days")
    return {"email": email, "granted_days": days, "access_until": db.access_until(uid)}


@router.post("/settings")
def settings(key: str = Form(...), trial_days: float | None = Form(None),
             installer_url: str | None = Form(None),
             installer_version: str | None = Form(None)) -> dict:
    """Read or change owner settings: the free-trial length for NEW signups, and
    the download link + version label shown on the public /download page —
    editable here so neither needs a Render restart. Existing customers are
    unaffected by the trial; extend them individually with the subscription
    action. An empty installer_url clears the link (page shows "coming soon")."""
    _require_admin(key)
    if trial_days is not None:
        if trial_days < 0 or trial_days > 365:
            raise HTTPException(400, "trial_days must be between 0 and 365")
        db.set_setting("trial_days", str(trial_days))
    if installer_url is not None:
        url = installer_url.strip()
        if url and not url.lower().startswith("https://"):
            raise HTTPException(400, "installer_url must start with https://")
        if len(url) > 500:
            raise HTTPException(400, "installer_url is too long")
        db.set_setting("installer_url", url)
    if installer_version is not None:
        db.set_setting("installer_version", installer_version.strip()[:40])
    return {"trial_days": db.trial_days(),
            "installer_url": db.get_setting("installer_url", ""),
            "installer_version": db.get_setting("installer_version", "")}


@router.post("/pricing")
def pricing_admin(key: str = Form(...),
                  margin: float | None = Form(None),
                  margin_video: float | None = Form(None),
                  margin_restyle: float | None = Form(None),
                  margin_cloud_voice: float | None = Form(None),
                  credit_usd: float | None = Form(None),
                  voicenote_credits_per_block: int | None = Form(None),
                  voicenote_chars_per_block: int | None = Form(None),
                  rate_buffer: float | None = Form(None),
                  rate_mode: str | None = Form(None),
                  manual_rate: float | None = Form(None),
                  currency: str | None = Form(None),
                  pay_currency: str | None = Form(None)) -> dict:
    """Read or change pricing knobs live — each model's own margin, the rate
    buffer, and whether the dollar rate auto-tracks the live market or is set
    by hand. Returns a full snapshot (cost, live rate, effective rate, every
    model's price + profit, and every package's price + profit)."""
    _require_admin(key)
    from app import pricing
    if margin is not None:
        if margin < 1.0 or margin > 5.0:
            raise HTTPException(400, "margin must be between 1.0 and 5.0")
        db.set_setting("pricing_margin", str(margin))
    for mode, val in (("video", margin_video), ("restyle", margin_restyle),
                     ("cloud_voice", margin_cloud_voice)):
        if val is not None:
            if val < 1.0 or val > 10.0:
                raise HTTPException(400, f"margin_{mode} must be between 1.0 and 10.0")
            db.set_setting(f"margin_{mode}", str(val))
    if credit_usd is not None:
        if credit_usd < 0.01 or credit_usd > 5.0:
            raise HTTPException(400, "credit_usd must be between 0.01 and 5.0")
        db.set_setting("credit_usd", str(credit_usd))
    if voicenote_credits_per_block is not None:
        if voicenote_credits_per_block < 1 or voicenote_credits_per_block > 100:
            raise HTTPException(400, "voicenote_credits_per_block must be between 1 and 100")
        db.set_setting("voicenote_credits_per_block", str(voicenote_credits_per_block))
    if voicenote_chars_per_block is not None:
        if voicenote_chars_per_block < 10 or voicenote_chars_per_block > 5000:
            raise HTTPException(400, "voicenote_chars_per_block must be between 10 and 5000")
        db.set_setting("voicenote_chars_per_block", str(voicenote_chars_per_block))
    if rate_buffer is not None:
        if rate_buffer < 1.0 or rate_buffer > 3.0:
            raise HTTPException(400, "rate_buffer must be between 1.0 and 3.0")
        db.set_setting("rate_buffer", str(rate_buffer))
    if rate_mode in ("auto", "manual"):
        db.set_setting("rate_mode", rate_mode)
    if manual_rate is not None:
        if manual_rate < 100 or manual_rate > 10000:
            raise HTTPException(400, "manual_rate looks wrong (100..10000)")
        db.set_setting("manual_rate", str(manual_rate))
    if currency is not None:
        cur = currency.strip().upper()
        if cur not in ("NGN", "USD"):
            raise HTTPException(400, "currency must be NGN or USD")
        # This only controls what price customers SEE (packages list, this
        # panel). It does NOT change what Flutterwave actually charges —
        # see pay_currency below.
        db.set_setting("currency", cur)
    if pay_currency is not None:
        pcur = pay_currency.strip().upper()
        if pcur not in ("NGN", "USD"):
            raise HTTPException(400, "pay_currency must be NGN or USD")
        # What's ACTUALLY sent to Flutterwave for the real charge. Confirmed
        # live (2026-09-17): a USD-denominated Flutterwave checkout drops to
        # card-only — bank transfer/USSD/etc. disappear, since those rails
        # are NGN-only. Keep this NGN (default) to preserve every payment
        # method, even while displaying prices in USD above.
        db.set_setting("pay_currency", pcur)
    return pricing.pricing_snapshot()


@router.post("/overview")
def overview(key: str = Form(...), limit: int = Form(60)) -> dict:
    """Everything the owner needs on one screen: totals, accounts, recent activity."""
    _require_admin(key)
    return {
        "trial_days": db.trial_days(),
        "installer_url": db.get_setting("installer_url", ""),
        "installer_version": db.get_setting("installer_version", ""),
        "totals": db.totals(),
        "users": [
            {"email": r["email"], "credit_minutes": round(float(r["credit_seconds"]) / 60.0, 2),
             "created": float(r["created"]),
             "access_until": float(r["access_until"] or 0.0)}
            for r in db.all_users()
        ],
        "transactions": [
            {"email": r["email"], "kind": r["kind"], "seconds": float(r["seconds"]),
             "detail": r["detail"] or "", "created": float(r["created"]),
             "comped": bool(r["tx_ref"] and str(r["tx_ref"]).startswith("admin-"))}
            for r in db.recent_transactions(min(int(limit), 200))
        ],
    }


# --- Owner's control panel --------------------------------------------------
# A plain page so the owner can run the business from a phone or laptop instead
# of needing curl. The page holds NO secret: the admin key is typed by the owner
# and kept only in their own browser, so serving this HTML publicly is harmless —
# every action still has to pass the key check above.
PANEL = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apex Cam — Owner Panel</title><style>
*{box-sizing:border-box}
body{margin:0;padding:18px;background:#0f1115;color:#e8eaed;
  font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1200px;margin:0 auto}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}
h1{font-size:20px;margin:0}
.sub{color:#9aa0a6;font-size:13px;margin:0 0 16px}
.card{background:#171a21;border:1px solid #2a2f3a;border-radius:10px;padding:16px;
  margin-bottom:16px}
.card h2{font-size:14px;margin:0 0 12px;color:#9aa0a6;font-weight:600;
  text-transform:uppercase;letter-spacing:.5px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat{background:#12151b;border:1px solid #2a2f3a;border-radius:8px;padding:12px}
.stat .n{font-size:22px;font-weight:700} .stat .l{font-size:12px;color:#9aa0a6}
.gold{color:#d4af37} .green{color:#4ade80} .red{color:#f87171} .blue{color:#60a5fa}
label{display:block;margin:0 0 6px;font-size:12px;color:#9aa0a6}
/* font-size:16px on inputs is deliberate, not cosmetic — anything smaller
   makes iOS Safari auto-zoom the whole page on focus, which is what a lot
   of "the page is broken on my phone" reports actually turn out to be. */
input,select{width:100%;padding:12px;border-radius:8px;border:1px solid #2a2f3a;
  background:#12151b;color:#e8eaed;font-size:16px;min-height:44px}
.fields{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;align-items:end}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
button{padding:12px 18px;border:0;border-radius:8px;font-size:15px;font-weight:600;
  cursor:pointer;min-height:44px}
.grant{background:#d4af37;color:#1a1a1a} .sub2{background:#60a5fa;color:#0f1115}
.check{background:#2a2f3a;color:#e8eaed} .ghost{background:transparent;
  border:1px solid #2a2f3a;color:#9aa0a6}
#out{margin-top:12px;padding:12px;border-radius:8px;background:#12151b;
  border:1px solid #2a2f3a;white-space:pre-wrap;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:#9aa0a6;font-size:12px;font-weight:600;padding:8px;
  border-bottom:1px solid #2a2f3a;text-transform:uppercase}
td{padding:8px;border-bottom:1px solid #1e222b}
tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:12px}
.p-topup{background:#14532d;color:#4ade80} .p-spend{background:#3f1d1d;color:#f87171}
.p-refund{background:#1e3a5f;color:#60a5fa} .p-subscription{background:#3f3416;color:#d4af37}
.muted{color:#6b7280} .right{text-align:right}
@media(max-width:700px){.fields{grid-template-columns:1fr}}
</style></head><body><div class="wrap">

<header><h1>Apex Cam — Owner Panel</h1>
<button class="ghost" onclick="load()">↻ Refresh</button></header>
<p class="sub">Your key is stored only in this browser. Nothing here is on the server.</p>

<div class="card">
  <h2>Admin key</h2>
  <input id="k" type="password" placeholder="APEXCAM_ADMIN_KEY" autocomplete="off">
</div>

<div class="card"><h2>Business at a glance</h2><div class="stats" id="stats">
  <div class="stat"><div class="n muted">—</div><div class="l">load with your key</div></div>
</div></div>

<div class="card">
  <h2>Actions</h2>
  <div class="fields">
    <div><label>Customer email</label>
      <input id="e" type="email" placeholder="customer@example.com"></div>
    <div><label>Minutes</label><input id="m" type="number" value="6" min="0.1" step="0.1"></div>
    <div><label>Sub days</label><input id="d" type="number" value="30" min="1" step="1"></div>
  </div>
  <div class="btns">
    <button class="check" onclick="go('balance')">Check balance</button>
    <button class="grant" onclick="go('grant')">Grant credit (minutes)</button>
    <button class="sub2" onclick="go('subscription')">Extend subscription (days)</button>
  </div>
  <div id="out">Enter your key, then pick an action.</div>
</div>

<div class="card">
  <h2>Settings</h2>
  <div class="fields">
    <div><label>Free trial for NEW signups (days)</label>
      <input id="trial" type="number" min="0" max="365" step="1" placeholder="7"></div>
    <div style="align-self:end"><button class="sub2" onclick="saveTrial()">Save trial length</button></div>
    <div></div>
  </div>
  <p class="sub" style="margin:10px 0 0">Only affects people who sign up after you save.
    To give an existing customer more time, use "Extend subscription" above.</p>
</div>

<div class="card">
  <h2>Download page (/download)</h2>
  <div class="fields">
    <div style="grid-column:1/-1"><label>Installer link (https://… to ApexCam-Setup.exe)</label>
      <input id="instUrl" type="url" placeholder="https://github.com/…/releases/download/…/ApexCam-Setup.exe"></div>
    <div><label>Version label shown on the page</label>
      <input id="instVer" type="text" maxlength="40" placeholder="1.0"></div>
    <div style="align-self:end"><button class="sub2" onclick="saveInstaller()">Save download link</button></div>
  </div>
  <p class="sub" style="margin:10px 0 0">Leave the link empty and the page shows "coming soon"
    instead of a dead button.</p>
</div>

<div class="card">
  <h2>Pricing (dollar rate &amp; profit)</h2>
  <div class="stats" id="pstats">
    <div class="stat"><div class="n muted">—</div><div class="l">load with your key</div></div>
  </div>
  <div class="fields" style="margin-top:14px">
    <div><label>Show prices to customers in</label>
      <select id="curr"><option value="USD">USD ($)</option>
        <option value="NGN">NGN (₦)</option></select></div>
    <div><label>Profit multiplier (sell = Decart cost × this)</label>
      <input id="margin" type="number" min="1" max="5" step="0.05" placeholder="1.3"></div>
    <div><label>Rate mode</label>
      <select id="rmode"><option value="auto">Auto (live rate × buffer)</option>
        <option value="manual">Manual</option></select></div>
  </div>
  <div class="fields" style="margin-top:12px">
    <div id="bufwrap"><label>Rate buffer (× live rate)</label>
      <input id="rbuf" type="number" min="1" max="3" step="0.01" placeholder="1.18"></div>
    <div id="manwrap" style="display:none"><label>Manual rate (₦ per $)</label>
      <input id="mrate" type="number" min="100" max="10000" step="1" placeholder="1650"></div>
    <div><label>Actually charge via Flutterwave in</label>
      <select id="paycurr"><option value="NGN">NGN — keeps bank transfer/USSD</option>
        <option value="USD">USD — card only</option></select></div>
  </div>
  <p class="sub" style="margin:8px 0 0">A USD Flutterwave checkout only offers card payment —
    bank transfer, USSD and other local rails disappear. Keep this on NGN so every payment
    method still works, even while prices are shown to customers in dollars above.</p>
  <div class="fields" style="margin-top:12px">
    <div style="align-self:end"><button class="grant" onclick="savePricing()">Save pricing</button></div>
  </div>
  <p class="sub" style="margin:10px 0 0">Switching to USD only actually charges customers in USD
    if your Flutterwave account is enabled for USD settlement — that's set on Flutterwave's side,
    not here. NGN still needs the rate mode/buffer below; USD ignores them (no conversion needed).</p>
  <div class="scroll" style="margin-top:14px">
    <table><thead><tr><th>Package</th><th class="right">Customer pays</th>
    <th class="right">Decart cost</th><th class="right">Your profit</th></tr></thead>
    <tbody id="pkgs"><tr><td colspan="4" class="muted">—</td></tr></tbody></table>
  </div>
  <p class="sub" style="margin:10px 0 0">Prices update instantly for every customer — no reinstall.
    You can never sell below Decart's cost.</p>
</div>

<div class="card">
  <h2>Credits (Lucy Image)</h2>
  <div class="stats" id="imgstats">
    <div class="stat"><div class="n muted">—</div><div class="l">load with your key</div></div>
  </div>
  <div class="fields" style="margin-top:14px">
    <div><label>Price per credit (USD)</label>
      <input id="creditusd" type="number" min="0.01" max="5" step="0.01" placeholder="0.05"></div>
    <div style="align-self:end"><button class="grant" onclick="saveImagePricing()">Save credit price</button></div>
    <div></div>
  </div>
  <p class="sub" style="margin:10px 0 0">Customers see "N credits" — never a raw $ or ₦ amount — for a
    Lucy Image generation (today: <span id="imgcredits">—</span> credits/image, set in code). Deducted
    from the same balance as live minutes, converted at whatever the live rate is worth right now.
    The price per credit is fixed in dollars, independent of the live-minute margin above, so tuning
    one never silently moves the other.</p>
</div>

<div class="card">
  <h2>Voice Note pricing</h2>
  <div class="stats" id="vnstats">
    <div class="stat"><div class="n muted">—</div><div class="l">load with your key</div></div>
  </div>
  <div class="fields" style="margin-top:14px">
    <div><label>Characters per block</label>
      <input id="vnchars" type="number" min="10" max="5000" step="1" placeholder="250"></div>
    <div><label>Credits per block</label>
      <input id="vncredits" type="number" min="1" max="100" step="1" placeholder="4"></div>
    <div style="align-self:end"><button class="grant" onclick="saveVoiceNotePricing()">Save</button></div>
  </div>
  <p class="sub" style="margin:10px 0 0">A message is billed in whole blocks, rounded up — a 1-character
    message still costs a full block. Uses the SAME price-per-credit as Lucy Image above (change it
    there to move both together); these two fields only control how many credits one block of Voice
    Note costs.</p>
</div>

<div class="card">
  <h2>Per-model pricing (video jobs + cloud voice)</h2>
  <div class="scroll">
    <table><thead><tr><th>Model</th><th class="right">Cost/sec</th>
    <th class="right">Margin</th><th class="right">Sell/sec</th>
    <th class="right">Profit</th><th></th></tr></thead>
    <tbody id="modeRows"><tr><td colspan="6" class="muted">—</td></tr></tbody></table>
  </div>
  <p class="sub" style="margin:10px 0 0">Each model — Lucy Video, Lucy Restyle, Cloud Voice — has its
    OWN margin, priced independently of Lucy Realtime above. A restyle job's final charge is its video
    length x this rate; Cloud Voice bills per second while a session is actually streaming (heartbeat-
    metered, same pattern as Lucy Realtime) — all billed from the same minutes balance as everything
    else.</p>
</div>

<div class="card"><h2>Accounts</h2><div class="scroll">
  <table><thead><tr><th>Email</th><th class="right">Credit</th>
  <th>App access</th><th>Joined</th></tr></thead>
  <tbody id="users"><tr><td colspan="4" class="muted">—</td></tr></tbody></table>
</div></div>

<div class="card"><h2>Recent activity</h2><div class="scroll">
  <table><thead><tr><th>When</th><th>Email</th><th>Type</th>
  <th class="right">Amount</th><th>Detail</th></tr></thead>
  <tbody id="tx"><tr><td colspan="5" class="muted">—</td></tr></tbody></table>
</div></div>

</div><script>
const $=i=>document.getElementById(i), out=$('out');
$('k').value = localStorage.getItem('apexAdminKey') || '';
$('k').oninput = e => { localStorage.setItem('apexAdminKey', e.target.value.trim()); load(); };
const esc = s => String(s==null?'':s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const when = s => s ? new Date(s*1000).toLocaleString() : '—';
const mins = s => (s/60).toFixed(1);

function stat(n, l, cls){ return '<div class="stat"><div class="n '+(cls||'')+'">'+n+
  '</div><div class="l">'+l+'</div></div>'; }

async function post(action, extra){
  const key = $('k').value.trim();
  if(!key) throw new Error('Enter your admin key first.');
  const f = new FormData(); f.append('key', key);
  for(const k in (extra||{})) f.append(k, extra[k]);
  // A slow/flaky mobile connection can otherwise hang forever with no
  // feedback at all ("not answering") — force a clear timeout instead.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  let r;
  try{
    r = await fetch('/admin/'+action, {method:'POST', body:f, signal: ctrl.signal});
  }catch(err){
    if(err.name === 'AbortError') throw new Error('Timed out — check your connection and try again.');
    throw new Error('Network error — check your connection and try again.');
  }finally{
    clearTimeout(timer);
  }
  const d = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(r.status===403 ? 'Wrong admin key.'
    : (d.detail || ('Error '+r.status)));
  return d;
}

async function load(){
  if(!$('k').value.trim()) return;
  try{
    const d = await post('overview', {limit:60});
    const t = d.totals;
    $('stats').innerHTML =
      stat(t.users, 'accounts', 'blue') +
      stat(t.subscribers, 'active app subs', 'gold') +
      stat(mins(t.outstanding_seconds), 'minutes owed to customers') +
      stat(mins(t.paid_seconds), 'minutes sold (paid)', 'green') +
      stat(mins(t.granted_seconds), 'minutes comped (free)', 'gold') +
      stat(mins(t.used_seconds), 'minutes used', 'red');
    if(document.activeElement !== $('trial')) $('trial').value = d.trial_days;
    if(document.activeElement !== $('instUrl')) $('instUrl').value = d.installer_url || '';
    if(document.activeElement !== $('instVer')) $('instVer').value = d.installer_version || '';
    const now = Date.now()/1000;
    $('users').innerHTML = d.users.length ? d.users.map(u =>
      '<tr><td>'+esc(u.email)+'</td><td class="right">'+u.credit_minutes.toFixed(1)+
      ' min</td><td>'+(u.access_until>now
        ? '<span class="pill p-topup">until '+when(u.access_until)+'</span>'
        : '<span class="muted">expired</span>')+
      '</td><td class="muted">'+when(u.created)+'</td></tr>').join('')
      : '<tr><td colspan="4" class="muted">No accounts yet</td></tr>';
    $('tx').innerHTML = d.transactions.length ? d.transactions.map(x =>
      '<tr><td class="muted">'+when(x.created)+'</td><td>'+esc(x.email)+
      '</td><td><span class="pill p-'+esc(x.kind)+'">'+esc(x.kind)+
      (x.comped?' (free)':'')+'</span></td><td class="right">'+
      (x.kind==='subscription' ? (x.seconds/86400).toFixed(0)+' days'
                               : mins(x.seconds)+' min')+
      '</td><td class="muted">'+esc(x.detail)+'</td></tr>').join('')
      : '<tr><td colspan="5" class="muted">No activity yet</td></tr>';
    renderPricing(await post('pricing', {}));
  }catch(err){ out.className=''; out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}

async function go(action){
  const email = $('e').value.trim();
  if(!email){ out.innerHTML='<span class="red">Enter a customer email.</span>'; return; }
  out.textContent='Working…';
  try{
    let d, msg;
    if(action==='grant'){
      d = await post('grant', {email, minutes:$('m').value});
      msg = 'Granted '+d.granted_minutes+' min to '+d.email+
            ' — balance now '+d.credit_minutes+' minutes.';
    } else if(action==='subscription'){
      d = await post('subscription', {email, days:$('d').value});
      msg = 'Extended '+d.email+' by '+d.granted_days+' days — app access until '+
            when(d.access_until)+'.';
    } else {
      d = await post('balance', {email});
      msg = d.email+' has '+d.credit_minutes+' minutes ('+
            Math.round(d.credit_seconds)+' seconds).';
    }
    out.innerHTML = '<span class="green">'+esc(msg)+'</span>';
    load();
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}

async function saveInstaller(){
  out.textContent='Working…';
  try{
    const d = await post('settings', {installer_url:$('instUrl').value, installer_version:$('instVer').value});
    out.innerHTML = '<span class="green">'+(d.installer_url
      ? 'Download page now points to your installer.' : 'Download link cleared — page shows "coming soon".')+'</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}

async function saveTrial(){
  out.textContent='Working…';
  try{
    const d = await post('settings', {trial_days:$('trial').value});
    out.innerHTML = '<span class="green">New signups now get a '+d.trial_days+
      '-day free trial.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}

let currentCurrency = 'NGN';
const money = n => currentCurrency==='USD'
  ? '$'+Number(n).toFixed(2)
  : '₦'+Math.round(n).toLocaleString();
const MODE_LABELS = {video:'Lucy Video', restyle:'Lucy Restyle', cloud_voice:'Cloud Voice'};
function renderPricing(p){
  currentCurrency = p.currency;
  const active = document.activeElement;
  if(active!==$('curr'))   $('curr').value   = p.currency;
  if(active!==$('paycurr')) $('paycurr').value = p.pay_currency;
  if(active!==$('margin')) $('margin').value = p.margin;
  if(active!==$('rbuf'))   $('rbuf').value   = p.rate_buffer;
  if(active!==$('mrate'))  $('mrate').value  = p.manual_rate;
  $('rmode').value = p.rate_mode;
  $('manwrap').style.display = p.rate_mode==='manual' ? '' : 'none';
  $('bufwrap').style.display = p.rate_mode==='manual' ? 'none' : '';
  $('pstats').innerHTML =
    stat('$'+p.live_cost_usd_per_min, 'Live (fal) cost / min', 'red') +
    stat(money(p.live_rate), 'live $ rate', 'blue') +
    stat(money(p.effective_rate), 'rate used', 'gold') +
    stat(p.profit_pct+'%', 'your profit', 'green');
  $('pkgs').innerHTML = p.packages.map(k =>
    '<tr><td>'+k.minutes+' min</td><td class="right">'+money(k.charge)+
    '</td><td class="right muted">'+money(k.cost)+
    '</td><td class="right green">'+money(k.profit)+'</td></tr>').join('');
  const activeImg = document.activeElement;
  if(activeImg!==$('creditusd')) $('creditusd').value = p.credit_usd;
  $('imgcredits').textContent = p.image_credits;
  $('imgstats').innerHTML =
    stat('$'+p.image_cost_usd, 'Decart cost / image', 'red') +
    stat('$'+p.credit_usd, '1 credit', 'gold') +
    stat(p.image_credits+' credits', 'per image', 'blue') +
    stat(money(p.image_charge), 'customer pays', 'blue') +
    stat(p.image_profit_pct+'%', 'your profit', 'green');
  const activeVn = document.activeElement;
  if(activeVn!==$('vnchars'))   $('vnchars').value   = p.voicenote_chars_per_block;
  if(activeVn!==$('vncredits')) $('vncredits').value = p.voicenote_credits_per_block;
  $('vnstats').innerHTML =
    stat('$'+p.voicenote_cost_usd_per_block, 'fal cost / block', 'red') +
    stat(p.voicenote_credits_per_block+' credits', 'per block', 'gold') +
    stat(p.voicenote_chars_per_block+' chars', 'per block', 'blue') +
    stat(money(p.voicenote_charge_per_block), 'customer pays / block', 'blue') +
    stat(p.voicenote_profit_pct+'%', 'your profit', 'green');
  $('modeRows').innerHTML = ['video','restyle','cloud_voice'].map(m => {
    const d = p.modes[m], id = 'margin_'+m;
    return '<tr><td>'+MODE_LABELS[m]+'</td><td class="right muted">$'+d.cost_usd_per_sec+'</td>'+
      '<td class="right"><input id="'+id+'" type="number" min="1" max="10" step="0.05" '+
      'value="'+d.margin+'" style="width:70px"></td>'+
      '<td class="right">$'+d.sell_usd_per_sec+'/s</td>'+
      '<td class="right green">'+d.profit_pct+'%</td>'+
      '<td><button class="grant" onclick="saveModeMargin(\''+m+'\')">Save</button></td></tr>';
  }).join('');
}
$('rmode').onchange = () => {
  $('manwrap').style.display = $('rmode').value==='manual' ? '' : 'none';
  $('bufwrap').style.display = $('rmode').value==='manual' ? 'none' : '';
};
async function savePricing(){
  out.textContent='Working…';
  try{
    const d = await post('pricing', {margin:$('margin').value, rate_buffer:$('rbuf').value,
      rate_mode:$('rmode').value, manual_rate:$('mrate').value||1650, currency:$('curr').value,
      pay_currency:$('paycurr').value});
    renderPricing(d);
    out.innerHTML = '<span class="green">Pricing saved — live for all customers. '+
      'Profit '+d.profit_pct+'%, rate used '+money(d.effective_rate)+'/$.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}
async function saveImagePricing(){
  out.textContent='Working…';
  try{
    const d = await post('pricing', {credit_usd:$('creditusd').value});
    renderPricing(d);
    out.innerHTML = '<span class="green">Credit price saved — $'+d.credit_usd+
      '/credit ('+money(d.image_charge)+' per image), profit '+d.image_profit_pct+'%.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}
async function saveVoiceNotePricing(){
  out.textContent='Working…';
  try{
    const d = await post('pricing', {voicenote_chars_per_block:$('vnchars').value,
      voicenote_credits_per_block:$('vncredits').value});
    renderPricing(d);
    out.innerHTML = '<span class="green">Voice Note pricing saved — '+
      d.voicenote_credits_per_block+' credits per '+d.voicenote_chars_per_block+
      ' characters ('+money(d.voicenote_charge_per_block)+'/block), profit '+
      d.voicenote_profit_pct+'%.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}
async function saveModeMargin(mode){
  out.textContent='Working…';
  try{
    const extra = {}; extra['margin_'+mode] = $('margin_'+mode).value;
    const d = await post('pricing', extra);
    renderPricing(d);
    const md = d.modes[mode];
    out.innerHTML = '<span class="green">'+MODE_LABELS[mode]+' saved — $'+md.sell_usd_per_sec+
      '/sec, profit '+md.profit_pct+'%.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}
load();
</script></body></html>"""


@router.get("/panel", response_class=HTMLResponse)
def panel() -> HTMLResponse:
    """The owner's control panel (the key is entered in-browser, never stored here)."""
    return HTMLResponse(PANEL)
