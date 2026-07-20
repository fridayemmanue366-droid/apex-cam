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


# --- Owner's control panel --------------------------------------------------
# A plain page so the owner can run the business from a phone or laptop instead
# of needing curl. The page holds NO secret: the admin key is typed by the owner
# and kept only in their own browser, so serving this HTML publicly is harmless —
# every action still has to pass the key check above.
PANEL = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apex Cam — Owner Panel</title><style>
*{box-sizing:border-box} body{margin:0;padding:20px;background:#0f1115;color:#e8eaed;
font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:520px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px} .sub{color:#9aa0a6;font-size:13px;margin:0 0 20px}
label{display:block;margin:14px 0 6px;font-size:13px;color:#9aa0a6}
input{width:100%;padding:12px;border-radius:8px;border:1px solid #2a2f3a;
background:#171a21;color:#e8eaed;font-size:16px}
.row{display:flex;gap:10px;margin-top:18px} button{flex:1;padding:13px;border:0;
border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}
.grant{background:#d4af37;color:#1a1a1a} .check{background:#2a2f3a;color:#e8eaed}
button:disabled{opacity:.5;cursor:default}
#out{margin-top:18px;padding:14px;border-radius:8px;background:#171a21;
border:1px solid #2a2f3a;white-space:pre-wrap;font-size:14px;min-height:20px}
.ok{color:#4ade80} .err{color:#f87171}
</style></head><body><div class="wrap">
<h1>Apex Cam — Owner Panel</h1>
<p class="sub">Grant credit and check balances. Your key stays in this browser only.</p>
<label>Admin key</label><input id="k" type="password" placeholder="your APEXCAM_ADMIN_KEY">
<label>Customer email</label><input id="e" type="email" placeholder="customer@example.com">
<label>Minutes to grant</label><input id="m" type="number" value="6" min="0.1" step="0.1">
<div class="row">
  <button class="check" onclick="go('balance')">Check balance</button>
  <button class="grant" onclick="go('grant')">Grant credit</button>
</div>
<div id="out">Enter the key and an email, then choose an action.</div>
</div><script>
const $=i=>document.getElementById(i), out=$('out');
$('k').value = localStorage.getItem('apexAdminKey') || '';
$('k').oninput = e => localStorage.setItem('apexAdminKey', e.target.value.trim());
async function go(action){
  const key=$('k').value.trim(), email=$('e').value.trim(), minutes=$('m').value;
  if(!key||!email){ out.className='err'; out.textContent='Enter the admin key and an email.'; return; }
  out.className=''; out.textContent='Working…';
  const f=new FormData(); f.append('key',key); f.append('email',email);
  if(action==='grant') f.append('minutes',minutes);
  try{
    const r=await fetch('/admin/'+action,{method:'POST',body:f});
    const d=await r.json();
    if(!r.ok){
      out.className='err';
      out.textContent = r.status===403 ? 'Wrong admin key.'
        : r.status===404 ? (d.detail||'Not found')
        : (d.detail||('Error '+r.status));
      return;
    }
    out.className='ok';
    out.textContent = (action==='grant'
      ? 'Granted '+d.granted_minutes+' min to '+d.email+'\\n'
      : 'Account: '+d.email+'\\n')
      + 'Balance now: '+d.credit_minutes+' minutes ('+Math.round(d.credit_seconds)+' seconds)';
  }catch(err){ out.className='err'; out.textContent='Network error: '+err.message; }
}
</script></body></html>"""


@router.get("/panel", response_class=HTMLResponse)
def panel() -> HTMLResponse:
    """The owner's control panel (the key is entered in-browser, never stored here)."""
    return HTMLResponse(PANEL)
