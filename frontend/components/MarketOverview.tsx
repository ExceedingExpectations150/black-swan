"use client";

import { useState } from "react";
import { useStore } from "@/lib/store";
import { fmtPrice, fmtPct, changeClass } from "@/lib/format";
import Sparkline from "@/components/Sparkline";

type Tab = "INDICES" | "COMMODITIES" | "CURRENCIES" | "CRYPTO";
const TABS: Tab[] = ["INDICES", "COMMODITIES", "CURRENCIES", "CRYPTO"];

// The backend only feeds indices; the other three tabs are honest voids.
export default function MarketOverview() {
  const [tab, setTab] = useState<Tab>("INDICES");
  const indices = useStore((s) => s.indices);

  return (
    <div className="panel flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-hair px-3 py-2.5">
        <span className="section-title shrink-0">Markets</span>
        <div className="flex items-center gap-1 overflow-x-auto scroll-thin">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] transition ${
                tab === t
                  ? "bg-white/[0.06] text-ink"
                  : "text-ink3 hover:text-ink2"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto">
        {tab === "INDICES" ? (
          indices.length === 0 ? (
            <EmptyBody label="AWAITING INDEX DATA" />
          ) : (
            <table className="w-full border-collapse">
              <thead>
                <tr className="text-[9.5px] uppercase tracking-[0.14em] text-ink3">
                  <th className="px-3 py-2 text-left font-medium">Index</th>
                  <th className="px-3 py-2 text-right font-medium">Value</th>
                  <th className="px-3 py-2 text-right font-medium">Change</th>
                  <th className="px-3 py-2 text-right font-medium">%Chg</th>
                  <th className="px-3 py-2 text-right font-medium">Trend</th>
                </tr>
              </thead>
              <tbody>
                {indices.map((idx) => (
                  <tr
                    key={idx.symbol}
                    className="border-t border-hair/60 text-xs"
                  >
                    <td className="px-3 py-2.5">
                      <div className="font-medium text-ink">{idx.name}</div>
                      <div className="text-[10px] text-ink3">{idx.symbol}</div>
                    </td>
                    <td className="tnum px-3 py-2.5 text-right text-ink">
                      {fmtPrice(idx.value)}
                    </td>
                    <td
                      className={`tnum px-3 py-2.5 text-right ${changeClass(idx.change)}`}
                    >
                      {idx.change > 0 ? "+" : ""}
                      {fmtPrice(idx.change)}
                    </td>
                    <td
                      className={`tnum px-3 py-2.5 text-right ${changeClass(idx.change_pct)}`}
                    >
                      {fmtPct(idx.change_pct)}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="ml-auto w-16">
                        <Sparkline
                          data={idx.sparkline}
                          up={idx.change_pct >= 0}
                          width={64}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          <EmptyBody label="NOT AVAILABLE" dash />
        )}
      </div>
    </div>
  );
}

function EmptyBody({ label, dash = false }: { label: string; dash?: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 py-10 text-center">
      {dash && <div className="text-2xl text-ink3">—</div>}
      <div className="text-[10px] uppercase tracking-[0.16em] text-ink3">
        {label}
      </div>
    </div>
  );
}
