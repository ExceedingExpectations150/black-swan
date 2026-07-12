"use client";

import { useStore } from "@/lib/store";
import { useState, useEffect, useRef } from "react";
import { Play, FastForward, Rewind } from "lucide-react";

export default function TimelineSlider() {
  const latestTickId = useStore((s) => s.latestTickId);
  const scrubbedTickId = useStore((s) => s.scrubbedTickId);
  const maxTicks = useStore((s) => s.maxTicks);
  const durationDays = useStore((s) => s.durationDays);
  const ticksPerDay = useStore((s) => s.ticksPerDay);
  const fetchHistory = useStore((s) => s.fetchHistory);
  const clearHistory = useStore((s) => s.clearHistory);
  const connection = useStore((s) => s.connectionStatus);

  const [sliderValue, setSliderValue] = useState<number>(latestTickId);
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (scrubbedTickId === null) {
      setSliderValue(latestTickId);
    }
  }, [latestTickId, scrubbedTickId]);

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    setSliderValue(val);
    
    // Clear any pending debounced fetch
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    
    // Set a new debounced fetch. Releasing at or within one tick of the
    // live edge snaps back to live — dragging "roughly to the end" must
    // never leave the dashboard silently frozen on history.
    debounceRef.current = setTimeout(() => {
      if (val >= latestTickId - 1) {
        clearHistory();
      } else {
        fetchHistory(val);
      }
    }, 150); // 150ms debounce for smooth but not spammy scrubbing
  };

  const returnToLive = () => {
    clearHistory();
  };

  if (latestTickId <= 1 && sliderValue <= 1) return null;

  const sliderMax = maxTicks ? Math.max(maxTicks, latestTickId) : latestTickId;

  let unit = "Tick";
  let current = sliderValue;
  let total = sliderMax;
  if (durationDays && ticksPerDay) {
    unit = "Day";
    current = Math.ceil(sliderValue / ticksPerDay);
    total = Math.max(durationDays, Math.ceil(sliderMax / ticksPerDay));
  } else if (maxTicks === 30 || maxTicks === 7) {
    unit = "Day";
  }

  return (
    <div className="flex flex-col gap-2 p-3 bg-[#0a0a0a] border-t border-hair shrink-0">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {scrubbedTickId !== null ? (
            <button
              onClick={returnToLive}
              className="flex items-center gap-1.5 px-3 py-1 rounded-sm bg-accent/20 text-accent hover:bg-accent/30 transition-colors text-xs font-medium uppercase tracking-wider"
            >
              <Play size={14} />
              Return to Live
            </button>
          ) : (
            <div className="flex items-center gap-1.5 px-3 py-1 text-ink2 text-xs font-medium uppercase tracking-wider">
              <span className={`live-dot inline-block h-2 w-2 rounded-full ${connection === "open" ? "bg-up" : "bg-down"}`} />
              Live Simulation
            </div>
          )}
        </div>
        <div className="tnum text-xs text-ink2 font-mono">
          {unit}: <span className="text-ink font-bold">{current}</span> / {total}
        </div>
      </div>
      
      <div className="flex items-center gap-4 w-full group">
        <Rewind size={16} className="text-ink3 group-hover:text-ink2 transition-colors" />
        <div className="relative w-full h-4 flex items-center">
          <input
            type="range"
            min="1"
            max={sliderMax}
            value={sliderValue}
            onChange={handleSliderChange}
            className="absolute z-10 w-full h-full opacity-0 cursor-pointer"
          />
          {/* Custom track styling — square terminal rail, no rounded pill */}
          <div className="w-full h-[3px] bg-white/10 overflow-hidden">
             <div
               className="h-full bg-accent transition-all duration-75 ease-linear"
               style={{ width: `${(sliderValue / sliderMax) * 100}%` }}
             />
          </div>
          {/* Playhead — a thin vertical marker, not a glowing orb */}
          <div
            className="absolute h-3.5 w-[2px] bg-ink pointer-events-none transition-all duration-75 ease-linear -ml-px"
            style={{ left: `${(sliderValue / sliderMax) * 100}%` }}
          />
        </div>
        <FastForward size={16} className="text-ink3 group-hover:text-ink2 transition-colors" />
      </div>
    </div>
  );
}
