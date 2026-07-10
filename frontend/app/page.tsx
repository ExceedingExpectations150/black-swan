"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useLiveConnection } from "@/lib/socket";
import { useStore, useCompanyList } from "@/lib/store";
import TopBar from "@/components/TopBar";
import TimeBar from "@/components/TimeBar";
import Sidebar, { type NavKey } from "@/components/Sidebar";
import MarketOverview from "@/components/MarketOverview";
import TopMovers from "@/components/TopMovers";
import AnalystPanel from "@/components/AnalystPanel";
import EconomyPanel from "@/components/EconomyPanel";
import CompanyDetail from "@/components/CompanyDetail";
import StockMarketView from "@/components/StockMarketView";
import BootTerminal from "@/components/BootTerminal";

// react-simple-maps is client-only; skip SSR to avoid window/hydration issues.
const WorldMap = dynamic(() => import("@/components/WorldMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-[11px] tracking-widest text-ink3">
      LOADING WORLD MAP…
    </div>
  ),
});

export default function Home() {
  useLiveConnection();
  const [nav, setNav] = useState<NavKey>("dashboard");
  const [booted, setBooted] = useState(false);
  const news = useStore((s) => s.news);

  if (!booted) {
    return <BootTerminal onLaunch={() => setBooted(true)} />;
  }

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-black">
      <TopBar />
      <TimeBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar active={nav} onNavigate={setNav} />
        <main className="flex min-w-0 flex-1 flex-col gap-3 p-3">
          {news && (
            <div className="shrink-0 rounded-md border border-hair bg-white/[0.03] px-3 py-1.5 text-[11px] text-ink2">
              <span className="mr-2 text-[9px] uppercase tracking-[0.2em] text-down">
                Macro
              </span>
              {news}
            </div>
          )}
          <MainContent nav={nav} />
        </main>
      </div>
      <CompanyDetail />
    </div>
  );
}

function MainContent({ nav }: { nav: NavKey }) {
  const companies = useCompanyList();

  if (nav === "analytics") {
    return (
      <div className="min-h-0 flex-1">
        <EconomyPanel />
      </div>
    );
  }

  if (nav === "analyst") {
    return (
      <div className="min-h-0 flex-1">
        <AnalystPanel />
      </div>
    );
  }

  if (nav === "market") {
    return (
      <div className="panel min-h-0 flex-1 p-3">
        {companies.length > 0 ? (
          <StockMarketView />
        ) : (
          <div className="flex h-full items-center justify-center text-[11px] tracking-widest text-ink3">
            AWAITING MARKET DATA
          </div>
        )}
      </div>
    );
  }

  if (nav === "alerts") {
    return (
      <div className="panel flex min-h-0 flex-1 items-center justify-center">
        <div className="text-center">
          <div className="section-title">Alerts</div>
          <div className="mt-2 text-xs text-ink3">No active alerts.</div>
        </div>
      </div>
    );
  }

  // dashboard (default) and nodes both lead with the world map.
  const mapFocus = nav === "nodes";
  return (
    <>
      <div className="panel relative min-h-0 flex-1 overflow-hidden">
        <WorldMap />
      </div>
      {!mapFocus && (
        <div className="grid h-[240px] shrink-0 grid-cols-3 gap-3">
          <MarketOverview />
          <TopMovers />
          <AnalystPanel />
        </div>
      )}
    </>
  );
}
