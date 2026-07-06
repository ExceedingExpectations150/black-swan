"use client";

// Live per-ticker price chart (lightweight-charts v5). Area series over tick
// index, with a REAL-market anchor reference line and a SIM-vs-REAL divergence
// readout. Data is backfilled from REST then kept live off the store — never
// fabricated.

import { useEffect, useRef } from "react";
import {
  createChart,
  AreaSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  IPriceLine,
  UTCTimestamp,
  AreaData,
  Time,
} from "lightweight-charts";
import { useStore } from "@/lib/store";
import { API_BASE } from "@/lib/socket";
import { fmtPrice, fmtPct, changeClass } from "@/lib/format";
import type { PricePoint } from "@/lib/types";

const UP = "#16c60c";
const DOWN = "#ff4d4f";
const GRID = "rgba(255,255,255,0.05)";
const HAIR = "rgba(255,255,255,0.07)";
const ANCHOR = "rgba(255,255,255,0.35)";

// lightweight-charts requires strictly-ascending, unique time. Live pushes and
// REST backfill can overlap on tick_id, so dedupe by t here defensively.
function toAreaData(points: PricePoint[]): AreaData[] {
  const byT = new Map<number, number>();
  for (const p of points) byT.set(p.t, p.price);
  return [...byT.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, value]) => ({ time: t as UTCTimestamp, value }));
}

export default function LiveStockChart({ ticker }: { ticker: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const priceLineRef = useRef<IPriceLine | null>(null);

  const points = useStore((s) => s.priceSeries[ticker]);
  const company = useStore((s) => s.companies[ticker]);

  // Create chart + series, wire resize, backfill from REST. Rebuild on ticker.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#5a5a5a",
        fontSize: 10,
        fontFamily: "var(--font-mono), monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: GRID },
        horzLines: { color: GRID },
      },
      rightPriceScale: { borderColor: HAIR },
      timeScale: {
        borderColor: HAIR,
        timeVisible: false,
        secondsVisible: false,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: HAIR, labelBackgroundColor: "#111" },
        horzLine: { color: HAIR, labelBackgroundColor: "#111" },
      },
      // Treat the horizontal scale as a bare tick index, not a clock.
      localization: { timeFormatter: (t: Time) => `#${String(t)}` },
      handleScroll: false,
      handleScale: false,
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: UP,
      topColor: "rgba(22,198,12,0.28)",
      bottomColor: "rgba(22,198,12,0.00)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    });
    ro.observe(el);

    let disposed = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/companies/${encodeURIComponent(ticker)}/prices?limit=512`,
          { signal: controller.signal },
        );
        if (!res.ok || disposed) return;
        const data = (await res.json()) as { prices: PricePoint[] };
        if (disposed) return;
        useStore.getState().mergePriceSeries(ticker, data.prices ?? []);
      } catch {
        // backend not up / aborted — live store still drives the chart
      }
    })();

    return () => {
      disposed = true;
      controller.abort();
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      priceLineRef.current = null;
    };
  }, [ticker]);

  // Push the merged series into the chart; recolor by trend.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    const data = toAreaData(points ?? []);
    series.setData(data);
    if (data.length >= 2) {
      const rising = data[data.length - 1].value >= data[0].value;
      const color = rising ? UP : DOWN;
      series.applyOptions({
        lineColor: color,
        topColor: rising
          ? "rgba(22,198,12,0.28)"
          : "rgba(255,77,79,0.28)",
        bottomColor: rising
          ? "rgba(22,198,12,0.00)"
          : "rgba(255,77,79,0.00)",
      });
    }
    if (data.length > 0) chart.timeScale().fitContent();
  }, [points]);

  // REAL-market anchor reference line.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !company) return;
    if (priceLineRef.current) {
      series.removePriceLine(priceLineRef.current);
      priceLineRef.current = null;
    }
    priceLineRef.current = series.createPriceLine({
      price: company.anchor_price,
      color: ANCHOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "REAL ANCHOR",
    });
  }, [company?.anchor_price, company]);

  const hasData = (points?.length ?? 0) > 0;
  const divergence =
    company && company.anchor_price !== 0
      ? ((company.current_price - company.anchor_price) / company.anchor_price) *
        100
      : 0;

  return (
    <div className="flex h-full flex-col">
      {company && (
        <div className="mb-2 flex items-center justify-between px-0.5">
          <span className="section-title">SIM vs REAL</span>
          <div className="flex items-center gap-2 text-[11px]">
            <span className="tnum text-ink">
              {fmtPrice(company.current_price)}
            </span>
            <span className="text-ink3">/</span>
            <span className="tnum text-ink2">
              {fmtPrice(company.anchor_price)}
            </span>
            <span className={`tnum ${changeClass(divergence)}`}>
              {fmtPct(divergence)}
            </span>
          </div>
        </div>
      )}
      <div className="relative flex-1">
        <div ref={containerRef} className="absolute inset-0" />
        {!hasData && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <span className="section-title text-ink3">
              NO PRICE HISTORY YET
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
