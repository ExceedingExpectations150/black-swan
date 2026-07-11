"use client";

// Single live store for the Black Swan global-intelligence terminal.
// The WebSocket client (lib/socket.ts) is the only writer of live deltas;
// components read via selectors. Dispatch is purely on message.type — never
// assume ordering. Nothing here fabricates data.

import { useMemo } from "react";
import { create } from "zustand";
import type {
  Company,
  ConnectionStatus,
  Economy,
  MarketIndex,
  PricePoint,
  SocialPostT,
  StateSnapshot,
  VolumePoint,
  Alert,
} from "./types";

const PRICE_SERIES_CAP = 512;
const SOCIAL_CAP = 100;

interface StoreState {
  companies: Record<string, Company>;
  priceSeries: Record<string, PricePoint[]>;
  volumeSeries: Record<string, VolumePoint[]>;
  social: SocialPostT[];
  economy: Economy | null;
  indices: MarketIndex[];
  latestTickId: number;
  scrubbedTickId: number | null;
  news: string;
  tickId: number;
  connectionStatus: ConnectionStatus;
  selectedTicker: string | null;
  watchlist: string[];
  hydrated: boolean;
  alerts: Alert[];
  isPaused: boolean;
  isPausing: boolean;
  tickInterval: number;
  maxTicks: number | null;
  durationDays: number | null;
  ticksPerDay: number | null;
  /** Headline armed at the boot terminal — prefills the setup console. */
  armedEvent: string;
  /** Current simulated time (epoch seconds) — advances with the sim clock. */
  simTime: number | null;

  // actions
  hydrate: (snapshot: StateSnapshot) => void;
  setConnection: (status: ConnectionStatus) => void;
  setIndices: (indices: MarketIndex[]) => void;
  setSelectedTicker: (ticker: string | null) => void;
  toggleWatch: (ticker: string) => void;
  applyPriceUpdate: (
    prices: { ticker: string; price: number; change_pct: number; volume: number }[],
    tickId: number,
    tsEpoch?: number,
  ) => void;
  applyCompanyUpdate: (
    updates: {
      ticker: string;
      sentiment: number;
      volatility: number;
      market_cap: number;
      is_bankrupt: boolean;
    }[],
  ) => void;
  addSocialPost: (post: SocialPostT) => void;
  setEconomy: (economy: Economy) => void;
  setNews: (headline: string) => void;
  setLatestTick: (tickId: number) => void;
  mergePriceSeries: (ticker: string, points: PricePoint[]) => void;
  dismissAlert: (id: string) => void;
  markAllAlertsRead: () => void;
  fetchHistory: (tickId: number) => Promise<void>;
  clearHistory: () => void;
  setSimStatus: (paused: boolean, tickInterval: number, isPausing?: boolean, maxTicks?: number | null, durationDays?: number | null, ticksPerDay?: number | null) => void;
  setArmedEvent: (headline: string) => void;
  setSimTime: (epochSeconds: number) => void;
  /** Clear all per-run state after the backend wiped the world (soft reset —
   *  no page reload, so a client sitting on the boot terminal is unaffected). */
  resetWorld: () => void;
}

