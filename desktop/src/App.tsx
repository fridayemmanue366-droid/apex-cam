import { useState } from "react";
import { TABS, TAB_SUMMARY, TAB_COMPONENTS, type TabName } from "./tabs";
import { StatusBar } from "./components/StatusBar";
import { ConsentModal } from "./components/ConsentModal";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { SubscriptionProvider, LocalGate, AccessRibbon } from "./components/AccountGate";
import { SidebarPromo, PromoModal } from "./components/Promo";
import { MediaProvider } from "./context/MediaContext";
import { PipelineProvider } from "./context/PipelineContext";
import { ProTab } from "./tabs/ProTab";
import { ModelsBanner } from "./components/ModelsBanner";

export function App() {
  const [active, setActive] = useState<TabName>("Home");
  const inPro = active === "Apex Pro";
  // Show the welcome / responsible-use notice EVERY time the app opens (not just
  // the first run) — it always starts unacknowledged for this session.
  const [consented, setConsented] = useState(false);
  const [showPromo, setShowPromo] = useState(false);
  const ActiveTab = TAB_COMPONENTS[active];

  const acceptConsent = () => {
    setConsented(true);
    // Sometimes surface the developer advert right after the welcome notice.
    if (Math.random() < 0.5) setShowPromo(true);
  };

  return (
    <MediaProvider>
      <PipelineProvider>
      <SubscriptionProvider>
      <div className={inPro ? "app app-pro" : "app"}>
        {!consented && <ConsentModal onAccept={acceptConsent} />}
        {consented && showPromo && <PromoModal onClose={() => setShowPromo(false)} />}
        <ConfirmDialog />

        {/* Apex Pro is its own full-width universe — the local sidebar/tabs are
            hidden entirely so it gets the whole window, not squeezed beside them.
            The dev/back button (in its hero bar) is the only way out. */}
        {!inPro && (
          <nav className="sidebar">
            <div className="brand">Apex&nbsp;Cam</div>
            <div className="nav-scroll">
              {TABS.map((t) => {
                const cls = ["nav-item"];
                if (t === active) cls.push("active");
                if (t === "Apex Pro") cls.push("pro-nav");
                return (
                  <button type="button" key={t} className={cls.join(" ")} onClick={() => setActive(t)}>
                    {t === "Apex Pro" ? "✦ Apex Pro" : t}
                  </button>
                );
              })}
            </div>
            <SidebarPromo onMore={() => setShowPromo(true)} />
          </nav>
        )}

        <main className="content">
          {!inPro && (
            <header className="content-header">
              <h1>{active}</h1>
              <p>{TAB_SUMMARY[active]}</p>
            </header>
          )}
          {inPro ? (
            // Apex Pro is separate: its own sign-in + pay-per-call credits, no
            // subscription. onExit returns to the local app (Home).
            <ProTab onExit={() => setActive("Home")} />
          ) : (
            <LocalGate>
              <ModelsBanner />
              <ActiveTab />
            </LocalGate>
          )}
        </main>

        <StatusBar />
        {!inPro && <AccessRibbon />}
      </div>
      </SubscriptionProvider>
      </PipelineProvider>
    </MediaProvider>
  );
}
