"use client";

import {
  LayoutDashboard,
  LineChart,
  MessagesSquare,
  MessageSquareText,
  Radar,
  BarChart3,
  Bell,
  Star,
  Circle,
} from "lucide-react";
import { useStore, useCompanyList } from "@/lib/store";
import { fmtPrice, fmtPct, changeClass } from "@/lib/format";
import Sparkline from "@/components/Sparkline";

export type NavKey = "dashboard" | "market" | "social" | "nodes" | "analytics" | "desk" | "alerts";

const NAV: { key: NavKey; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { key: "market", label: "Stock Market", icon: LineChart },
  { key: "social", label: "News Feed", icon: MessagesSquare },
  { key: "nodes", label: "Company Nodes", icon: Radar },
  { key: "analytics", label: "Analytics", icon: BarChart3 },
  { key: "desk", label: "Trading Desk", icon: MessageSquareText },
  { key: "alerts", label: "Alerts", icon: Bell },
];

export default function Sidebar({
  active,
  onNavigate,
}: {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
}) {
  const companies = useCompanyList();
  const watchlist = useStore((s) => s.watchlist);
  const indices = useStore((s) => s.indices);
  const connection = useStore((s) => s.connectionStatus);
  const setSelectedTicker = useStore((s) => s.setSelectedTicker);

  const byTicker = Object.fromEntries(companies.map((c) => [c.ticker, c]));
  const watched = watchlist.map((t) => byTicker[t]).filter(Boolean);
  const globalSpark = indices[0]?.sparkline ?? [];
  const isOpen = connection === "open";

  return (
    <aside className="flex w-[230px] shrink-0 flex-col gap-4 border-r border-hair p-3">
      <nav className="flex flex-col gap-0.5">
        {NAV.map(({ key, label, icon: Icon }) => {
          const on = active === key;
          return (
            <button
              key={key}
              onClick={() => onNavigate(key)}
              className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition ${
                on
                  ? "bg-white/[0.07] text-ink"
                  : "text-ink2 hover:bg-white/[0.03] hover:text-ink"
              }`}
            >
              <Icon size={15} />
              {label}
            </button>
          );
        })}
      </nav>

      <div className="panel p-3">
        <div className="flex items-center gap-1.5">
          <Circle size={7} className={isOpen ? "fill-up text-up live-dot" : "fill-down text-down"} />
          <span className="section-title">{isOpen ? "Market Open" : "Reconnecting"}</span>
        </div>
        <div className="mt-1 text-[11px] text-ink2">Global Markets — Live</div>
        <div className="mt-2 h-8">
          <Sparkline data={globalSpark} up height={32} />
        </div>
      </div>

      <div className="panel flex min-h-0 flex-1 flex-col p-3">
        <div className="flex items-center gap-1.5">
          <Star size={11} className="text-ink2" />
          <span className="section-title">Watchlist</span>
        </div>
        <div className="mt-2 flex flex-col gap-0.5 overflow-y-auto scroll-thin">
          {watched.length === 0 ? (
            <span className="text-[11px] text-ink3">Awaiting companies…</span>
          ) : (
            watched.map((c) => (
              <button
                key={c.ticker}
                onClick={() => setSelectedTicker(c.ticker)}
                className="flex items-center justify-between rounded px-1.5 py-1.5 text-left hover:bg-white/[0.04]"
              >
                <div className="min-w-0 leading-none">
                  <div className="tnum text-xs text-ink">{c.ticker}</div>
                  <div className="mt-0.5 truncate text-[10px] text-ink3">{c.name}</div>
                </div>
                <div className="text-right leading-none">
                  <div className="tnum text-xs text-ink">{fmtPrice(c.current_price)}</div>
                  <div className={`tnum mt-0.5 text-[10px] ${changeClass(c.change_pct)}`}>
                    {fmtPct(c.change_pct)}
                  </div>
                </div>
              </button>
            ))
          )}
        </div>
      </div>

    </aside>
  );
}
