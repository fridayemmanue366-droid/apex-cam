// Legal / policy documents shown in the app (welcome lightbox + Settings).
//
// DEVELOPER NOTE: these are solid, standard drafts written for Apex Cam's actual
// data flow (local processing on-device; Apex Pro sends media to our server and
// Decart; Flutterwave handles payments). Before you sell to the public, have a
// lawyer review them and fill in the operator/contact/jurisdiction below.
import type { ReactNode } from "react";

// —— fill these in with your real details before public launch ——
export const OPERATOR = "Apex Cam";                 // your legal/business name
export const CONTACT_EMAIL = "support@apexcam.app"; // your support email
export const JURISDICTION = "the Federal Republic of Nigeria";
export const UPDATED = "12 July 2026";

export type DocId = "terms" | "acceptable" | "privacy" | "cookies";

export interface LegalDoc {
  title: string;
  body: ReactNode;
}

export const LEGAL_ORDER: DocId[] = ["terms", "acceptable", "privacy", "cookies"];

export const LEGAL_TITLES: Record<DocId, string> = {
  terms: "Terms of Use",
  acceptable: "Acceptable-Use Policy",
  privacy: "Privacy Policy",
  cookies: "Cookie Policy",
};

export const LEGAL_DOCS: Record<DocId, LegalDoc> = {
  terms: {
    title: "Terms of Use",
    body: (
      <>
        <p className="legal-updated">Last updated: {UPDATED}</p>
        <p>
          These Terms of Use (“Terms”) are an agreement between you and {OPERATOR} (“we”, “us”)
          governing your use of the Apex Cam application and related services (the “Service”). By
          installing or using Apex Cam you accept these Terms. If you do not agree, do not use the
          Service.
        </p>

        <h4>1. Who can use Apex Cam</h4>
        <p>
          You must be at least 18 years old and legally able to enter into this agreement. You are
          responsible for complying with all laws that apply to you.
        </p>

        <h4>2. Your account</h4>
        <p>
          Some features require an account. Keep your password secure; you are responsible for
          activity under your account. Tell us immediately if you suspect unauthorised use.
        </p>

        <h4>3. Subscriptions, credits and payments</h4>
        <ul>
          <li>
            The local app is offered on a <strong>1-day free trial</strong>, after which it requires
            a paid monthly subscription to remain unlocked. Renewal is <strong>manual</strong> — you
            are not charged automatically.
          </li>
          <li>
            Apex Pro (cloud features) runs on <strong>prepaid credits</strong> you purchase in
            advance. Credits are consumed as you use paid features.
          </li>
          <li>
            Payments are processed by our payment provider (Flutterwave). We do not receive or store
            your card details.
          </li>
          <li>
            Except where required by law, payments and unused credits are non-refundable. Prices may
            change with notice.
          </li>
        </ul>

        <h4>4. Acceptable use</h4>
        <p>
          Your use of Apex Cam is subject to our Acceptable-Use Policy. In short, Apex Cam is a
          creative tool for streaming, content creation and entertainment — <strong>never</strong>{" "}
          for scams, fraud, deception, impersonating real people, or anything illegal.
        </p>

        <h4>5. Your content and AI output</h4>
        <p>
          You keep ownership of the images, video and audio you provide. You grant us a limited
          licence to process that content solely to provide the Service (for example, sending it to
          our AI processing partner for cloud features). You are solely responsible for the content
          you create with Apex Cam and how you use it, including disclosing that content is
          AI-generated where the law requires.
        </p>

        <h4>6. Third-party services</h4>
        <p>
          Cloud features rely on third parties, including our AI processing partner (Decart) and our
          payment provider (Flutterwave). Their terms and privacy practices also apply to those
          parts of the Service.
        </p>

        <h4>7. Disclaimer</h4>
        <p>
          The Service is provided “as is” and “as available”, without warranties of any kind. We do
          not guarantee that outputs will be accurate, uninterrupted, or fit for any particular
          purpose.
        </p>

        <h4>8. Limitation of liability</h4>
        <p>
          To the fullest extent permitted by law, we are not liable for any indirect, incidental, or
          consequential losses, or for loss of profits, data, or goodwill arising from your use of
          the Service. Our total liability is limited to the amount you paid us in the 3 months
          before the claim.
        </p>

        <h4>9. Suspension and termination</h4>
        <p>
          We may suspend or terminate your access if you breach these Terms or the Acceptable-Use
          Policy, or where required to protect users or comply with law.
        </p>

        <h4>10. Changes</h4>
        <p>
          We may update these Terms. Continued use after an update means you accept the revised
          Terms.
        </p>

        <h4>11. Governing law</h4>
        <p>These Terms are governed by the laws of {JURISDICTION}.</p>

        <h4>12. Contact</h4>
        <p>Questions? Contact us at {CONTACT_EMAIL}.</p>
      </>
    ),
  },

  acceptable: {
    title: "Acceptable-Use Policy",
    body: (
      <>
        <p className="legal-updated">Last updated: {UPDATED}</p>
        <p>
          Apex Cam is built for <strong>streamers, content creators, video callers, and
          entertainment</strong> — a creative tool to change how you look and sound in real time.
          This policy explains what is and isn’t allowed. Breaking it can lead to suspension,
          termination, and reporting to the authorities.
        </p>

        <h4>✅ What Apex Cam is for</h4>
        <ul>
          <li>Live streaming, video content, and social media.</li>
          <li>Fun and creative video calls where people understand it’s a creative effect.</li>
          <li>Protecting your privacy, cosplay, avatars, and artistic projects.</li>
        </ul>

        <h4>🚫 What is strictly prohibited</h4>
        <ul>
          <li>
            <strong>Scams and fraud of any kind</strong> — including romance scams, investment or
            payment scams, phishing, or pretending to be someone else to obtain money, goods, or
            information.
          </li>
          <li>
            <strong>Impersonating a real person to deceive</strong> — using someone’s face or voice
            to trick, defraud, or harm others, or to gain a benefit dishonestly.
          </li>
          <li>
            <strong>Fake identity or verification</strong> — bypassing identity checks (KYC), or
            creating fake “proof”, documents, or evidence.
          </li>
          <li>
            <strong>Non-consensual or sexual content</strong> — sexual content involving anyone who
            has not consented, and <strong>any</strong> sexual content involving minors (zero
            tolerance — such use is reported to the authorities).
          </li>
          <li>
            <strong>Harassment, bullying, or defamation</strong> — targeting or humiliating a real
            person.
          </li>
          <li>
            <strong>Disinformation</strong> — misleading people in elections, emergencies, or news.
          </li>
          <li>Anything else that is illegal where you or your audience are located.</li>
        </ul>

        <h4>Disclosure</h4>
        <p>
          Where the law requires it, you must tell the people you share your video with that it is
          AI-generated. Being open about a creative effect is not just the law in some places — it’s
          the right thing to do.
        </p>

        <h4>Reporting misuse</h4>
        <p>
          If someone uses Apex Cam to impersonate or harm you, contact {CONTACT_EMAIL}. Confirmed
          misuse leads to blocking and restriction.
        </p>
      </>
    ),
  },

  privacy: {
    title: "Privacy Policy",
    body: (
      <>
        <p className="legal-updated">Last updated: {UPDATED}</p>
        <p>
          This Privacy Policy explains what {OPERATOR} collects when you use Apex Cam, and how we use
          and protect it.
        </p>

        <h4>What we collect</h4>
        <ul>
          <li>
            <strong>Account details</strong> — your email address and a securely hashed password (we
            never store your password in readable form).
          </li>
          <li>
            <strong>Subscription and credit status</strong> — whether your subscription is active,
            your credit balance, and payment references (not card numbers).
          </li>
          <li>
            <strong>Content you submit to cloud features</strong> — when you use Apex Pro, the
            images, video, or audio you upload are sent to our server and our AI partner to produce
            your result.
          </li>
          <li>
            <strong>Basic diagnostic information</strong> — limited technical data needed to run and
            troubleshoot the app.
          </li>
        </ul>

        <h4>What we do NOT collect</h4>
        <ul>
          <li>
            <strong>Your card details</strong> — these are handled entirely by our payment provider
            (Flutterwave). We never see or store them.
          </li>
          <li>
            <strong>Your local webcam feed</strong> — the standard (offline) face swap and voice
            features run <strong>on your own device</strong>. Those frames are not sent to us.
          </li>
        </ul>

        <h4>How we use your information</h4>
        <ul>
          <li>To provide, secure, and support the Service.</li>
          <li>To process payments and manage your subscription and credits.</li>
          <li>To prevent fraud and abuse, and to comply with the law.</li>
        </ul>

        <h4>Sharing and third parties</h4>
        <p>
          We share only what is necessary with: our AI processing partner (Decart) for cloud
          features, and our payment provider (Flutterwave) for payments. We do not sell your
          personal data.
        </p>

        <h4>Retention</h4>
        <p>
          We keep account and transaction records for as long as your account is active and as
          required by law. Media processed by cloud features is handled only to produce your result
          and is not used to build advertising profiles.
        </p>

        <h4>Security</h4>
        <p>
          We use reasonable measures to protect your data, including hashing passwords and keeping
          our provider keys on our server, never in the app you install.
        </p>

        <h4>Your rights</h4>
        <p>
          You can request access to, correction of, or deletion of your personal data by contacting
          {" "}{CONTACT_EMAIL}. You can also stop using the Service and delete your account.
        </p>

        <h4>Children</h4>
        <p>Apex Cam is not intended for anyone under 18.</p>

        <h4>Changes</h4>
        <p>We may update this policy and will show the new “last updated” date here.</p>

        <h4>Contact</h4>
        <p>{CONTACT_EMAIL}</p>
      </>
    ),
  },

  cookies: {
    title: "Cookie Policy",
    body: (
      <>
        <p className="legal-updated">Last updated: {UPDATED}</p>
        <p>
          Apex Cam is a desktop application. Rather than advertising cookies, it stores a small
          amount of data on your own device so the app can work. This policy explains what and why.
        </p>

        <h4>What we store on your device</h4>
        <ul>
          <li>
            <strong>Sign-in token</strong> — so you stay signed in between sessions.
          </li>
          <li>
            <strong>Subscription/access status</strong> — a cached copy of your access expiry so the
            app still works during a brief loss of connection.
          </li>
          <li>
            <strong>Your preferences</strong> — such as your theme (light/dark) and that you have
            seen the welcome notice.
          </li>
        </ul>

        <h4>What we do NOT use</h4>
        <p>
          We do not use advertising cookies or third-party tracking to follow you across the
          internet.
        </p>

        <h4>Managing this data</h4>
        <p>
          Signing out clears your sign-in token. Uninstalling the app, or clearing its data, removes
          the stored preferences and cache.
        </p>

        <h4>Contact</h4>
        <p>{CONTACT_EMAIL}</p>
      </>
    ),
  },
};
