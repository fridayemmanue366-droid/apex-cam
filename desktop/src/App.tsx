import { useState } from "react";
import { TABS, TAB_SUMMARY, TAB_COMPONENTS, type TabName } from "./tabs";
import { StatusBar } from "./components/StatusBar";
import { ConsentModal, hasConsent } from "./components/ConsentModal";
import { ConfirmDialog } from "./components/ConfirmDialog";
import { SubscriptionProvider, LocalGate, AccessRibbon } from "./components/AccountGate";
import { MediaProvider } from "./context/MediaContext";
import { PipelineProvider } from "./context/PipelineContext";

export function App() {
  const [active, setActive] = useState<TabName>("Home");
  const [consented, setConsented] = useState(hasConsent());
  const ActiveTab = TAB_COMPONENTS[active];

  return (
    <MediaProvider>
      <PipelineProvider>
      <SubscriptionProvider>
      <div className="app">
        {!consented && <ConsentModal onAccept={() => setConsented(true)} />}
        <ConfirmDialog />

        <nav className="sidebar">
          <div className="brand">Apex&nbsp;Cam</div>
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
        </nav>

        <main className="content">
          <header className="content-header">
            <h1>{active}</h1>
            <p>{TAB_SUMMARY[active]}</p>
          </header>
          {active === "Apex Pro" ? (
            // Apex Pro is separate: its own sign-in + pay-per-call credits, no subscription.
            <ActiveTab />
          ) : (
            <LocalGate>
              <ActiveTab />
            </LocalGate>
          )}
        </main>

        <StatusBar />
        <AccessRibbon />
      </div>
      </SubscriptionProvider>
      </PipelineProvider>
    </MediaProvider>
  );
}
