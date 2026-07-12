"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import { fmtPrice, fmtPct, changeClass, monogram } from "@/lib/format";
import type { Mover } from "@/lib/types";

type Tab = "GAINERS" | "LOSERS";

export default function TopMovers() {
  const [tab, setTab] = useState<Tab>("GAINERS");
  const economy = useStore((s) => s.economy);
  const companies = useStore((s) => s.companies);
  const setSelectedTicker = useStore((s) => s.setSelectedTicker);

  const movers: Mover[] =
    tab === "GAINERS"
      ? economy?.biggest_gainers ?? []
      : economy?.biggest_losers ?? [];

  // Only movers we can resolve to a known company are shown — no fabrication.
  const rows = movers
    .map((m) => ({ mover: m, company: companies[m.ticker] }))
    .filter((r) => r.company);

  return (
    <div className="panel flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-hair px-3 py-2.5">
        <span className="section-title shrink-0">Top Movers</span>
        <div className="flex items-center gap-1">
          {(["GAINERS", "LOSERS"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-sm px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] transition ${
                tab === t ? "bg-white/[0.06] text-ink" : "text-ink3 hover:text-ink2"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto">
        {rows.length === 0 ? (
          <div className="flex h-full items-center justify-center py-10 text-[10px] uppercase tracking-[0.16em] text-ink3">
            NO MOVERS YET
          </div>
        ) : (
          <ul>
            {rows.map(({ mover, company }) => (
              <li key={mover.ticker}>
                <button
                  onClick={() => setSelectedTicker(mover.ticker)}
                  className="flex w-full items-center gap-3 border-t border-hair/60 px-3 py-2.5 text-left transition hover:bg-white/[0.03]"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-white/[0.05] text-[10px] font-semibold text-ink2 ring-1 ring-inset ring-hair">
                    {monogram(company.name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-ink">
                      {mover.ticker}
                    </span>
                    <span className="block truncate text-[10px] text-ink3">
                      {company.name}
                    </span>
                  </span>
                  <span className="tnum shrink-0 text-right text-xs text-ink2">
                    {fmtPrice(company.current_price)}
                  </span>
                  <span
                    className={`tnum w-16 shrink-0 text-right text-xs font-semibold ${changeClass(
                      mover.change_pct,
                    )}`}
                  >
                    {fmtPct(mover.change_pct)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-hair px-3 py-1.5">
        <span className="text-[9px] uppercase tracking-[0.14em] text-ink3">
          {tab === "GAINERS" ? "Session gainers" : "Session losers"}
        </span>
        <span className="tnum font-mono text-[9px] uppercase tracking-[0.14em] text-ink3">
          {rows.length} symbols
        </span>
      </div>
    </div>
  );
}
