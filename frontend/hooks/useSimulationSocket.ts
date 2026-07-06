"use client";

import { useEffect, useRef, useState } from "react";

export interface TickTelemetry {
  tick_id: number;
  clearing_price: number;
  previous_price: number;
  timesfm_prediction: number;
  total_volume: number;
  cleared_transactions: number;
  orders_submitted: number;
  system_stress_index: number;
  news_headline: string;
  agents: {
    retail_active: number;
    institutional_active: number;
    bankrupt_total: number;
  };
  timestamp: string | null;
}

const MAX_BUFFERED_TICKS = 100;
const RECONNECT_DELAY_MS = 3000;
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws";

export function useSimulationSocket(url: string = WS_URL) {
  const [ticks, setTicks] = useState<TickTelemetry[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [socketError, setSocketError] = useState<string | null>(null);
  const disposedRef = useRef(false);

  useEffect(() => {
    disposedRef.current = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (disposedRef.current) return;
      socket = new WebSocket(url);

      socket.onopen = () => {
        setIsConnected(true);
        setSocketError(null);
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        const payload = JSON.parse(event.data);
        if ("error" in payload) {
          setSocketError(String(payload.error));
          return;
        }
        setTicks((prev) => {
          const next = [...prev, payload as TickTelemetry];
          return next.slice(-MAX_BUFFERED_TICKS);
        });
      };

      socket.onclose = () => {
        setIsConnected(false);
        if (!disposedRef.current) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };

      socket.onerror = () => {
        socket?.close();
      };
    };

    connect();

    return () => {
      disposedRef.current = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [url]);

  const latest: TickTelemetry | null =
    ticks.length > 0 ? ticks[ticks.length - 1] : null;

  return { ticks, latest, isConnected, socketError };
}
