import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { cloud, signedIn, cachedActive, type SubStatus } from "../api/cloud";
import { api } from "../api/client";

/**
 * The ₦20,000/month subscription gates the LOCAL app only (Face, Voice, Camera,
 * etc.). Apex Pro is a completely separate product — it has its own sign-in and
 * runs on pay-per-call credits, so it is NEVER gated by this subscription. That's
 * why these screens render INLINE inside the local content area (not as a
 * full-screen overlay): the sidebar stays clickable so Apex Pro is always reachable.
 *
 * New accounts get a 1-day free trial; after it lapses the local tabs lock until a
 * ₦20,000 payment adds another 30 days (manual renewal). Access is decided by the
 * SERVER (users.access_until); we only cache the expiry for brief-offline grace.
 */

interface SubCtx {
  status: SubStatus | null;
  offline: boolean;
  loading: boolean;
  refresh: () => void;
}
const Ctx = createContext<SubCtx>({ status: null, offline: false, loading: false, refresh: () => {} });
export const useSubscription = () => useContext(Ctx);

/** Fetches subscription status ONCE and shares it, so switching local tabs doesn't
 *  re-hit the server or flicker. Always renders its children (it's just a provider). */
export function SubscriptionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<SubStatus | null>(null);
  const [offline, setOffline] = useState(false);
  const [loading, setLoading] = useState(signedIn());

  const refresh = useCallback(async () => {
    if (!signedIn()) { setStatus(null); setLoading(false); return; }
    setLoading(true);
    try {
      const s = await cloud.subscription();
      setStatus(s); setOffline(false);
      // Keep the local backend's per-frame watermark decision in sync with
      // the account's real license state (local face swap is gated here,
      // not by /me — see pipeline.py's `licensed` attribute).
      api.setLicensed(s.licensed).catch(() => undefined);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      if (/sign in/i.test(msg)) { setStatus(null); }   // token rejected -> signed out
      else { setOffline(true); }                        // network -> keep last / use cache
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Poll, not just a one-time fetch on launch: this is the license sync source
  // for local face swap, which never visits the Apex Pro tab at all, so a
  // purchase or an admin grant made mid-session (no restart) needs a live path
  // to reach the local watermark decision, not just app-launch's one-shot call.
  useEffect(() => {
    if (!signedIn()) return;
    const t = setInterval(refresh, 20_000);
    return () => clearInterval(t);
  }, [refresh]);

  return <Ctx.Provider value={{ status, offline, loading, refresh }}>{children}</Ctx.Provider>;
}

/** Wraps LOCAL tab content. Shows sign-in or the subscribe screen inline when the
 *  user has no active local access; otherwise renders the tab. */
export function LocalGate({ children }: { children: React.ReactNode }) {
  const { status, offline, loading, refresh } = useSubscription();

  if (!signedIn()) {
    return <InlineWrap><AuthCard onDone={refresh} /></InlineWrap>;
  }
  if (loading && !status) {
    return <InlineWrap><div className="gate-splash">Checking your subscription…</div></InlineWrap>;
  }
  const active = status ? status.active : cachedActive();
  if (!active) {
    return (
      <InlineWrap>
        <SubscribeCard status={status} offline={offline} onRefresh={refresh}
                       onSignOut={() => { cloud.logout(); refresh(); }} />
      </InlineWrap>
    );
  }
  return <>{children}</>;
}

/** Slim pill shown while on trial or nearly expired. Renders nothing otherwise.
 *  Its button opens checkout directly so a trial user can pay early (paying never
 *  loses days — the server extends from whichever is later, now or expiry). */
export function AccessRibbon() {
  const { status } = useSubscription();
  const [busy, setBusy] = useState(false);
  if (!status || !status.active || !(status.trial || status.days_left <= 3)) return null;
  const days = Math.max(0, Math.ceil(status.days_left));
  const left = days === 1 ? "1 day" : `${days} days`;
  const pay = async () => {
    setBusy(true);
    try { const r = await cloud.startSubscription(); window.open(r.link, "_blank"); }
    catch { /* surfaced on the full subscribe screen if it persists */ }
    finally { setBusy(false); }
  };
  return (
    <div className="access-ribbon">
      <span>{status.trial ? `Free trial — ${left} left` : `Subscription — ${left} left`}</span>
      <button className="access-ribbon-btn" disabled={busy} onClick={pay}>
        {busy ? "…" : status.trial ? "Subscribe" : "Renew"}
      </button>
    </div>
  );
}

// --- inline layout wrapper ---------------------------------------------------
function InlineWrap({ children }: { children: React.ReactNode }) {
  return <div className="gate-inline">{children}</div>;
}

// --- sign in / create account (shared account with Apex Pro) -----------------
function AuthCard({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null); setBusy(true);
    try {
      if (mode === "register") await cloud.register(email.trim(), password);
      else await cloud.login(email.trim(), password);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong");
    } finally { setBusy(false); }
  };

  return (
    <div className="gate-card">
      <h2>{mode === "register" ? "Unlock Apex Cam" : "Welcome back"}</h2>
      <p>
        {mode === "register"
          ? "Create an account — your 1-day free trial starts right away."
          : "Sign in to use the local app."}
      </p>
      <input className="gate-input" type="email" placeholder="Email" value={email}
             autoFocus onChange={(e) => setEmail(e.target.value)} />
      <input className="gate-input" type="password" placeholder="Password (min 6 chars)"
             value={password} onChange={(e) => setPassword(e.target.value)}
             onKeyDown={(e) => e.key === "Enter" && submit()} />
      {err && <p className="gate-error">{err}</p>}
      <button className="btn primary" disabled={busy || !email || !password} onClick={submit}>
        {busy ? "Please wait…" : mode === "register" ? "Start free trial" : "Sign in"}
      </button>
      <button className="gate-link"
              onClick={() => { setErr(null); setMode(mode === "register" ? "login" : "register"); }}>
        {mode === "register" ? "I already have an account" : "Create a new account"}
      </button>
      <p className="gate-fine">Just want the cloud tier? Open <strong>✦ Apex Pro</strong> — it runs
        on pay-per-call credits, no monthly plan.</p>
    </div>
  );
}

// --- subscribe / renew -------------------------------------------------------
function SubscribeCard({ status, offline, onRefresh, onSignOut }: {
  status: SubStatus | null; offline: boolean;
  onRefresh: () => void; onSignOut: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const price = status ? `₦${status.price_ngn.toLocaleString()}` : "₦20,000";
  const usd = status ? `$${status.price_usd.toFixed(2)}` : "$12.50";
  const everPaid = status ? status.ever_paid : false;

  const subscribe = async () => {
    setErr(null); setBusy(true);
    try {
      const r = await cloud.startSubscription();
      window.open(r.link, "_blank");   // opens in the system browser (Electron)
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not start payment");
    } finally { setBusy(false); }
  };

  return (
    <div className="gate-card">
      <h2>{everPaid ? "Your subscription has ended" : "Your free trial has ended"}</h2>
      <p>Subscribe to unlock the local app — face swap, voice, and the virtual camera.</p>
      <div className="gate-price">
        <span className="gate-price-main">{price}</span>
        <span className="gate-price-sub">/ month &nbsp;·&nbsp; about {usd}</span>
      </div>
      <p className="gate-fine">Manual renewal — pay again next month to keep going. No auto-charge.</p>
      {offline && <p className="gate-error">Couldn&apos;t reach the server — check your connection and press refresh.</p>}
      {err && <p className="gate-error">{err}</p>}
      <button className="btn primary" disabled={busy} onClick={subscribe}>
        {busy ? "Opening checkout…" : `Subscribe — ${price}/month`}
      </button>
      <button className="gate-link" onClick={onRefresh}>I&apos;ve paid — refresh</button>
      <button className="gate-link" onClick={onSignOut}>Sign out</button>
      <p className="gate-fine">Prefer the cloud tier instead? <strong>✦ Apex Pro</strong> needs no
        subscription — it runs on pay-per-call credits.</p>
    </div>
  );
}
