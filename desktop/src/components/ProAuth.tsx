import { useState } from "react";
import { cloud } from "../api/cloud";

// Sign in / create an Apex Pro account. Credits live on our server (tied to this
// account), so the balance follows the user across machines and can't be edited
// locally. Shown before the Pro universe when signed out.
export function ProAuth({ onSignedIn }: { onSignedIn: (email: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    const go = mode === "login" ? cloud.login : cloud.register;
    go(email.trim(), password)
      .then((r) => onSignedIn(r.email))
      .catch((x) => setErr(String(x.message || x)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="pro">
      <div className="pro-hero">
        <span className="pro-crown">✦</span>
        <div>
          <h1 className="pro-title">APEX&nbsp;PRO</h1>
          <p className="pro-sub">Sign in to use the cloud studio.</p>
        </div>
        <span className="pro-vip">VIP</span>
      </div>

      <form className="pro-card narrow" onSubmit={submit}>
        <h3>{mode === "login" ? "Sign in" : "Create your account"}</h3>
        <p className="pro-muted">
          Your minutes live on your account — they follow you to any PC you sign in on.
        </p>
        <input className="pro-input pro-auth-field" type="email" required value={email}
               placeholder="you@email.com" autoComplete="email"
               onChange={(e) => setEmail(e.target.value)} />
        <input className="pro-input pro-auth-field" type="password" required value={password}
               placeholder="Password (min 6 characters)" minLength={6}
               autoComplete={mode === "login" ? "current-password" : "new-password"}
               onChange={(e) => setPassword(e.target.value)} />
        {err && <p className="error">{err}</p>}
        <button type="submit" className="pro-goldbtn" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "✦ Sign in" : "✦ Create account"}
        </button>
        <p className="pro-muted pro-note">
          {mode === "login" ? "New here? " : "Already have an account? "}
          <button type="button" className="pro-linkbtn"
                  onClick={() => { setMode(mode === "login" ? "register" : "login"); setErr(null); }}>
            {mode === "login" ? "Create an account" : "Sign in"}
          </button>
        </p>
      </form>
    </div>
  );
}
