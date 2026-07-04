import { useState } from "react";

const CONSENT_KEY = "emycam.consent.v1";

export function hasConsent(): boolean {
  return localStorage.getItem(CONSENT_KEY) === "accepted";
}

// One-time terms acknowledgment shown on first run (docs/LEGAL.md). Not an
// access gate — a single acknowledgment recorded locally.
export function ConsentModal({ onAccept }: { onAccept: () => void }) {
  const [checked, setChecked] = useState(false);

  const accept = () => {
    localStorage.setItem(CONSENT_KEY, "accepted");
    localStorage.setItem("emycam.consent.date", new Date().toISOString());
    onAccept();
  };

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>Welcome to EMY CAM</h2>
        <p>EMY CAM transforms how you look and sound in real time. Before you start:</p>
        <ul>
          <li>
            <strong>Do not impersonate any real person without their permission.</strong>{" "}
            Using someone's face or voice to deceive, defraud, or harass may be illegal.
          </li>
          <li>
            All output is <strong>labeled AI-generated</strong>. Do not remove or hide the label.
          </li>
          <li>
            Misuse can be reported and leads to <strong>blocking and restriction</strong>.
          </li>
        </ul>
        <label className="check-row">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => setChecked(e.target.checked)}
          />
          I understand and agree to the Terms of Use &amp; Responsible-Use Policy
        </label>
        <button className="btn primary" disabled={!checked} onClick={accept}>
          Get started
        </button>
      </div>
    </div>
  );
}
