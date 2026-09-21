"""Public, mobile-friendly web pages — no desktop app needed. Plain HTML/JS
served directly (same pattern as admin.py's owner panel: no build step, no
bundler), so publishing a change here is just editing a string and pushing.

Today: Voice Note only (record/upload a reference voice, type a message,
get an audio file) -- the feature customers specifically asked to have on
their phones, since it's desktop-app-only otherwise. Uses the SAME account
system and SAME /studio/voicenote/* routes the desktop app already uses --
nothing here is a separate backend, just a different front door to it.
"""
from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import db

router = APIRouter(tags=["web"])

VOICENOTE_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apex Cam — Voice Note</title><style>
*{box-sizing:border-box}
body{margin:0;padding:0;background:#0f1115;color:#e8eaed;
  font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
  min-height:100vh}
.wrap{max-width:480px;margin:0 auto;padding:18px 18px 60px}
header{text-align:center;margin:8px 0 20px}
header h1{font-size:20px;margin:0 0 4px}
header p{color:#9aa0a6;font-size:13px;margin:0}
.card{background:#171a21;border:1px solid #2a2f3a;border-radius:12px;
  padding:18px;margin-bottom:16px}
.card h2{font-size:14px;margin:0 0 12px;color:#9aa0a6;font-weight:600;
  text-transform:uppercase;letter-spacing:.5px}
label{display:block;margin:0 0 6px;font-size:12px;color:#9aa0a6}
input,textarea,select{width:100%;padding:12px;border-radius:8px;
  border:1px solid #2a2f3a;background:#12151b;color:#e8eaed;font-size:16px;
  min-height:44px;font-family:inherit}
textarea{resize:vertical;min-height:90px}
.row{margin-bottom:12px}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin-top:6px}
button{padding:13px 18px;border:0;border-radius:8px;font-size:15px;
  font-weight:600;cursor:pointer;min-height:46px;width:100%}
button:disabled{opacity:.5;cursor:not-allowed}
.gold{background:#d4af37;color:#1a1a1a}
.blue{background:#60a5fa;color:#0f1115}
.ghost{background:transparent;border:1px solid #2a2f3a;color:#e8eaed}
.danger{background:#e66;color:#1a1a1a}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px}
.p-ok{background:#14532d;color:#4ade80} .p-live{background:#3f1d1d;color:#f87171}
.muted{color:#9aa0a6;font-size:13px}
.error{color:#f87171;font-size:14px;margin:8px 0}
.success{color:#4ade80;font-size:14px;margin:8px 0}
.tabs{display:flex;gap:8px;margin-bottom:14px}
.tab{flex:1;padding:10px;text-align:center;border-radius:8px;
  background:#12151b;border:1px solid #2a2f3a;color:#9aa0a6;cursor:pointer;
  font-size:13px}
.tab.active{background:#d4af37;color:#1a1a1a;border-color:#d4af37}
audio{width:100%;margin-top:10px}
.steps{margin:0;padding-left:20px;font-size:13px;color:#9aa0a6}
.steps li{margin-bottom:6px}
.hidden{display:none!important}
.balance{display:flex;justify-content:space-between;align-items:center}
.balance .n{font-size:20px;font-weight:700;color:#d4af37}
.pkg{display:flex;justify-content:space-between;align-items:center;
  padding:10px 0;border-bottom:1px solid #1e222b}
.pkg:last-child{border-bottom:0}
.pkg button{width:auto;padding:8px 16px;min-height:38px}
</style></head><body><div class="wrap">

<header>
  <h1>🎙️ Apex Cam — Voice Note</h1>
  <p>Clone any voice, type a message, get an audio file.</p>
</header>

<div id="authCard" class="card">
  <h2>Sign in</h2>
  <div class="tabs">
    <div class="tab active" id="tabLogin" onclick="setAuthTab('login')">Log in</div>
    <div class="tab" id="tabRegister" onclick="setAuthTab('register')">Sign up</div>
  </div>
  <div class="row"><label>Email</label><input id="email" type="email" autocomplete="email"></div>
  <div class="row"><label>Password</label><input id="password" type="password" autocomplete="current-password"></div>
  <div class="btns">
    <button class="gold" id="authBtn" onclick="doAuth()">Log in</button>
  </div>
  <div id="authMsg"></div>
</div>

<div id="mainCard" class="hidden">
  <div class="card">
    <div class="balance">
      <div><div class="muted">Signed in as <span id="who"></span></div>
        <div class="n" id="balanceN">—</div>
        <div class="muted" id="balanceCredits">—</div></div>
      <button class="ghost" style="width:auto" onclick="logout()">Log out</button>
    </div>
  </div>

  <div class="card" id="buyCard">
    <h2>Buy credit</h2>
    <div id="pkgs"><p class="muted">Loading packages…</p></div>
  </div>

  <div class="card">
    <h2>1. Reference voice — the voice to clone</h2>
    <div class="btns">
      <button class="ghost" id="recBtn" onclick="toggleRecording()">🎙 Record</button>
      <label class="ghost" style="text-align:center;display:block;cursor:pointer">
        📁 Upload a clip
        <input type="file" accept="audio/*" class="hidden" id="fileInput" onchange="onFilePicked(event)">
      </label>
    </div>
    <div id="refPreview"></div>
  </div>

  <div class="card">
    <h2>2. Message — what the voice should say</h2>
    <div class="row"><textarea id="text" maxlength="5000" placeholder="Type exactly what you want said…" oninput="updatePricing()"></textarea></div>
    <p class="muted"><span id="charCount">0</span> / <span id="maxChars">5000</span> characters</p>
    <button class="gold" id="genBtn" onclick="generate()" disabled>Generate — <span id="costLabel">—</span></button>
    <div id="genMsg"></div>
  </div>

  <div class="card hidden" id="resultCard">
    <h2>Result</h2>
    <audio id="resultAudio" controls></audio>
    <div class="btns">
      <a id="dlLink" class="ghost" style="text-align:center;display:block;text-decoration:none" download="apex-voice-note.ogg">⬇ Download</a>
    </div>
    <h2 style="margin-top:18px">Want it to show your photo on WhatsApp?</h2>
    <p class="muted">Attaching the file works and sounds great, but WhatsApp only shows your
      profile picture on a message actually RECORDED through its own mic button. To get that:</p>
    <ol class="steps">
      <li>Open WhatsApp and the chat you want to send to</li>
      <li>Press and HOLD the microphone button (don't tap — hold it)</li>
      <li>While holding it, come back here and press play above, right next to your phone</li>
      <li>When it finishes, let go — that sends it</li>
    </ol>
    <p class="muted">Note: your phone will pause other audio while WhatsApp is recording — that's
      normal and expected, not a bug.</p>
  </div>
</div>

</div><script>
const $=id=>document.getElementById(id);
const API = location.origin;
let authMode = 'login';

function setAuthTab(mode){
  authMode = mode;
  $('tabLogin').className = mode==='login' ? 'tab active' : 'tab';
  $('tabRegister').className = mode==='register' ? 'tab active' : 'tab';
  $('authBtn').textContent = mode==='login' ? 'Log in' : 'Sign up';
}

function getToken(){ return localStorage.getItem('apexToken') || ''; }
function setToken(t){ if(t) localStorage.setItem('apexToken', t); else localStorage.removeItem('apexToken'); }

async function api(path, opts){
  opts = opts || {};
  const headers = opts.headers || {};
  const token = getToken();
  if(token) headers['Authorization'] = 'Bearer ' + token;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 20000);
  let r;
  try{
    r = await fetch(API + path, {...opts, headers, signal: ctrl.signal});
  }catch(err){
    throw new Error(err.name==='AbortError' ? 'Timed out — check your connection.' : 'Network error — check your connection.');
  }finally{ clearTimeout(timer); }
  if(r.status === 401){ setToken(null); showAuth(); throw new Error('Session expired — sign in again.'); }
  if(!r.ok){
    const d = await r.json().catch(()=>({}));
    throw new Error(d.detail || ('Request failed (' + r.status + ')'));
  }
  return r;
}
async function apiJson(path, opts){ return (await api(path, opts)).json(); }

async function apiBlob(path){
  const r = await api(path);
  return URL.createObjectURL(await r.blob());
}

function showAuth(){
  $('authCard').classList.remove('hidden');
  $('mainCard').classList.add('hidden');
}
function showMain(){
  $('authCard').classList.add('hidden');
  $('mainCard').classList.remove('hidden');
}

async function doAuth(){
  const email = $('email').value.trim();
  const password = $('password').value;
  $('authMsg').innerHTML = '';
  if(!email || !password){ $('authMsg').innerHTML = '<p class="error">Enter your email and password.</p>'; return; }
  try{
    const d = await apiJson('/auth/' + authMode, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({email, password})
    });
    setToken(d.token);
    await afterSignIn();
  }catch(err){
    $('authMsg').innerHTML = '<p class="error">' + err.message + '</p>';
  }
}
function logout(){ setToken(null); showAuth(); }

let account = null;
function renderBalance(){
  $('balanceN').textContent = account.minutes.toFixed(1) + ' min';
  $('balanceCredits').textContent = account.credits + ' credit' + (account.credits===1?'':'s') + ' worth';
}
async function afterSignIn(){
  account = await apiJson('/me');
  $('who').textContent = account.email;
  renderBalance();
  showMain();
  loadPackages();
  updatePricing();
}

async function loadPackages(){
  try{
    const pkgs = await apiJson('/pay/packages');
    $('pkgs').innerHTML = pkgs.map(p =>
      '<div class="pkg"><span>' + p.minutes + ' min — ' +
      (p.currency==='NGN' ? '₦'+Math.round(p.charge).toLocaleString() : '$'+p.usd.toFixed(2)) +
      ' <span class="muted">(≈' + p.credits + ' credit' + (p.credits===1?'':'s') + ')</span>' +
      '</span><button class="blue" onclick="buy(' + p.minutes + ')">Buy</button></div>'
    ).join('');
  }catch(err){ $('pkgs').innerHTML = '<p class="error">' + err.message + '</p>'; }
}
async function buy(minutes){
  try{
    const d = await apiJson('/pay/start', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({minutes})
    });
    window.open(d.link, '_blank');
  }catch(err){ $('pkgs').innerHTML = '<p class="error">' + err.message + '</p>'; }
}

// --- reference voice: record or upload --------------------------------
let mediaRecorder = null, recordedChunks = [], recording = false;
let refBlob = null;   // final 16-bit mono WAV, ready to upload
const MIN_REF_S = 10, MAX_REF_S = 120, TARGET_SR = 48000;

async function toggleRecording(){
  if(recording){
    mediaRecorder.stop();
    return;
  }
  try{
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if(e.data.size>0) recordedChunks.push(e.data); };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t=>t.stop());
      recording = false;
      $('recBtn').textContent = '🎙 Record';
      await setReference(new Blob(recordedChunks, {type: mediaRecorder.mimeType || 'audio/webm'}));
    };
    mediaRecorder.start();
    recording = true;
    $('recBtn').textContent = '■ Stop recording';
    setTimeout(() => { if(recording) mediaRecorder.stop(); }, MAX_REF_S*1000);
  }catch(err){
    $('refPreview').innerHTML = '<p class="error">Could not access the microphone.</p>';
  }
}
function onFilePicked(e){
  const f = e.target.files[0];
  if(f) setReference(f);
}

async function decodeToMonoPCM(blob){
  const buf = await blob.arrayBuffer();
  const AC = window.AudioContext || window.webkitAudioContext;
  const probe = new AC();
  let decoded;
  try{ decoded = await probe.decodeAudioData(buf.slice(0)); }
  finally{ await probe.close(); }
  const seconds = Math.min(decoded.duration, MAX_REF_S);
  const len = Math.max(1, Math.round(seconds*TARGET_SR));
  const offline = new OfflineAudioContext(1, len, TARGET_SR);
  const src = offline.createBufferSource();
  src.buffer = decoded; src.connect(offline.destination); src.start(0,0,seconds);
  const rendered = await offline.startRendering();
  return rendered.getChannelData(0).slice();
}
function encodeWav(samples, sr){
  sr = sr || TARGET_SR;
  const bytesPerSample = 2;
  const buffer = new ArrayBuffer(44 + samples.length*bytesPerSample);
  const view = new DataView(buffer);
  const ws = (o,s) => { for(let i=0;i<s.length;i++) view.setUint8(o+i, s.charCodeAt(i)); };
  ws(0,'RIFF'); view.setUint32(4, 36+samples.length*bytesPerSample, true); ws(8,'WAVE');
  ws(12,'fmt '); view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
  view.setUint32(24,sr,true); view.setUint32(28,sr*bytesPerSample,true);
  view.setUint16(32,bytesPerSample,true); view.setUint16(34,16,true);
  ws(36,'data'); view.setUint32(40,samples.length*bytesPerSample,true);
  let o=44;
  for(let i=0;i<samples.length;i++,o+=2){
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(o, s<0 ? s*0x8000 : s*0x7fff, true);
  }
  return new Blob([buffer], {type:'audio/wav'});
}
function rms(samples){
  if(!samples.length) return 0;
  let sum=0; for(let i=0;i<samples.length;i++) sum += samples[i]*samples[i];
  return Math.sqrt(sum/samples.length);
}
async function setReference(raw){
  $('refPreview').innerHTML = '<p class="muted">Processing…</p>';
  try{
    const pcm = await decodeToMonoPCM(raw);
    const seconds = pcm.length/TARGET_SR;
    if(seconds < MIN_REF_S){
      $('refPreview').innerHTML = '<p class="error">That clip is only ' + seconds.toFixed(0) +
        's — use at least ' + MIN_REF_S + 's for a good clone.</p>';
      return;
    }
    if(rms(pcm) < 0.002){
      $('refPreview').innerHTML = '<p class="error">That clip sounds silent — check your microphone.</p>';
      return;
    }
    refBlob = encodeWav(pcm);
    const url = URL.createObjectURL(refBlob);
    $('refPreview').innerHTML = '<p class="muted">Reference clip (' + seconds.toFixed(0) +
      's):</p><audio controls src="' + url + '"></audio>';
    updatePricing();
  }catch(err){
    $('refPreview').innerHTML = '<p class="error">Could not read that audio.</p>';
  }
}

// --- pricing + generate --------------------------------------------------
let pricingTimer = null;
function updatePricing(){
  const text = $('text').value;
  $('charCount').textContent = text.length;
  $('genBtn').disabled = !refBlob || !text.trim();
  if(pricingTimer) clearTimeout(pricingTimer);
  pricingTimer = setTimeout(async () => {
    try{
      const p = await apiJson('/studio/voicenote/pricing?chars=' + text.length);
      $('costLabel').textContent = p.credits + ' credit' + (p.credits===1?'':'s');
      $('maxChars').textContent = p.max_chars;
    }catch(err){ /* ignore -- not worth surfacing a background pricing refresh failure */ }
  }, 250);
}

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }
async function generate(){
  const text = $('text').value.trim();
  if(!refBlob || !text) return;
  $('genBtn').disabled = true;
  $('genMsg').innerHTML = '<p class="muted">Starting…</p>';
  $('resultCard').classList.add('hidden');
  try{
    const form = new FormData();
    form.append('reference', refBlob, 'reference.wav');
    form.append('text', text);
    const started = await apiJson('/studio/voicenote/start', {method:'POST', body: form});
    const jobId = started.job_id;
    const deadline = Date.now() + 10*60*1000;
    let status = 'processing', lastError = null;
    while(status === 'processing'){
      if(Date.now() > deadline) throw new Error('Taking too long — try again.');
      await sleep(1500);
      $('genMsg').innerHTML = '<p class="muted">Generating…</p>';
      const s = await apiJson('/studio/voicenote/' + jobId);
      status = s.status; lastError = s.error;
    }
    if(status === 'error') throw new Error(lastError || 'Could not generate that voice note.');
    const url = await apiBlob('/studio/voicenote/' + jobId + '/content');
    $('resultAudio').src = url;
    $('dlLink').href = url;
    $('resultCard').classList.remove('hidden');
    $('genMsg').innerHTML = '<p class="success">Done!</p>';
    account = await apiJson('/me');
    renderBalance();
  }catch(err){
    $('genMsg').innerHTML = '<p class="error">' + err.message + '</p>';
  }finally{
    $('genBtn').disabled = !refBlob || !text.trim();
  }
}

// --- boot ------------------------------------------------------------
if(getToken()){ afterSignIn().catch(() => showAuth()); }
else{ showAuth(); }
</script></body></html>"""


@router.get("/voicenote", response_class=HTMLResponse)
def voicenote_page() -> HTMLResponse:
    """The public, mobile-friendly Voice Note page. Same account system and
    same /studio/voicenote/* routes as the desktop app — this is only a
    different front door, not a separate backend."""
    return HTMLResponse(VOICENOTE_PAGE)


DOWNLOAD_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Download Apex Cam</title><style>
*{box-sizing:border-box}
body{margin:0;background:#0f1115;color:#e8eaed;
  font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:640px;margin:0 auto;padding:28px 18px 70px}
h1{font-size:30px;margin:8px 0 6px;text-align:center}
.tag{color:#9aa0a6;text-align:center;margin:0 0 26px}
.card{background:#171a21;border:1px solid #2a2f3a;border-radius:14px;
  padding:20px;margin-bottom:16px}
.card h2{font-size:13px;margin:0 0 12px;color:#9aa0a6;font-weight:600;
  text-transform:uppercase;letter-spacing:.6px}
.dl{text-align:center}
.btn{display:inline-block;background:#f4d06f;color:#1a1a1a;font-weight:700;
  font-size:18px;padding:16px 34px;border-radius:12px;text-decoration:none;
  min-height:48px}
.btn.off{background:#2a2f3a;color:#9aa0a6;cursor:default}
.meta{color:#9aa0a6;font-size:13px;margin-top:12px}
ol,ul{margin:0;padding-left:22px}
li{margin:7px 0}
.note{color:#9aa0a6;font-size:14px}
a{color:#f4d06f}
</style></head><body><div class="wrap">
<h1>Apex Cam</h1>
<p class="tag">Real-time face swap and AI camera for Windows</p>

<div class="card dl">
  {{BUTTON}}
  <div class="meta">{{META}}</div>
</div>

<div class="card">
  <h2>Install in 3 steps</h2>
  <ol>
    <li>Download <b>ApexCam-Setup.exe</b> and double-click it.</li>
    <li>Windows may warn about an unknown publisher: click <b>More info</b>, then <b>Run anyway</b>.</li>
    <li>Follow the installer, open Apex Cam, and create your account.{{TRIAL}}</li>
  </ol>
</div>

<div class="card">
  <h2>What you need</h2>
  <ul>
    <li>Windows 10 or 11 (64-bit)</li>
    <li>A webcam</li>
    <li>A few GB of free disk space</li>
    <li>An internet connection for the first setup (the AI models download after install)</li>
  </ul>
</div>

<div class="card">
  <h2>On your phone?</h2>
  <p class="note" style="margin:0">The Apex Cam desktop app is Windows only. You can still make cloned-voice
  audio notes right in your phone's browser: <a href="/voicenote">open Voice Note</a>.</p>
</div>
</div></body></html>"""


@router.get("/download", response_class=HTMLResponse)
def download_page() -> HTMLResponse:
    """Public download page. The installer itself (~460 MB) can't live on this
    server, so its link is an admin-panel setting (installer_url) -- until one
    is set the page shows "coming soon" rather than a dead button."""
    url = (db.get_setting("installer_url", "") or "").strip()
    ver = (db.get_setting("installer_version", "") or "").strip()
    if url.lower().startswith("https://"):
        button = '<a class="btn" href="%s" rel="noopener">Download for Windows</a>' % html.escape(url, quote=True)
        meta = ("Version %s &middot; " % html.escape(ver) if ver else "") + "Windows 10/11, 64-bit"
    else:
        button = '<span class="btn off">Download coming soon</span>'
        meta = "The installer is being finalized. Check back shortly."
    try:
        trial = float(db.trial_days())
    except Exception:
        trial = 0.0
    trial_txt = (" You get a <b>%g-day free trial</b>." % trial) if trial > 0 else ""
    page = (DOWNLOAD_PAGE.replace("{{BUTTON}}", button)
            .replace("{{META}}", meta).replace("{{TRIAL}}", trial_txt))
    return HTMLResponse(page)
