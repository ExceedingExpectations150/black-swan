"use client";

// The single live connection: REST hydration (/api/state), one WebSocket with
// exponential-backoff reconnect dispatching envelope events into the store, and
// /api/indices polling for the real-market strip. Never blanks state on drop.

import { useEffect } from "react";
import { useStore } from "./store";
import type {
  CompanyUpdatePayload,
  Envelope,
  MarketIndex,
  PriceUpdatePayload,
  SocialPostT,
  StateSnapshot,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws";
const INDICES_POLL_MS = 25_000;
const RECONNECT_MAX_MS = 15_000;

function dispatch(env: Envelope): void {
  const s = useStore.getState();
  switch (env.type) {
    case "tick_start":
      s.setLatestTick(env.tick_id);
      break;
    case "news":
      s.setNews((env.payload as { headline: string }).headline);
      break;
    case "price_update":
      s.applyPriceUpdate((env.payload as PriceUpdatePayload).prices, env.tick_id);
      break;
    case "company_update":
      s.applyCompanyUpdate((env.payload as CompanyUpdatePayload).companies);
      break;
    case "social_post":
      s.addSocialPost(env.payload as SocialPostT);
      break;
    case "economy_update":
      s.setEconomy(env.payload as never);
      break;
    case "sim_status": {
      const p = env.payload as any;
      s.setSimStatus(p.paused, p.tick_interval_seconds, p.pausing_in_progress, p.max_ticks, p.duration_days, p.ticks_per_day);
      break;
    }
    case "reset":
      // Hard refresh to fully clear frontend state safely
      window.location.reload();
      break;
    case "tick_end":
    default:
      break;
  }
}

async function hydrate(): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/state`);
    if (!res.ok) return;
    const snapshot = (await res.json()) as StateSnapshot;
    useStore.getState().hydrate(snapshot);
  } catch {
    // backend not up yet; WS reconnect + next poll will recover
  }
}

async function pollIndices(): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/api/indices`);
    if (!res.ok) return;
    const data = (await res.json()) as { indices: MarketIndex[] };
    useStore.getState().setIndices(data.indices ?? []);
  } catch {
    // leave the last good strip; the UI shows "—" only when it was never set
  }
}

export function useLiveConnection(): void {
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    void hydrate();
    void pollIndices();
    const indicesTimer = setInterval(() => void pollIndices(), INDICES_POLL_MS);

    const connect = () => {
      if (disposed) return;
      useStore.getState().setConnection(attempt === 0 ? "connecting" : "connecting");
      socket = new WebSocket(WS_URL);

      socket.onopen = () => {
        attempt = 0;
        useStore.getState().setConnection("open");
        // Re-hydrate on (re)connect so a mid-run refresh catches up.
        void hydrate();
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const env = JSON.parse(event.data) as Envelope;
          if (env && typeof env.type === "string") dispatch(env);
        } catch {
          // ignore malformed frame
        }
      };

      socket.onclose = () => {
        useStore.getState().setConnection("closed");
        if (disposed) return;
        attempt += 1;
        const delay = Math.min(1000 * 2 ** attempt, RECONNECT_MAX_MS);
        reconnectTimer = setTimeout(connect, delay);
      };

      socket.onerror = () => socket?.close();
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      clearInterval(indicesTimer);
      socket?.close();
    };
  }, []);
}
