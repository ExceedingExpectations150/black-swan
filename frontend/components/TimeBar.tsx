"use client";

// Simulated-time timeline. The world advances by ticks; the backend maps each
// tick to a trading-session clock (Day N / HH:MM) with a 0..1 session progress
// that fills this bar. Purely reads the store clock set on every tick_start.

import { useStore } from "@/lib/store";

export default function TimeBar() {
  const clock = useStore((s) => s.clock);
  const tickId = useStore((s) => s.tickId);
  const pct = Math.max(0, Math.min(1, clock?.session_pct ?? 0));

  return (
    <div className="flex items-center gap-3 border-b border-hair bg-white/[0.02] px-4 py-1.5">
      <span className="text-[9px] uppercase tracking-[0.2em] text-ink3">Session</span>
      <span className="tnum whitespace-nowrap text-xs text-ink">
        {clock?.sim_label ?? "Awaiting open"}
      </span>
      <div className="relative h-1 min-w-0 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-[#E85002] transition-[width] duration-500"
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      <span className="text-[9px] uppercase tracking-[0.2em] text-ink3">09:30</span>
      <span className="text-[9px] uppercase tracking-[0.2em] text-ink3">16:00</span>
      <span className="tnum ml-2 whitespace-nowrap text-[10px] text-ink3">TICK {tickId}</span>
    </div>
  );
}
