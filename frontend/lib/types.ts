// Shared backend contract (IDENTICAL to the backend prompt). Do not invent
// fields; a missing value is a backend gap, never a reason to fabricate.

export interface Company {
  ticker: string;
  name: string;
  sector: string;
  country: string;
  city: string;
  lat: number;
  lon: number;
  description: string;
  shares_outstanding: number;
  anchor_price: number;
  current_price: number;
  change_pct: number;
  sentiment: number;
  volatility: number;
  market_cap: number;
  is_bankrupt: boolean;
}

export type AuthorType = "company" | "analyst" | "trader";

export interface SocialPostT {
  post_id: string;
  tick_id: number;
  ts: string | null;
  author_type: AuthorType;
  author_ticker: string | null;
  author_display: string;
  handle: string;
  content: string;
  sentiment: number;
  likes: number;
  reposts: number;
}

export interface SectorRollup {
  sector: string;
  avg_sentiment: number;
  avg_change_pct: number;
  market_cap: number;
  companies: string[];
}

export interface Mover {
  ticker: string;
  change_pct: number;
}

export interface VolatileMover {
  ticker: string;
  volatility: number;
}

export interface Economy {
  tick_id: number;
  ts: string;
  system_stress_index: number;
  bankrupt_count: number;
  sectors: SectorRollup[];
  biggest_gainers: Mover[];
  biggest_losers: Mover[];
  most_volatile: VolatileMover[];
  narrative: string;
}

export interface MarketIndex {
  symbol: string;
  name: string;
  value: number;
  change: number;
  change_pct: number;
  sparkline: number[];
}

export interface PricePoint {
  t: number;
  price: number;
  volume?: number;
}

export interface VolumePoint {
  t: number;
  v: number;
}

// WebSocket envelope: { type, tick_id, ts, payload }
export interface Envelope<P = unknown> {
  type: string;
  tick_id: number;
  ts: string;
  payload: P;
}

export interface PriceUpdatePayload {
  prices: { ticker: string; price: number; change_pct: number; volume: number }[];
}

export interface CompanyUpdatePayload {
  companies: {
    ticker: string;
    sentiment: number;
    volatility: number;
    market_cap: number;
    is_bankrupt: boolean;
  }[];
}

export interface StateSnapshot {
  companies: Company[];
  economy: Economy;
  social: SocialPostT[];
  tick_id: number;
  // Sim-status fields are not returned by GET /api/state today (they arrive
  // via the sim_status WS event), so they are optional here.
  paused?: boolean;
  tick_interval_seconds?: number;
  max_ticks?: number | null;
  duration_days?: number | null;
  ticks_per_day?: number | null;
}

export type ConnectionStatus = "connecting" | "open" | "closed";

export interface Alert {
  id: string;
  tick_id: number;
  ts: string;
  severity: "critical" | "warning" | "info";
  title: string;
  message: string;
  read: boolean;
}
