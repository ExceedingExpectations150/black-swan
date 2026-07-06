"use client";

import { Area, AreaChart, ResponsiveContainer, YAxis } from "recharts";

interface SparklineProps {
  data: number[];
  up?: boolean;
  height?: number;
  width?: number;
}

// Tiny trend line for index rows, watchlist rows, market-status card.
export default function Sparkline({ data, up = true, height = 22, width }: SparklineProps) {
  if (!data || data.length < 2) {
    return <div style={{ height, width }} />;
  }
  const color = up ? "var(--up)" : "var(--down)";
  const chartData = data.map((v, i) => ({ i, v }));
  const min = Math.min(...data);
  const max = Math.max(...data);
  const body = (
    <AreaChart data={chartData} margin={{ top: 1, right: 0, bottom: 1, left: 0 }}>
      <YAxis hide domain={[min, max]} />
      <Area
        type="monotone"
        dataKey="v"
        stroke={color}
        strokeWidth={1.4}
        fill={color}
        fillOpacity={0.12}
        isAnimationActive={false}
        dot={false}
      />
    </AreaChart>
  );
  if (width) {
    return (
      <div style={{ width, height }}>
        <ResponsiveContainer width="100%" height="100%">
          {body}
        </ResponsiveContainer>
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      {body}
    </ResponsiveContainer>
  );
}
