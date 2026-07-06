"use client";

import { Activity, Radio, Skull, BarChart3, Newspaper } from "lucide-react";
import ControlPanel from "@/components/ControlPanel";
import MarketChart from "@/components/MarketChart";
import { useSimulationSocket } from "@/hooks/useSimulationSocket";

export default function Home() {
  const { ticks, latest, isConnected, socketError } = useSimulationSocket();

  const stress = latest?.system_stress_index ?? 0;
  const stressTone =
    stress > 0.66 ? "text-rose-400" : stress > 0.33 ? "text-amber-300" : "text-emerald-400";

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-6 py-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight text-white">
            ChaosNet
            <span className="ml-3 rounded-md bg-white/5 px-2 py-0.5 align-middle font-mono text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400 ring-1 ring-inset ring-white/10">
              Black Swan Twin
            </span>
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            50 Gemma behavioral cohorts vs 5 TimesFM quants · Continuous Double Auction
          </p>
        </div>
        <div
          className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 font-mono text-xs ring-1 ring-inset ${
            isConnected
              ? "bg-emerald-500/10 text-emerald-300 ring-emerald-400/30"
              : "bg-rose-500/10 text-rose-300 ring-rose-400/30"
          }`}
        >
          <Radio size={13} className={isConnected ? "animate-pulse" : ""} />
          {isConnected ? "LIVE FEED" : "DISCONNECTED"}
        </div>
      </header>

      {socketError && (
        <div className="mt-4 rounded-lg border border-rose-400/30 bg-rose-500/10 px-4 py-2.5 font-mono text-xs text-rose-300">
          {socketError}
        </div>
      )}

      <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile
          icon={<Skull size={15} />}
          label="Bankrupt Agents"
          value={latest ? String(latest.agents.bankrupt_total) : "—"}
          accent={latest && latest.agents.bankrupt_total > 0 ? "text-rose-400" : "text-slate-200"}
        />
        <StatTile
          icon={<BarChart3 size={15} />}
          label="Order Volume"
          value={latest ? latest.total_volume.toLocaleString() : "—"}
          accent="text-slate-200"
        />
        <StatTile
          icon={<Activity size={15} />}
          label="Stress Index"
          value={latest ? stress.toFixed(3) : "—"}
          accent={stressTone}
        />
        <StatTile
          icon={<Newspaper size={15} />}
          label="Active Headline"
          value={latest?.news_headline?.trim() ? latest.news_headline : "No macro shock"}
          accent="text-slate-300"
          isText
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_360px]">
        <MarketChart ticks={ticks} />
        <div className="flex flex-col gap-4">
          <ControlPanel />
          <section className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-md">
            <h2 className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
              Tick Telemetry
            </h2>
            {latest ? (
              <dl className="mt-3 space-y-2 font-mono text-xs">
                <TelemetryRow label="tick_id" value={String(latest.tick_id)} />
                <TelemetryRow
                  label="timesfm_prediction"
                  value={`$${latest.timesfm_prediction.toFixed(2)}`}
                />
                <TelemetryRow
                  label="cleared_transactions"
                  value={String(latest.cleared_transactions)}
                />
                <TelemetryRow label="orders_submitted" value={String(latest.orders_submitted)} />
                <TelemetryRow
                  label="retail / institutional"
                  value={`${latest.agents.retail_active} / ${latest.agents.institutional_active}`}
                />
              </dl>
            ) : (
              <p className="mt-3 font-mono text-xs text-slate-600">
                Telemetry appears after the first tick.
              </p>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

function StatTile({
  icon,
  label,
  value,
  accent,
  isText = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  accent: string;
  isText?: boolean;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 backdrop-blur-md">
      <div className="flex items-center gap-2 text-slate-500">
        {icon}
        <span className="font-display text-[11px] font-semibold uppercase tracking-[0.18em]">
          {label}
        </span>
      </div>
      <div
        className={`mt-2 ${accent} ${
          isText
            ? "line-clamp-2 text-sm leading-snug"
            : "font-mono text-2xl font-semibold tabular-nums"
        }`}
        title={isText ? value : undefined}
      >
        {value}
      </div>
    </div>
  );
}

function TelemetryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="tabular-nums text-slate-200">{value}</dd>
    </div>
  );
}