export const useStore = create<StoreState>((set) => ({
  companies: {},
  priceSeries: {},
  volumeSeries: {},
  social: [],
  economy: null,
  indices: [],
  latestTickId: 0,
  scrubbedTickId: null,
  news: "Awaiting market open...",
  tickId: 0,
  connectionStatus: "connecting",
  selectedTicker: null,
  watchlist: ["AAPL", "NVDA", "TSLA", "JPM", "2222.SR"],
  hydrated: false,
  alerts: [],
  isPaused: false,
  isPausing: false,
  tickInterval: 1.0,
  maxTicks: null,
  durationDays: null,
  ticksPerDay: null,
  armedEvent: "",
  simTime: null,

  hydrate: (snapshot) =>
    set((s) => {
      const companies: Record<string, Company> = {};
      for (const c of snapshot.companies) companies[c.ticker] = c;
      const social = snapshot.social.slice(0, SOCIAL_CAP);
      // If the backend's world rewound (fresh DB after a wipe, or a process
      // restart), stale per-run series from the previous world must go —
      // otherwise charts silently merge points from two different runs.
      const isFreshDB = snapshot.tick_id === 0;
      const worldRewound = isFreshDB || snapshot.tick_id < s.latestTickId;
      if (worldRewound) {
        return {
          companies,
          social,
          economy: snapshot.economy ?? null,
          news: "",
          tickId: snapshot.tick_id,
          latestTickId: snapshot.tick_id,
          priceSeries: {},
          volumeSeries: {},
          alerts: [],
          scrubbedTickId: null,
          simTime: null,
          isPaused: snapshot.paused ?? false,
          tickInterval: snapshot.tick_interval_seconds ?? s.tickInterval,
          maxTicks: snapshot.max_ticks ?? null,
          durationDays: snapshot.duration_days ?? null,
          ticksPerDay: snapshot.ticks_per_day ?? null,
          hydrated: true,
        };
      }

      return {
        companies,
        social,
        economy: snapshot.economy ?? null,
        news: "",
        tickId: snapshot.tick_id,
        latestTickId: isFreshDB ? 0 : Math.max(s.latestTickId, snapshot.tick_id),
        isPaused: snapshot.paused ?? s.isPaused,
        tickInterval: snapshot.tick_interval_seconds ?? s.tickInterval,
        maxTicks: snapshot.max_ticks ?? s.maxTicks,
        durationDays: snapshot.duration_days ?? s.durationDays,
        ticksPerDay: snapshot.ticks_per_day ?? s.ticksPerDay,
        hydrated: true,
      };
    }),

  setConnection: (connectionStatus) => set({ connectionStatus }),
  setIndices: (indices) => set({ indices }),
  setSelectedTicker: (selectedTicker) => set({ selectedTicker }),

  toggleWatch: (ticker) =>
    set((s) => ({
      watchlist: s.watchlist.includes(ticker)
        ? s.watchlist.filter((t) => t !== ticker)
        : [...s.watchlist, ticker],
    })),

  applyPriceUpdate: (prices, tickId, tsEpoch) =>
    set((s) => {
      if (s.scrubbedTickId !== null) return s;
      // Series points are keyed by simulated time; fall back to the tick
      // ordinal only if the envelope carried no usable timestamp.
      const t = tsEpoch ?? tickId;
      const companies = { ...s.companies };
      const priceSeries = { ...s.priceSeries };
      const volumeSeries = { ...s.volumeSeries };
      for (const p of prices) {
        const existing = companies[p.ticker];
        if (existing) {
          companies[p.ticker] = {
            ...existing,
            current_price: p.price,
            change_pct: p.change_pct,
            market_cap: p.price * existing.shares_outstanding,
          };
        }
        const series = priceSeries[p.ticker] ? [...priceSeries[p.ticker]] : [];
        series.push({ t, tick: tickId, price: p.price, volume: p.volume });
        priceSeries[p.ticker] = series.slice(-PRICE_SERIES_CAP);

        const vol = volumeSeries[p.ticker] ? [...volumeSeries[p.ticker]] : [];
        vol.push({ t, v: p.volume });
        volumeSeries[p.ticker] = vol.slice(-PRICE_SERIES_CAP);
      }
      return { companies, priceSeries, volumeSeries, tickId };
    }),

  applyCompanyUpdate: (updates) =>
    set((s) => {
      if (s.scrubbedTickId !== null) return s;
      const companies = { ...s.companies };
      const newAlerts: Alert[] = [];
      const now = new Date().toISOString();
      for (const u of updates) {
        const existing = companies[u.ticker];
        if (existing) {
          if (!existing.is_bankrupt && u.is_bankrupt) {
             newAlerts.push({
               id: Math.random().toString(36).substring(7),
               tick_id: s.latestTickId,
               ts: now,
               severity: "critical",
               title: "Bankruptcy Declared",
               message: `${existing.name} (${existing.ticker}) has filed for bankruptcy.`,
               read: false,
             });
          }
          companies[u.ticker] = {
            ...existing,
            sentiment: u.sentiment,
            volatility: u.volatility,
            market_cap: u.market_cap,
            is_bankrupt: u.is_bankrupt,
          };
        }
      }
      return { 
        companies, 
        alerts: newAlerts.length > 0 ? [...newAlerts, ...s.alerts].slice(0, 100) : s.alerts 
      };
    }),

  addSocialPost: (post) =>
    set((s) => {
      if (s.scrubbedTickId !== null) return s;
      return { social: [post, ...s.social].slice(0, SOCIAL_CAP) };
    }),

  setEconomy: (economy) => set((s) => {
    if (s.scrubbedTickId !== null) return s;
    const newAlerts: Alert[] = [];
    if (s.economy && s.economy.system_stress_index < 0.8 && economy.system_stress_index >= 0.8) {
      newAlerts.push({
         id: Math.random().toString(36).substring(7),
         tick_id: economy.tick_id,
         ts: new Date().toISOString(),
         severity: "critical",
         title: "Extreme Market Stress",
         message: `System stress index has reached ${economy.system_stress_index.toFixed(2)}. Contagion risk is high.`,
         read: false,
      });
    } else if (s.economy && s.economy.system_stress_index < 0.6 && economy.system_stress_index >= 0.6) {
      newAlerts.push({
         id: Math.random().toString(36).substring(7),
         tick_id: economy.tick_id,
         ts: new Date().toISOString(),
         severity: "warning",
         title: "Elevated Market Stress",
         message: `System stress index is climbing (${economy.system_stress_index.toFixed(2)}).`,
         read: false,
      });
    }
    return {
      economy,
      alerts: newAlerts.length > 0 ? [...newAlerts, ...s.alerts].slice(0, 100) : s.alerts
    };
  }),
  setNews: (news) => set((s) => s.scrubbedTickId !== null ? s : { news }),
  setLatestTick: (tickId) => set({ latestTickId: tickId }),

  mergePriceSeries: (ticker, points) =>
    set((s) => {
      const live = s.priceSeries[ticker] ?? [];
      const byT = new Map<number, PricePoint>();
      for (const p of points) byT.set(p.t, p);
      for (const p of live) byT.set(p.t, p);
      const merged = Array.from(byT.values())
        .sort((a, b) => a.t - b.t)
        .slice(-PRICE_SERIES_CAP);

      const liveVol = s.volumeSeries[ticker] ?? [];
      const volByT = new Map<number, number>();
      for (const p of points) if (p.volume != null) volByT.set(p.t, p.volume);
      for (const p of liveVol) volByT.set(p.t, p.v);
      const mergedVol = Array.from(volByT.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([t, v]) => ({ t, v }))
        .slice(-PRICE_SERIES_CAP);

      return {
        priceSeries: { ...s.priceSeries, [ticker]: merged },
        volumeSeries: { ...s.volumeSeries, [ticker]: mergedVol },
      };
    }),

  dismissAlert: (id) => set((s) => ({ alerts: s.alerts.filter((a) => a.id !== id) })),
  markAllAlertsRead: () => set((s) => ({ alerts: s.alerts.map((a) => ({ ...a, read: true })) })),

  fetchHistory: async (tickId) => {
    set({ scrubbedTickId: tickId });
    try {
      const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
      const res = await fetch(`${API_BASE}/api/history/${tickId}`);
      if (!res.ok) {
        // No snapshot for that tick — release the scrub freeze instead of
        // leaving the dashboard stuck on stale data with no history shown.
        set({ scrubbedTickId: null });
        return;
      }
      const snapshot = await res.json();
      set((s) => {
        const companies = { ...s.companies };
        for (const c of snapshot.companies) companies[c.ticker] = c;
        return {
           companies,
           economy: snapshot.economy,
           social: snapshot.social,
           tickId: snapshot.tick_id, // Fix: Use tickId instead of overwriting latestTickId
           hydrated: true,
        };
      });
    } catch {
      //
    }
  },

  clearHistory: () => {
    set({ scrubbedTickId: null });
    // so we trigger a hydrate immediately.
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
    fetch(`${API_BASE}/api/state`).then(r => r.json()).then(snapshot => {
      useStore.getState().hydrate(snapshot);
    }).catch(() => {});
  },

  setSimStatus: (paused, interval, isPausing = false, maxTicks = null, durationDays = null, ticksPerDay = null) =>
    set({ isPaused: paused, tickInterval: interval, isPausing, maxTicks, durationDays, ticksPerDay }),

  setArmedEvent: (headline) => set({ armedEvent: headline }),

  setSimTime: (epochSeconds) => set({ simTime: epochSeconds }),

  resetWorld: () =>
    set({
      priceSeries: {},
      volumeSeries: {},
      social: [],
      economy: null,
      alerts: [],
      news: "Awaiting market open...",
      tickId: 0,
      latestTickId: 0,
      scrubbedTickId: null,
      simTime: null,
      isPaused: false,
      isPausing: false,
      maxTicks: null,
      durationDays: null,
      ticksPerDay: null,
    }),
}));

// Company list as a MEMOIZED hook. A raw selector returning Object.values()
// would allocate a new array every render and drive zustand into an infinite
// loop ("getServerSnapshot should be cached"); select the stable record and
// derive the array with useMemo instead.
export function useCompanyList(): Company[] {
  const companies = useStore((s) => s.companies);
  return useMemo(() => Object.values(companies), [companies]);
}
