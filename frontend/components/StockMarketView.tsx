"use client";

// Stock Market view — trading-terminal styling: deep-blue gradient canvas,
// green/red candlesticks bucketed from the real clearing-price series, blue
// volume bars, and a glowing cyan trend line. All data is real (backfilled
// from REST, kept live off the store); OHLC is aggregated from ticks, never
// fabricated.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  ColorType,
  CrosshairMode,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  UTCTimestamp,
  CandlestickData,
  HistogramData,
} from "lightweight-charts";
import { useStore, useCompanyList } from "@/lib/store";
import { API_BASE } from "@/lib/socket";
import { fmtPrice, fmtPct, changeClass } from "@/lib/format";
import type { PricePoint } from "@/lib/types";

const UP = "#16c60c";
const DOWN = "#ff4d4f";
const VOL_UP = "rgba(22,198,12,0.30)";
const VOL_DOWN = "rgba(255,77,79,0.30)";
const TARGET_CANDLES = 60;
// Below this many ticks we can't form multi-tick candles, so shadows (wicks)
// won't appear yet; above it we bucket >=2 ticks per candle so each candle's
// high/low come from real intra-bucket price movement.
const MIN_TICKS_FOR_SHADOWS = 6;
const MIN_BUCKET = 3;

interface Bucketed {
  candles: CandlestickData[];
  volumes: HistogramData[];
}

const DAY_SECONDS = 86400;

// Aggregate tick-level prices into OHLC candles the way a real chart does:
// one candle per simulated DAY, with open/high/low/close taken from that
// day's intraday ticks (real wicks whenever the sim runs >1 tick/day).
// Falls back to count-based bucketing when there's at most one tick per day.
function bucketize(points: PricePoint[]): Bucketed {
  const clean = points.filter((p) => p.price > 0).sort((a, b) => a.t - b.t);
  if (clean.length === 0) return { candles: [], volumes: [] };

  // Group by simulated calendar day.
  const byDay = new Map<number, PricePoint[]>();
  for (const p of clean) {
    const day = Math.floor(p.t / DAY_SECONDS);
    const arr = byDay.get(day);
    if (arr) arr.push(p);
    else byDay.set(day, [p]);
  }
  const intraday = clean.length / byDay.size >= 2;

  const buckets: PricePoint[][] = [];
  const times: number[] = [];
  if (intraday) {
    for (const [day, arr] of [...byDay.entries()].sort((a, b) => a[0] - b[0])) {
      buckets.push(arr);
      times.push(day * DAY_SECONDS);
    }
  } else {
    // One tick per day (or sparser): bucket a few ticks per candle so
    // high/low still come from real price movement.
    const k =
      clean.length >= MIN_TICKS_FOR_SHADOWS
        ? Math.max(MIN_BUCKET, Math.round(clean.length / TARGET_CANDLES))
        : 1;
    for (let i = 0; i < clean.length; i += k) {
      const bucket = clean.slice(i, i + k);
      buckets.push(bucket);
      times.push(bucket[bucket.length - 1].t);
    }
  }

  const candles: CandlestickData[] = [];
  const volumes: HistogramData[] = [];
  const seen = new Set<number>();
  buckets.forEach((bucket, i) => {
    const prices = bucket.map((p) => p.price);
    const open = prices[0];
    const close = prices[prices.length - 1];
    const high = Math.max(...prices);
    const low = Math.min(...prices);
    const vol = bucket.reduce((sum, p) => sum + (p.volume ?? 0), 0);
    let t = times[i];
    while (seen.has(t)) t += 1; // strictly-ascending unique times
    seen.add(t);
    const time = t as UTCTimestamp;
    candles.push({ time, open, high, low, close });
    volumes.push({ time, value: vol, color: close >= open ? VOL_UP : VOL_DOWN });
  });
  return { candles, volumes };
}

