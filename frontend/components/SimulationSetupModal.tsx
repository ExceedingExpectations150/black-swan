"use client";

import { useEffect, useState } from "react";
import { Play, Loader2, Calendar, Settings, Zap, Cpu, KeyRound } from "lucide-react";
import { useStore } from "@/lib/store";
import { API_BASE } from "@/lib/socket";

interface ComputeConfig {
  llm_active: boolean;
  device: string;
  compute_backend: string;
}

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

  // Compute device + LLM status (drives the AI section below).
  const [config, setConfig] = useState<ComputeConfig | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [keyStatus, setKeyStatus] = useState<"idle" | "saving" | "ok" | "err">("idle");
  useEffect(() => {
    if (!shouldShow) return;
    fetch(`${API_BASE}/api/config`)
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => c && setConfig(c))
      .catch(() => {});
  }, [shouldShow]);

  if (!shouldShow) return null;

  const onGpu = config?.device === "cuda";
  const llmOn = config?.llm_active || keyStatus === "ok";

  const applyKey = async (): Promise<boolean> => {
    const key = apiKey.trim();
    if (!key) return true; // nothing to apply — keyless is fine
    setKeyStatus("saving");
    try {
      const res = await fetch(`${API_BASE}/api/config/gemini`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ primary: key }),
      });
      setKeyStatus(res.ok ? "ok" : "err");
      return res.ok;
    } catch {
      setKeyStatus("err");
      return false;
    }
  };

  const handleStart = async () => {
    setIsLoading(true);
    try {
      // Apply a runtime key first (if provided) so LLM prose is live for this run.
      await applyKey();
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

          {/* Compute & AI: device readout + optional runtime API key */}
          <div className="flex flex-col gap-2 border-t border-hair pt-4">
            <label className="text-xs text-ink2 flex items-center gap-1.5">
              <Cpu size={14} /> Compute &amp; AI
            </label>
            <div className="flex items-center justify-between rounded-sm border border-hair bg-black px-3 py-2 text-[11px]">
              <span className="text-ink3">
                Inference:{" "}
                <span className={onGpu ? "text-accent" : "text-ink2"}>
                  {config ? (onGpu ? `AMD GPU · ${config.compute_backend}` : "CPU") : "…"}
                </span>
              </span>
              <span className="text-ink3">
                LLM:{" "}
                <span className={llmOn ? "text-up" : "text-warn"}>
                  {llmOn ? "active" : "keyless (factual)"}
                </span>
              </span>
            </div>
            {!onGpu && (
              <p className="text-[10px] text-ink3 leading-relaxed">
                Running on CPU. For GPU-accelerated TimesFM, launch the AMD ROCm
                container (<span className="font-mono">Dockerfile.rocm</span>) — see README.
              </p>
            )}
            {!llmOn && (
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <KeyRound
                    size={13}
                    className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink3"
                  />
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => {
                      setApiKey(e.target.value);
                      if (keyStatus !== "idle") setKeyStatus("idle");
                    }}
                    placeholder="Optional: paste a Gemini API key for LLM news & chat"
                    className="w-full bg-black border border-hair rounded-sm pl-8 pr-3 py-2 text-xs text-white placeholder-ink3 focus:outline-none focus:border-accent"
                    spellCheck={false}
                    autoComplete="off"
                  />
                </div>
                {apiKey.trim() && (
                  <button
                    onClick={applyKey}
                    disabled={keyStatus === "saving"}
                    className="shrink-0 rounded-sm border border-hair px-2.5 py-2 text-[11px] text-ink2 hover:text-ink hover:border-ink3 transition-colors disabled:opacity-50"
                  >
                    {keyStatus === "saving" ? "…" : keyStatus === "err" ? "Retry" : "Apply"}
                  </button>
                )}
              </div>
            )}
            {keyStatus === "err" && (
              <p className="text-[10px] text-down">Couldn&apos;t reach the backend to set the key.</p>
            )}
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
