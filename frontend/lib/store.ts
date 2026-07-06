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
} from "./types";

const PRICE_SERIES_CAP = 512;
const SOCIAL_CAP = 100;

interface StoreState {
  companies: Record<string, Company>;
  priceSeries: Record<string, PricePoint[]>;
  social: SocialPostT[];
  economy: Economy | null;
  indices: MarketIndex[];
  news: string;
  tickId: number;
  connectionStatus: ConnectionStatus;
  selectedTicker: string | null;
  watchlist: string[];
  hydrated: boolean;

  // actions
  hydrate: (snapshot: StateSnapshot) => void;
  setConnection: (status: ConnectionStatus) => void;
  setIndices: (indices: MarketIndex[]) => void;
  setSelectedTicker: (ticker: string | null) => void;
  toggleWatch: (ticker: string) => void;
  applyPriceUpdate: (
    prices: { ticker: string; price: number; change_pct: number; volume: number }[],
    tickId: number,
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
  setTick: (tickId: number) => void;
  mergePriceSeries: (ticker: string, points: PricePoint[]) => void;
}

export const useStore = create<StoreState>((set) => ({
  companies: {},
  priceSeries: {},
  social: [],
  economy: null,
  indices: [],
  news: "",
  tickId: 0,
  connectionStatus: "connecting",
  selectedTicker: null,
  watchlist: ["AAPL", "NVDA", "TSLA", "JPM", "2222.SR"],
  hydrated: false,

  hydrate: (snapshot) =>
    set(() => {
      const companies: Record<string, Company> = {};
      for (const c of snapshot.companies) companies[c.ticker] = c;
      const social = snapshot.social.slice(0, SOCIAL_CAP);
      return {
        companies,
        social,
        economy: snapshot.economy ?? null,
        news: "",
        tickId: snapshot.tick_id,
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

  applyPriceUpdate: (prices, tickId) =>
    set((s) => {
      const companies = { ...s.companies };
      const priceSeries = { ...s.priceSeries };
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
        series.push({ t: tickId, price: p.price });
        priceSeries[p.ticker] = series.slice(-PRICE_SERIES_CAP);
      }
      return { companies, priceSeries };
    }),

  applyCompanyUpdate: (updates) =>
    set((s) => {
      const companies = { ...s.companies };
      for (const u of updates) {
        const existing = companies[u.ticker];
        if (existing) {
          companies[u.ticker] = {
            ...existing,
            sentiment: u.sentiment,
            volatility: u.volatility,
            market_cap: u.market_cap,
            is_bankrupt: u.is_bankrupt,
          };
        }
      }
      return { companies };
    }),

  addSocialPost: (post) =>
    set((s) => ({ social: [post, ...s.social].slice(0, SOCIAL_CAP) })),

  setEconomy: (economy) => set({ economy }),
  setNews: (news) => set({ news }),
  setTick: (tickId) => set({ tickId }),

  mergePriceSeries: (ticker, points) =>
    set((s) => {
      // Backfill from REST: keep the longer of (backfill, live) and dedupe by t.
      const live = s.priceSeries[ticker] ?? [];
      const byT = new Map<number, number>();
      for (const p of points) byT.set(p.t, p.price);
      for (const p of live) byT.set(p.t, p.price);
      const merged = Array.from(byT.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([t, price]) => ({ t, price }))
        .slice(-PRICE_SERIES_CAP);
      return { priceSeries: { ...s.priceSeries, [ticker]: merged } };
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
