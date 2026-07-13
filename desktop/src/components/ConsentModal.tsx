import { useState } from "react";
import { LegalModal } from "./LegalModal";
import { LEGAL_ORDER, LEGAL_TITLES, type DocId } from "../legal/LegalContent";

const CONSENT_KEY = "apexcam.consent.v1";

export function hasConsent(): boolean {
  return localStorage.getItem(CONSENT_KEY) === "accepted";
}

// Welcome notice shown EVERY time the app opens (per the product decision) — a
// standing reminder of what Apex Cam is for and what it must never be used for.
// Links to the full policies. Records the latest acknowledgement for the audit view.
export function ConsentModal({ onAccept }: { onAccept: () => void }) {
  const [checked, setChecked] = useState(false);
  const [openDoc, setOpenDoc] = useState<DocId | null>(null);

  const accept = () => {
    localStorage.setItem(CONSENT_KEY, "accepted");
    localStorage.setItem("apexcam.consent.date", new Date().toISOString());
    onAccept();
  };

  return (
    <div className="modal-backdrop">
      <div className="modal consent-modal">
        <h2>Welcome to Apex&nbsp;Cam</h2>
        <p>
          Apex Cam is a creative tool for <strong>streamers, content creators, video callers and
          entertainment</strong> — change how you look and sound in real time.
        </p>

        <div className="consent-box consent-ok">
          <strong>✅ Great for</strong>
          <ul>
            <li>Live streaming, videos and social content</li>
            <li>Fun, creative video calls and avatars</li>
            <li>Privacy, cosplay and artistic projects</li>
          </ul>
        </div>

        <div className="consent-box consent-no">
          <strong>🚫 Never use Apex Cam for</strong>
          <ul>
            <li><strong>Scams or fraud</strong> of any kind</li>
            <li><strong>Impersonating a real person to deceive</strong> or gain money dishonestly</li>
            <li>Non-consensual content, harassment, or anything illegal</li>
          </ul>
          <p className="consent-fine">
            Where the law requires it, tell people your video is AI-generated. Misuse leads to
            blocking and can be reported to the authorities.
          </p>
        </div>

        <label className="check-row">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          I agree to the Terms of Use, Acceptable-Use Policy and Privacy Policy, and I will use Apex
          Cam responsibly.
        </label>

        <div className="consent-links">
          {LEGAL_ORDER.map((id) => (
            <button type="button" key={id} className="legal-link" onClick={() => setOpenDoc(id)}>
              {LEGAL_TITLES[id]}
            </button>
          ))}
        </div>

        <button className="btn primary" disabled={!checked} onClick={accept}>
          I Agree — Enter Apex Cam
        </button>
      </div>

      {openDoc && <LegalModal doc={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
