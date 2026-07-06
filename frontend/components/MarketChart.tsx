"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TickTelemetry } from "@/hooks/useSimulationSocket";

interface MarketChartProps {
  ticks: TickTelemetry[];
}

const EMERALD = "#10b981";
const ROSE = "#f43f5e";

export default function MarketChart({ ticks }: MarketChartProps) {
  // Trend over the visible window decides the chart's mood.
  const isBullish =
    ticks.length < 2 ||
    ticks[ticks.length - 1].clearing_price >= ticks[0].clearing_price;
  const tone = isBullish ? EMERALD : ROSE;

  return (
    <section
      className="rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-md"
      style={{ boxShadow: `0 0 60px -18px ${tone}55, inset 0 1px 0 0 rgba(255,255,255,0.04)` }}
    >
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
          Clearing Price
        </h2>
        {ticks.length > 0 && (
          <span
            className="font-mono text-2xl font-semibold tabular-nums"
            style={{ color: tone, textShadow: `0 0 18px ${tone}66` }}
          >
            ${ticks[ticks.length - 1].clearing_price.toFixed(2)}
          </span>
        )}
      </div>

      <div className="mt-4 h-[340px]">
        {ticks.length === 0 ? (
          <div className="flex h-full items-center justify-center font-mono text-sm text-slate-600">
            Awaiting first tick — start the simulation.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={ticks} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={tone} stopOpacity={0.45} />
                  <stop offset="60%" stopColor={tone} stopOpacity={0.08} />
                  <stop offset="100%" stopColor={tone} stopOpacity={0} />
                </linearGradient>
                <filter id="priceGlow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="4" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>
              <CartesianGrid stroke="rgba(148,163,184,0.08)" vertical={false} />
              <XAxis
                dataKey="tick_id"
                stroke="rgba(148,163,184,0.35)"
                tick={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
                tickLine={false}
                axisLine={false}
                minTickGap={24}
              />
              <YAxis
                domain={["auto", "auto"]}
                stroke="rgba(148,163,184,0.35)"
                tick={{ fontSize: 11, fontFamily: "var(--font-mono)" }}
                tickLine={false}
                axisLine={false}
                width={56}
                tickFormatter={(v: number) => `$${v.toFixed(0)}`}
              />
              <Tooltip
                contentStyle={{
                  background: "rgba(2,6,23,0.92)",
                  border: "1px solid rgba(148,163,184,0.2)",
                  borderRadius: 10,
                  fontFamily: "var(--font-mono)",
                  fontSize: 12,
                }}
                labelFormatter={(label) => `Tick ${label}`}
                formatter={(value) => [
                  `$${Number(value).toFixed(2)}`,
                  "Clearing price",
                ]}
              />
              <Area
                type="monotone"
                dataKey="clearing_price"
                stroke={tone}
                strokeWidth={2}
                fill="url(#priceFill)"
                filter="url(#priceGlow)"
                isAnimationActive={false}
                dot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  );
}
