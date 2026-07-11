"use client";

import { useStore } from "@/lib/store";
import { fmtPct, fmtCap, changeClass, sentimentColor } from "@/lib/format";
import type { Mover, VolatileMover } from "@/lib/types";

// Threshold-driven stress tone, mirrored across the meter and headline value.
function stressColor(v: number): string {
  if (v > 0.66) return "var(--down)";
  if (v > 0.33) return "var(--warn)";
  return "var(--up)";
}

export default function EconomyPanel() {
  const economy = useStore((s) => s.economy);

  return (
    <div className="panel flex h-full flex-col">
      <div className="border-b border-hair px-3 py-2.5">
        <span className="section-title">Global Economy</span>
      </div>

      {!economy ? (
        <div className="flex flex-1 items-center justify-center text-[10px] uppercase tracking-[0.16em] text-ink3">
          AWAITING ECONOMY DATA
        </div>
      ) : (
        <div className="scroll-thin flex-1 space-y-6 overflow-y-auto p-4">
          {/* (a) SYSTEM STRESS */}
          <section>
            <div className="mb-2 flex items-baseline justify-between">
              <span className="section-title">System Stress</span>
              <span className="text-[10px] text-ink3">
                <span className="tnum text-ink2">{economy.bankrupt_count}</span>{" "}
                bankrupt
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.min(100, Math.max(0, economy.system_stress_index * 100))}%`,
                    backgroundColor: stressColor(economy.system_stress_index),
                  }}
                />
              </div>
              <span
                className="tnum w-14 shrink-0 text-right text-sm font-semibold"
                style={{ color: stressColor(economy.system_stress_index) }}
              >
                {economy.system_stress_index.toFixed(3)}
              </span>
            </div>
          </section>

          {/* (b) SECTORS */}
          <section>
            <div className="mb-2 section-title">Sectors</div>
            {economy.sectors.length === 0 ? (
              <div className="text-[10px] uppercase tracking-[0.16em] text-ink3">
                NO SECTOR DATA
              </div>
            ) : (
              <ul className="space-y-2.5">
                {economy.sectors.map((sec) => (
                  <li key={sec.sector} className="text-xs">
                    <div className="flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-sm"
                        style={{ backgroundColor: sentimentColor(sec.avg_sentiment) }}
                        title={`sentiment ${sec.avg_sentiment.toFixed(2)}`}
                      />
                      <span className="flex-1 truncate font-medium text-ink">
                        {sec.sector}
                      </span>
                      <span
                        className={`tnum shrink-0 text-right ${changeClass(sec.avg_change_pct)}`}
                      >
                        {fmtPct(sec.avg_change_pct)}
                      </span>
                      <span className="tnum w-14 shrink-0 text-right text-ink2">
                        {fmtCap(sec.market_cap)}
                      </span>
                    </div>
                    <div className="mt-1 ml-[18px] flex items-center gap-2">
                      <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
                        <div
                          className="h-full rounded-full"
                          style={{
                            width: `${Math.min(100, Math.abs(sec.avg_change_pct) * 10)}%`,
                            backgroundColor:
                              sec.avg_change_pct >= 0 ? "var(--up)" : "var(--down)",
                          }}
                        />
                      </div>
                    </div>
                    <div className="mt-1 ml-[18px] truncate text-[10px] text-ink3">
                      {sec.companies.join(" · ")}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* (c) MOVERS */}
          <section className="grid grid-cols-3 gap-3">
            <MoverColumn title="Gainers" movers={economy.biggest_gainers} />
            <MoverColumn title="Losers" movers={economy.biggest_losers} />
            <VolatileColumn title="Volatile" movers={economy.most_volatile} />
          </section>

          {/* (d) MACRO NARRATIVE */}
          <section>
            <div className="mb-2 section-title">Macro Analyst</div>
            <div className="rounded-md border border-hair bg-white/[0.02] p-3">
              {economy.narrative.trim() ? (
                <p className="font-mono text-xs leading-relaxed text-ink2">
                  {economy.narrative}
                </p>
              ) : (
                <p className="font-mono text-xs italic text-ink3">
                  Awaiting macro analysis…
                </p>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function MoverColumn({ title, movers }: { title: string; movers: Mover[] }) {
  return (
    <div>
      <div className="mb-1.5 text-[9.5px] uppercase tracking-[0.14em] text-ink3">
        {title}
      </div>
      {movers.length === 0 ? (
        <div className="text-[10px] text-ink3">—</div>
      ) : (
        <ul className="space-y-1">
          {movers.map((m) => (
            <li
              key={m.ticker}
              className="flex items-baseline justify-between gap-1 text-[11px]"
            >
              <span className="truncate text-ink2">{m.ticker}</span>
              <span className={`tnum shrink-0 ${changeClass(m.change_pct)}`}>
                {fmtPct(m.change_pct)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VolatileColumn({
  title,
  movers,
}: {
  title: string;
  movers: VolatileMover[];
}) {
  return (
    <div>
      <div className="mb-1.5 text-[9.5px] uppercase tracking-[0.14em] text-ink3">
        {title}
      </div>
      {movers.length === 0 ? (
        <div className="text-[10px] text-ink3">—</div>
      ) : (
        <ul className="space-y-1">
          {movers.map((m) => (
            <li
              key={m.ticker}
              className="flex items-baseline justify-between gap-1 text-[11px]"
            >
              <span className="truncate text-ink2">{m.ticker}</span>
              <span className="tnum shrink-0 text-ink2">
                {m.volatility.toFixed(3)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
