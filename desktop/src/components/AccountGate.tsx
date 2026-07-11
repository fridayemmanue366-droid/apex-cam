import { useCallback, useEffect, useState } from "react";
import { cloud, signedIn, cachedActive, type SubStatus } from "../api/cloud";

/**
 * Gates the whole local app behind an account + active subscription.
 *
 * Flow: new users register -> a 1-day free trial starts automatically -> after it
 * lapses the app is locked until they pay ₦20,000 for another 30 days (manual
 * renewal). Access is decided by the SERVER (users.access_until); we only cache the
 * expiry so a brief network drop doesn't lock a paid-up user out.
 */
type Phase = "loading" | "auth" | "subscribe" | "ok";

export function AccountGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [status, setStatus] = useState<SubStatus | null>(null);
  const [offline, setOffline] = useState(false);

  const check = useCallback(async () => {
    if (!signedIn()) { setPhase("auth"); return; }
    setPhase("loading");
    try {
      const s = await cloud.subscription();
      setStatus(s);
      setOffline(false);
      setPhase(s.active ? "ok" : "subscribe");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      if (/sign in/i.test(msg)) { setPhase("auth"); return; }  // token rejected
      // Couldn't reach the server: fall back to the last confirmed expiry.
      setOffline(true);
      setPhase(cachedActive() ? "ok" : "subscribe");
    }
  }, []);

  useEffect(() => { check(); }, [check]);

  if (phase === "loading") {
    return <div className="gate-splash">Apex&nbsp;Cam…</div>;
  }
  if (phase === "auth") {
    return <AuthScreen onDone={check} />;
  }
  if (phase === "subscribe") {
    return <SubscribeScreen status={status} offline={offline}
                            onRefresh={check} onSignOut={() => { cloud.logout(); check(); }} />;
  }
  // Unlocked — show the app, with a slim ribbon while on trial or nearly expired.
  return (
    <>
      {status && (status.trial || status.days_left <= 3) && (
        <AccessRibbon status={status} onManage={() => setPhase("subscribe")} />
      )}
      {children}
    </>
  );
}

// --- sign in / create account ------------------------------------------------
function AuthScreen({ onDone }: { onDone: () => void }) {
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
    <div className="modal-backdrop">
      <div className="modal">
        <h2>{mode === "register" ? "Create your Apex Cam account" : "Welcome back"}</h2>
        <p>
          {mode === "register"
            ? "Sign up and your 1-day free trial starts right away."
            : "Sign in to continue."}
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
      </div>
    </div>
  );
}

// --- subscribe / renew -------------------------------------------------------
function SubscribeScreen({ status, offline, onRefresh, onSignOut }: {
  status: SubStatus | null; offline: boolean;
  onRefresh: () => void; onSignOut: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const price = status ? `₦${status.price_ngn.toLocaleString()}` : "₦20,000";
  const usd = status ? `$${status.price_usd.toFixed(2)}` : "$12.50";

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
    <div className="modal-backdrop">
      <div className="modal">
        <h2>Your Apex Cam access has ended</h2>
        <p>Subscribe to unlock face swap, voice, and the virtual camera.</p>
        <div className="gate-price">
          <span className="gate-price-main">{price}</span>
          <span className="gate-price-sub">/ month &nbsp;·&nbsp; about {usd}</span>
        </div>
        <p className="gate-fine">Manual renewal — pay again next month to keep going. No auto-charge.</p>
        {offline && <p className="gate-error">Couldn't reach the server — check your connection and press refresh.</p>}
        {err && <p className="gate-error">{err}</p>}
        <button className="btn primary" disabled={busy} onClick={subscribe}>
          {busy ? "Opening checkout…" : `Subscribe — ${price}/month`}
        </button>
        <button className="gate-link" onClick={onRefresh}>I&apos;ve paid — refresh</button>
        <button className="gate-link" onClick={onSignOut}>Sign out</button>
      </div>
    </div>
  );
}

// --- slim ribbon while on trial or about to expire ---------------------------
function AccessRibbon({ status, onManage }: { status: SubStatus; onManage: () => void }) {
  const days = Math.max(0, Math.ceil(status.days_left));
  const label = status.trial
    ? `Free trial — ${days === 1 ? "1 day" : `${days} days`} left`
    : `Subscription — ${days === 1 ? "1 day" : `${days} days`} left`;
  return (
    <div className="access-ribbon">
      <span>{label}</span>
      <button className="access-ribbon-btn" onClick={onManage}>
        {status.trial ? "Subscribe" : "Renew"}
      </button>
    </div>
  );
}
