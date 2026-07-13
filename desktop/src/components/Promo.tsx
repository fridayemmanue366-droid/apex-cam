// In-app advert for the developer's web/software business. A slim promo card
// lives in the sidebar on every page; a richer popup appears sometimes after the
// welcome lightbox. The WhatsApp button opens the system chat (Electron routes
// https links to the OS browser/app).

const WHATSAPP_NUMBER = "2349134437870"; // 09134437870 in international format
const WHATSAPP_MSG =
  "Hello! I saw your advert on Apex Cam. I'd like to build a website / app for my business.";
export const WHATSAPP_URL =
  `https://wa.me/${WHATSAPP_NUMBER}?text=${encodeURIComponent(WHATSAPP_MSG)}`;

const openWhatsApp = () => window.open(WHATSAPP_URL, "_blank");

const SERVICES: { icon: string; label: string }[] = [
  { icon: "🏢", label: "Business & company websites" },
  { icon: "🛍️", label: "Online stores & e-commerce" },
  { icon: "🏦", label: "Banking & fintech platforms" },
  { icon: "📅", label: "Booking & appointment systems" },
  { icon: "🎓", label: "School & church portals" },
  { icon: "📱", label: "Web & mobile apps" },
  { icon: "⚙️", label: "Custom software & automation" },
];

/** Slim, always-visible promo pinned to the bottom of the sidebar. */
export function SidebarPromo({ onMore }: { onMore: () => void }) {
  return (
    <div className="promo-card">
      <div className="promo-card-glow" />
      <div className="promo-card-kicker">FROM OUR DEVELOPERS</div>
      <div className="promo-card-title">Need a website?</div>
      <div className="promo-card-sub">Business, e-commerce, banking &amp; apps — built for you.</div>
      <button type="button" className="promo-wa-btn" onClick={openWhatsApp}>
        <span className="promo-wa-icon">💬</span> Chat on WhatsApp
      </button>
      <button type="button" className="promo-more" onClick={onMore}>See all services ›</button>
    </div>
  );
}

/** Richer advert popup, shown sometimes after the welcome lightbox. */
export function PromoModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop promo-backdrop" onClick={onClose}>
      <div className="modal promo-modal" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="legal-close promo-x" onClick={onClose} aria-label="Close">✕</button>
        <div className="promo-kicker">FROM OUR DEVELOPERS</div>
        <h2 className="promo-title">Need a website or app for your business?</h2>
        <p className="promo-lead">
          We design and build professional, secure digital products — from simple business sites to
          full banking &amp; fintech platforms.
        </p>

        <div className="promo-grid">
          {SERVICES.map((s) => (
            <div className="promo-item" key={s.label}>
              <span className="promo-item-icon">{s.icon}</span>
              <span>{s.label}</span>
            </div>
          ))}
        </div>

        <div className="promo-trust">🔒 Your privacy and business are 100% safe with us.</div>

        <button type="button" className="promo-wa-btn promo-wa-lg" onClick={openWhatsApp}>
          <span className="promo-wa-icon">💬</span> Chat our developers on WhatsApp
        </button>
        <button type="button" className="promo-later" onClick={onClose}>Maybe later</button>
      </div>
    </div>
  );
}
