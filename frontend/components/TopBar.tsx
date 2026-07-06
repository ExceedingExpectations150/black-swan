"use client";

import { useEffect, useMemo, useState } from "react";
import { Search, Globe } from "lucide-react";
import { useStore, useCompanyList } from "@/lib/store";
import { fmtPrice, fmtPct, changeClass } from "@/lib/format";
import Sparkline from "@/components/Sparkline";

function useUtcClock(): { time: string; date: string } {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  if (!now) return { time: "--:--:--", date: "" };
  const time = now.toISOString().slice(11, 19);
  const date = now.toISOString().slice(0, 10);
  return { time, date };
}

export default function TopBar() {
  const indices = useStore((s) => s.indices);
  const companies = useCompanyList();
  const setSelectedTicker = useStore((s) => s.setSelectedTicker);
  const { time, date } = useUtcClock();
  const [query, setQuery] = useState("");

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return companies
      .filter((c) => c.ticker.toLowerCase().includes(q) || c.name.toLowerCase().includes(q))
      .slice(0, 6);
  }, [query, companies]);

  return (
    <header className="flex h-14 items-center gap-4 border-b border-hair px-4">
      <div className="flex items-center gap-2.5 shrink-0">
        <Globe size={18} className="text-ink" />
        <div className="leading-none">
          <div className="font-display text-sm font-semibold tracking-tight text-ink">
            ChaosNet
          </div>
          <div className="mt-0.5 text-[9px] uppercase tracking-[0.2em] text-ink3">
            Global Intelligence
          </div>
        </div>
      </div>

      <div className="relative w-64 shrink-0">
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink3" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search companies, markets, topics…"
          className="w-full rounded-md border border-hair bg-white/[0.03] py-1.5 pl-8 pr-3 text-xs text-ink placeholder:text-ink3 focus:border-white/20 focus:outline-none"
        />
        {matches.length > 0 && (
          <div className="absolute left-0 top-full z-30 mt-1 w-full overflow-hidden rounded-md border border-hair bg-black">
            {matches.map((c) => (
              <button
                key={c.ticker}
                onClick={() => {
                  setSelectedTicker(c.ticker);
                  setQuery("");
                }}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-white/[0.05]"
              >
                <span className="text-ink">{c.name}</span>
                <span className="tnum text-ink3">{c.ticker}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-4 overflow-x-auto scroll-thin">
        {indices.length === 0 ? (
          <span className="text-[11px] text-ink3">— awaiting index data —</span>
        ) : (
          indices.map((idx) => {
            const up = idx.change_pct >= 0;
            return (
              <div key={idx.symbol} className="flex shrink-0 items-center gap-2">
                <div className="leading-none">
                  <div className="text-[10px] uppercase tracking-wider text-ink2">{idx.name}</div>
                  <div className="tnum mt-0.5 text-xs text-ink">{fmtPrice(idx.value)}</div>
                </div>
                <div className="w-12">
                  <Sparkline data={idx.sparkline} up={up} height={20} />
                </div>
                <span className={`tnum text-[11px] ${changeClass(idx.change_pct)}`}>
                  {fmtPct(idx.change_pct)}
                </span>
              </div>
            );
          })
        )}
      </div>

      <div className="shrink-0 text-right leading-none">
        <div className="tnum text-sm text-ink">{time}</div>
        <div className="tnum mt-0.5 text-[10px] text-ink3">{date} UTC</div>
      </div>
    </header>
  );
}
