"use client";

import { useEffect, useState } from "react";
import { Play, Loader2, Calendar, Settings, Zap } from "lucide-react";
import { useStore } from "@/lib/store";
import { API_BASE } from "@/lib/socket";

export default function SimulationSetupModal() {
  const latestTickId = useStore((s) => s.latestTickId);
  const connection = useStore((s) => s.connectionStatus);
  const armedEvent = useStore((s) => s.armedEvent);

  // The setup console: shown when connected and the world is at tick 0
  // (every page load resets to state zero, so each session configures its
  // own run here). Disappears once the run starts producing ticks.
  const shouldShow = connection === "open" && latestTickId === 0;

  const [isLoading, setIsLoading] = useState(false);
  const [scenario, setScenario] = useState("");
  // Prefill with the headline armed at the boot terminal.
  useEffect(() => {
    if (armedEvent) setScenario(armedEvent);
  }, [armedEvent]);
  const [durationDays, setDurationDays] = useState(30);
  // 4 ticks/day (6-hour) by default so daily candles carry real OHLC range.
  const [ticksPerDay, setTicksPerDay] = useState(4);
  const [speed, setSpeed] = useState(0.0); // MAX default

  if (!shouldShow) return null;

  const handleStart = async () => {
    setIsLoading(true);
    try {
      if (scenario.trim()) {
        await fetch(`${API_BASE}/api/event`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ headline: scenario.trim() }),
        });
      }
      
      await fetch(`${API_BASE}/api/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          speed,
          duration_days: durationDays,
          ticks_per_day: ticksPerDay,
        }),
      });
    } catch {
      setIsLoading(false); // only reset on error. on success, tick > 0 will unmount it
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85">
      <div className="w-[400px] bg-[#0a0a0a] border border-hair rounded-sm flex flex-col overflow-hidden">
        
        {/* Header */}
        <div className="p-4 border-b border-hair bg-white/[0.02]">
          <h2 className="text-sm tracking-widest text-white flex items-center gap-2">
            <Settings size={16} className="text-accent" />
            NEW SIMULATION SETUP
          </h2>
          <p className="text-xs text-ink3 mt-1">
            Configure the parameters for the new Black Swan scenario.
          </p>
        </div>

        {/* Body */}
        <div className="p-4 flex flex-col gap-6">
          
          {/* Scenario / Context */}
          <div className="flex flex-col gap-2">
            <label className="text-xs text-ink2 flex items-center gap-1.5">
              Simulation Scenario Context (Optional)
            </label>
            <input
              type="text"
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              placeholder="e.g. AMD announces breakthrough AI chip..."
              className="w-full bg-black border border-hair rounded-sm px-3 py-2 text-sm text-white placeholder-ink3 focus:outline-none focus:border-accent"
              spellCheck={false}
            />
          </div>
          
          {/* Duration */}
          <div className="flex flex-col gap-2">
            <label className="text-xs text-ink2 flex items-center gap-1.5">
              <Calendar size={14} /> Total Simulated Duration (Days)
            </label>
            <div className="flex bg-white/5 rounded-sm border border-hair overflow-hidden">
              {[
                { label: "1 Week", val: 7 },
                { label: "1 Month", val: 30 },
                { label: "3 Months", val: 90 },
                { label: "1 Year", val: 365 },
              ].map((d) => (
                <button
                  key={d.label}
                  onClick={() => setDurationDays(d.val)}
                  className={`flex-1 py-2 text-xs border-r border-hair last:border-0 transition-colors ${
                    durationDays === d.val ? "bg-accent/20 text-accent font-bold" : "text-ink3 hover:bg-white/5 hover:text-ink1"
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          {/* Resolution */}
          <div className="flex flex-col gap-2">
            <label className="text-xs text-ink2 flex items-center gap-1.5">
              <Settings size={14} /> Resolution (Ticks per Day)
            </label>
            <div className="flex bg-white/5 rounded-sm border border-hair overflow-hidden">
              {[
                { label: "1 (Daily)", val: 1 },
                { label: "4 (6-Hour)", val: 4 },
                { label: "24 (Hourly)", val: 24 },
              ].map((r) => (
                <button
                  key={r.label}
                  onClick={() => setTicksPerDay(r.val)}
                  className={`flex-1 py-2 text-xs border-r border-hair last:border-0 transition-colors ${
                    ticksPerDay === r.val ? "bg-accent/20 text-accent font-bold" : "text-ink3 hover:bg-white/5 hover:text-ink1"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-ink3 italic">
              Total execution: {durationDays * ticksPerDay} ticks.
            </p>
          </div>

          {/* Speed */}
          <div className="flex flex-col gap-2">
            <label className="text-xs text-ink2 flex items-center gap-1.5">
              <Zap size={14} /> Execution Speed
            </label>
            <div className="flex bg-white/5 rounded-sm border border-hair overflow-hidden">
              {[
                { label: "1x", val: 3.0 },
                { label: "5x", val: 0.6 },
                { label: "MAX", val: 0.0 },
              ].map((s) => (
                <button
                  key={s.label}
                  onClick={() => setSpeed(s.val)}
                  className={`flex-1 py-2 text-xs border-r border-hair last:border-0 transition-colors ${
                    speed === s.val ? "bg-accent/20 text-accent font-bold" : "text-ink3 hover:bg-white/5 hover:text-ink1"
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

        </div>

        {/* Footer */}
        <div className="p-4 border-t border-hair bg-white/[0.02]">
          <button
            onClick={handleStart}
            disabled={isLoading}
            className="w-full flex items-center justify-center gap-2 bg-accent text-black font-bold py-2.5 rounded-sm hover:bg-white transition-colors disabled:opacity-50"
          >
            {isLoading ? (
              <>
                <Loader2 size={16} className="animate-spin" /> Starting Engine...
              </>
            ) : (
              <>
                <Play size={16} className="fill-current" /> LAUNCH SIMULATION
              </>
            )}
          </button>
        </div>
        
      </div>
    </div>
  );
}
