"use client";

// Workstation status bar: one always-visible line of system truth.
// Every readout is live store state — nothing decorative, nothing faked.

import { useMemo } from "react";
import { useStore } from "@/lib/store";

const SPEED_LABELS: Record<string, string> = {
  "6": "0.5x",
  "3": "1x",
  "1.5": "2x",
  "0.6": "5x",
  "0": "MAX",
};

function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-ink3">{label}</span>
      <span className={`tnum ${tone ?? "text-ink2"}`}>{value}</span>
    </span>
  );
}

export default function StatusBar() {
  const connection = useStore((s) => s.connectionStatus);
  const latestTickId = useStore((s) => s.latestTickId);
  const scrubbedTickId = useStore((s) => s.scrubbedTickId);
  const maxTicks = useStore((s) => s.maxTicks);
  const ticksPerDay = useStore((s) => s.ticksPerDay);
  const isPaused = useStore((s) => s.isPaused);
  const tickInterval = useStore((s) => s.tickInterval);
  const simTime = useStore((s) => s.simTime);

  const linkUp = connection === "open";

  const mode = useMemo(() => {
    if (!linkUp) return { text: "LINK DOWN", tone: "text-down" };
    if (scrubbedTickId !== null)
      return { text: `REPLAY @ T${scrubbedTickId}`, tone: "text-warn" };
    if (latestTickId === 0) return { text: "STANDBY", tone: "text-ink2" };
    if (isPaused) return { text: "PAUSED", tone: "text-warn" };
    return { text: "LIVE", tone: "text-accent" };
  }, [linkUp, scrubbedTickId, latestTickId, isPaused]);

  const simStamp = useMemo(() => {
    if (simTime === null) return "—";
    const iso = new Date(simTime * 1000).toISOString();
    return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
  }, [simTime]);

  const day =
    ticksPerDay && latestTickId > 0 ? Math.ceil(latestTickId / ticksPerDay) : null;
  const speed = SPEED_LABELS[String(tickInterval)] ?? `${tickInterval}s`;

  return (
    <footer className="flex h-[26px] shrink-0 items-center gap-5 overflow-hidden border-t border-hair bg-white/[0.015] px-3 font-mono text-[10px] uppercase tracking-[0.08em]">
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            linkUp ? "bg-accent live-dot" : "bg-down"
          }`}
        />
        <span className={linkUp ? "text-ink2" : "text-down"}>
          {linkUp ? "WS LINK" : "WS " + connection.toUpperCase()}
        </span>
      </span>
      <span className={`font-semibold ${mode.tone}`}>{mode.text}</span>
      <span className="min-w-0 flex-1" />
      <Cell label="TICK" value={maxTicks ? `${latestTickId}/${maxTicks}` : String(latestTickId)} />
      {day !== null && <Cell label="DAY" value={String(day)} />}
      {ticksPerDay && <Cell label="RES" value={`${ticksPerDay}/DAY`} />}
      <Cell label="SPD" value={speed} />
      <Cell label="SIM" value={simStamp} tone="text-accent" />
    </footer>
  );
}