export default function StockMarketView() {
  const companies = useCompanyList();
  // Local ticker selection so switching symbols here does NOT open the global
  // CompanyDetail drawer (that's driven by the store's selectedTicker).
  const [activeTicker, setActiveTicker] = useState<string | null>(
    () => useStore.getState().selectedTicker,
  );
  useEffect(() => {
    if (!activeTicker && companies.length > 0) setActiveTicker(companies[0].ticker);
  }, [companies, activeTicker]);

  const points = useStore((s) => (activeTicker ? s.priceSeries[activeTicker] : undefined));
  const company = useStore((s) => (activeTicker ? s.companies[activeTicker] : undefined));

  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  // Build chart once per active ticker; backfill from REST (now with volume).
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !activeTicker) return;

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
        vertLines: { color: "rgba(255,255,255,0.05)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.08)",
        scaleMargins: { top: 0.06, bottom: 0.26 },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.08)",
        // Point times are simulated epoch seconds, so the axis reads like a
        // real trading chart (dates/times), not tick ordinals.
        timeVisible: true,
        secondsVisible: false,
        // Thin candles, fixed width so sparse data doesn't stretch into
        // huge blocks.
        barSpacing: 6,
        minBarSpacing: 2,
        rightOffset: 4,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(255,255,255,0.15)", labelBackgroundColor: "#111" },
        horzLine: { color: "rgba(255,255,255,0.15)", labelBackgroundColor: "#111" },
      },
      handleScroll: true,
      handleScale: true,
    });

    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      color: VOL_UP,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });

    const candle = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      borderVisible: true,
      wickUpColor: "#4fe04a",
      wickDownColor: "#ff6b6d",
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });

    chartRef.current = chart;
    volRef.current = volume;
    candleRef.current = candle;

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    });
    ro.observe(el);

    let disposed = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/api/companies/${encodeURIComponent(activeTicker)}/prices?limit=512`,
          { signal: controller.signal },
        );
        if (!res.ok || disposed) return;
        const data = (await res.json()) as { prices: PricePoint[] };
        if (disposed) return;
        useStore.getState().mergePriceSeries(activeTicker, data.prices ?? []);
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
      candleRef.current = null;
      volRef.current = null;
    };
  }, [activeTicker]);

  const bucketed = useMemo(() => bucketize(points ?? []), [points]);

  useEffect(() => {
    if (!candleRef.current || !volRef.current || !chartRef.current) return;
    candleRef.current.setData(bucketed.candles);
    volRef.current.setData(bucketed.volumes);
    // Never fitContent(): stretching a handful of candles across the full
    // width produces giant blocks. Fixed barSpacing keeps candles thin and
    // uniform (real-chart behavior). While the whole run still fits in the
    // pane, pin the FIRST candle to the left edge so the tape grows
    // rightward from the session open; once it overflows, follow the live
    // edge like a real terminal.
    if (bucketed.candles.length > 0) {
      const ts = chartRef.current.timeScale();
      const paneWidth = (containerRef.current?.clientWidth ?? 0) - 70; // price scale
      const capacity = Math.max(10, Math.floor(paneWidth / 6)); // bars at 6px spacing
      if (bucketed.candles.length <= capacity) {
        ts.setVisibleLogicalRange({ from: -1, to: capacity });
      } else {
        ts.scrollToRealTime();
      }
    }
  }, [bucketed]);

  const hasData = bucketed.candles.length > 0;
  const divergence =
    company && company.anchor_price !== 0
      ? ((company.current_price - company.anchor_price) / company.anchor_price) * 100
      : 0;

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center gap-2 overflow-x-auto scroll-thin pb-1">
        {companies.map((c) => {
          const on = c.ticker === activeTicker;
          return (
            <button
              key={c.ticker}
              onClick={() => setActiveTicker(c.ticker)}
              className={`shrink-0 rounded-md border px-2.5 py-1 text-xs transition ${
                on
                  ? "border-white/25 bg-white/10 text-ink"
                  : "border-hair text-ink2 hover:bg-white/[0.04] hover:text-ink"
              }`}
            >
              <span className="tnum">{c.ticker}</span>
            </button>
          );
        })}
      </div>

      {company && (
        <div className="mb-2 flex items-baseline gap-3 px-0.5">
          <span className="font-display text-lg font-semibold text-ink">{company.name}</span>
          <span className="tnum text-2xl font-semibold text-ink">
            {fmtPrice(company.current_price)}
          </span>
          <span className={`tnum text-sm ${changeClass(company.change_pct)}`}>
            {fmtPct(company.change_pct)}
          </span>
          <span className="ml-auto text-[11px] text-ink3">
            anchor {fmtPrice(company.anchor_price)} · sim{" "}
            <span className={changeClass(divergence)}>{fmtPct(divergence)}</span>
          </span>
        </div>
      )}

      <div className="relative flex-1 overflow-hidden rounded-lg">
        <div ref={containerRef} className="absolute inset-0" />
        {!hasData && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <span className="section-title text-ink3">NO PRICE HISTORY YET</span>
          </div>
        )}
      </div>
    </div>
  );
}
