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
    
    // Set a new debounced fetch
    debounceRef.current = setTimeout(() => {
      if (val >= latestTickId) {
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
  
  let formattedLabel = `Tick: ${sliderValue} / ${sliderMax}`;
  if (durationDays && ticksPerDay) {
    const currentDay = Math.ceil(sliderValue / ticksPerDay);
    const maxDay = Math.max(durationDays, Math.ceil(sliderMax / ticksPerDay));
    formattedLabel = `Day: ${currentDay} / ${maxDay}`;
  } else if (maxTicks === 30 || maxTicks === 7) {
    formattedLabel = `Day: ${sliderValue} / ${sliderMax}`;
  }

  return (
    <div className="flex flex-col gap-2 p-3 bg-[#0a0a0a] border-t border-hair shrink-0">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {scrubbedTickId !== null ? (
            <button 
              onClick={returnToLive}
              className="flex items-center gap-1.5 px-3 py-1 rounded bg-accent/20 text-accent hover:bg-accent/30 transition-colors text-xs font-medium uppercase tracking-wider"
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
        <div className="text-xs text-ink2 font-mono">
          {formattedLabel.split(':')[0]}: <span className="text-ink1 font-bold">{formattedLabel.split(':')[1].split('/')[0].trim()}</span> / {formattedLabel.split('/')[1].trim()}
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
          {/* Custom track styling */}
          <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
             <div 
               className="h-full bg-accent transition-all duration-75 ease-linear"
               style={{ width: `${(sliderValue / sliderMax) * 100}%` }}
             />
          </div>
          {/* Custom thumb styling */}
          <div 
            className="absolute w-3 h-3 bg-white rounded-full shadow-[0_0_10px_rgba(255,255,255,0.5)] pointer-events-none transition-all duration-75 ease-linear -ml-1.5"
            style={{ left: `${(sliderValue / sliderMax) * 100}%` }}
          />
        </div>
        <FastForward size={16} className="text-ink3 group-hover:text-ink2 transition-colors" />
      </div>
    </div>
  );
}
