"use client";

// Event Analyst -> TimesFM readout. Shows the agent's per-company impact map for
// the active Black Swan event — the forward-looking adjustment that conditions
// the TimesFM forecast the quant funds trade. Polls /api/analyst.

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/socket";

interface AnalystData {
  event: string;
  source: string;
  impact: Record<string, number>;
}

const POLL_MS = 5_000;

export default function AnalystPanel() {
  const [data, setData] = useState<AnalystData | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/analyst`);
        if (!res.ok) return;
        const d = (await res.json()) as AnalystData;
        if (alive) setData(d);
      } catch {
        // backend not up; keep last good read
      }
    };
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const impact = data?.impact ?? {};
  const rows = Object.entries(impact).sort((a, b) => a[1] - b[1]).slice(0, 8);
  const hasEvent = Boolean(data?.event);

  return (
    <div className="panel flex min-h-0 flex-col p-3">
      <div className="flex items-center justify-between">
        <span className="section-title">Event Analyst → TimesFM</span>
        {data?.source && (
          <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-ink3">
            {data.source === "llm" ? "LLM agent" : "heuristic"}
          </span>
        )}
      </div>

      {!hasEvent ? (
        <div className="mt-2 text-[11px] text-ink3">
          No active event. Inject a Black Swan to see its projected impact.
        </div>
      ) : (
        <>
          <div className="mt-1.5 text-[11px] leading-snug text-ink2">{data?.event}</div>
          <div className="mt-1 text-[9px] uppercase tracking-[0.18em] text-ink3">
            Projected impact fed to the forecast
          </div>
          <div className="mt-1.5 flex min-h-0 flex-col gap-0.5 overflow-y-auto scroll-thin">
            {rows.length === 0 ? (
              <span className="text-[11px] text-ink3">Analyzing…</span>
            ) : (
              rows.map(([ticker, value]) => {
                const pct = value * 100;
                const down = value < 0;
                return (
                  <div
                    key={ticker}
                    className="flex items-center justify-between rounded px-1.5 py-1 text-xs"
                  >
                    <span className="tnum text-ink">{ticker}</span>
                    <span className={`tnum ${down ? "text-down" : "text-up"}`}>
                      {pct > 0 ? "+" : ""}
                      {pct.toFixed(1)}%
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </>
      )}
    </div>
  );
}
