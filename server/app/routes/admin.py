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
def settings(key: str = Form(...), trial_days: float | None = Form(None)) -> dict:
    """Read or change owner settings. Currently the free-trial length for NEW
    signups — editable here so it never needs a Render restart. Existing customers
    are unaffected; extend them individually with the subscription action."""
    _require_admin(key)
    if trial_days is not None:
        if trial_days < 0 or trial_days > 365:
            raise HTTPException(400, "trial_days must be between 0 and 365")
        db.set_setting("trial_days", str(trial_days))
    return {"trial_days": db.trial_days()}


@router.post("/pricing")
def pricing_admin(key: str = Form(...),
                  margin: float | None = Form(None),
                  image_margin: float | None = Form(None),
                  rate_buffer: float | None = Form(None),
                  rate_mode: str | None = Form(None),
                  manual_rate: float | None = Form(None)) -> dict:
    """Read or change pricing knobs live — margin, the rate buffer, and whether the
    dollar rate auto-tracks the live market or is set by hand. Returns a full
    snapshot (cost, live rate, effective rate, and every package's price + profit)."""
    _require_admin(key)
    from app import pricing
    if margin is not None:
        if margin < 1.0 or margin > 5.0:
            raise HTTPException(400, "margin must be between 1.0 and 5.0")
        db.set_setting("pricing_margin", str(margin))
    if image_margin is not None:
        if image_margin < 1.0 or image_margin > 10.0:
            raise HTTPException(400, "image_margin must be between 1.0 and 10.0")
        db.set_setting("image_margin", str(image_margin))
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
    return pricing.pricing_snapshot()


@router.post("/overview")
def overview(key: str = Form(...), limit: int = Form(60)) -> dict:
    """Everything the owner needs on one screen: totals, accounts, recent activity."""
    _require_admin(key)
    return {
        "trial_days": db.trial_days(),
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
input{width:100%;padding:10px;border-radius:8px;border:1px solid #2a2f3a;
  background:#12151b;color:#e8eaed;font-size:15px}
.fields{display:grid;grid-template-columns:2fr 1fr 1fr;gap:12px;align-items:end}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
button{padding:11px 16px;border:0;border-radius:8px;font-size:14px;font-weight:600;
  cursor:pointer}
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
  <h2>Pricing (dollar rate &amp; profit)</h2>
  <div class="stats" id="pstats">
    <div class="stat"><div class="n muted">—</div><div class="l">load with your key</div></div>
  </div>
  <div class="fields" style="margin-top:14px">
    <div><label>Profit multiplier (sell = Decart cost × this)</label>
      <input id="margin" type="number" min="1" max="5" step="0.05" placeholder="1.3"></div>
    <div><label>Rate mode</label>
      <select id="rmode"><option value="auto">Auto (live rate × buffer)</option>
        <option value="manual">Manual</option></select></div>
    <div id="bufwrap"><label>Rate buffer (× live rate)</label>
      <input id="rbuf" type="number" min="1" max="3" step="0.01" placeholder="1.18"></div>
  </div>
  <div class="fields" style="margin-top:12px">
    <div id="manwrap" style="display:none"><label>Manual rate (₦ per $)</label>
      <input id="mrate" type="number" min="100" max="10000" step="1" placeholder="1650"></div>
    <div style="align-self:end"><button class="grant" onclick="savePricing()">Save pricing</button></div>
    <div></div>
  </div>
  <div class="scroll" style="margin-top:14px">
    <table><thead><tr><th>Package</th><th class="right">Customer pays</th>
    <th class="right">Decart cost</th><th class="right">Your profit</th></tr></thead>
    <tbody id="pkgs"><tr><td colspan="4" class="muted">—</td></tr></tbody></table>
  </div>
  <p class="sub" style="margin:10px 0 0">Prices update instantly for every customer — no reinstall.
    You can never sell below Decart's cost.</p>
</div>

<div class="card">
  <h2>Lucy Image pricing (per-image, separate from live-minute pricing)</h2>
  <div class="stats" id="imgstats">
    <div class="stat"><div class="n muted">—</div><div class="l">load with your key</div></div>
  </div>
  <div class="fields" style="margin-top:14px">
    <div><label>Profit multiplier (sell = image cost × this)</label>
      <input id="imgmargin" type="number" min="1" max="10" step="0.05" placeholder="1.75"></div>
    <div style="align-self:end"><button class="grant" onclick="saveImagePricing()">Save image pricing</button></div>
    <div></div>
  </div>
  <p class="sub" style="margin:10px 0 0">Deducted from the same credit balance as live minutes —
    each image just converts to however many minutes that price is worth right now.</p>
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
  const r = await fetch('/admin/'+action, {method:'POST', body:f});
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

async function saveTrial(){
  out.textContent='Working…';
  try{
    const d = await post('settings', {trial_days:$('trial').value});
    out.innerHTML = '<span class="green">New signups now get a '+d.trial_days+
      '-day free trial.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}

const money = n => '₦'+Math.round(n).toLocaleString();
function renderPricing(p){
  const active = document.activeElement;
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
    '<tr><td>'+k.minutes+' min</td><td class="right">'+money(k.ngn)+
    '</td><td class="right muted">'+money(k.cost_ngn)+
    '</td><td class="right green">'+money(k.profit_ngn)+'</td></tr>').join('');
  const activeImg = document.activeElement;
  if(activeImg!==$('imgmargin')) $('imgmargin').value = p.image_margin;
  $('imgstats').innerHTML =
    stat('$'+p.image_cost_usd, 'Decart cost / image', 'red') +
    stat('$'+p.image_sell_usd, 'sell / image', 'gold') +
    stat(money(p.image_charge), 'customer pays', 'blue') +
    stat(p.image_profit_pct+'%', 'your profit', 'green');
}
$('rmode').onchange = () => {
  $('manwrap').style.display = $('rmode').value==='manual' ? '' : 'none';
  $('bufwrap').style.display = $('rmode').value==='manual' ? 'none' : '';
};
async function savePricing(){
  out.textContent='Working…';
  try{
    const d = await post('pricing', {margin:$('margin').value, rate_buffer:$('rbuf').value,
      rate_mode:$('rmode').value, manual_rate:$('mrate').value||1650});
    renderPricing(d);
    out.innerHTML = '<span class="green">Pricing saved — live for all customers. '+
      'Profit '+d.profit_pct+'%, rate used '+money(d.effective_rate)+'/$.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}
async function saveImagePricing(){
  out.textContent='Working…';
  try{
    const d = await post('pricing', {image_margin:$('imgmargin').value});
    renderPricing(d);
    out.innerHTML = '<span class="green">Image pricing saved — $'+d.image_sell_usd+
      '/image ('+money(d.image_charge)+'), profit '+d.image_profit_pct+'%.</span>';
  }catch(err){ out.innerHTML='<span class="red">'+esc(err.message)+'</span>'; }
}
load();
</script></body></html>"""


@router.get("/panel", response_class=HTMLResponse)
def panel() -> HTMLResponse:
    """The owner's control panel (the key is entered in-browser, never stored here)."""
    return HTMLResponse(PANEL)
