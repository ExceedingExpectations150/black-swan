"use client";

import { useEffect, useRef, useState } from "react";
import { Play, Pause, Square, Gauge, Clock, Loader2, RotateCcw } from "lucide-react";
import { useStore } from "@/lib/store";
import { API_BASE } from "@/lib/socket";

export default function SimulationControls() {
  const isPaused = useStore((s) => s.isPaused);
  const isPausing = useStore((s) => s.isPausing);
  const tickInterval = useStore((s) => s.tickInterval);
  const connection = useStore((s) => s.connectionStatus);

  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingResume, setIsLoadingResume] = useState(false);
  // Two-step destructive action: first click arms, second click within 3s fires.
  const [resetArmed, setResetArmed] = useState(false);
  const armTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (armTimer.current) clearTimeout(armTimer.current);
  }, []);

  const handlePause = async () => {
    setIsLoading(true);
    try {
      await fetch(`${API_BASE}/api/pause`, { method: "POST" });
    } finally {
      setIsLoading(false);
    }
  };

  const handleResume = async () => {
    setIsLoadingResume(true);
    try {
      await fetch(`${API_BASE}/api/resume`, { method: "POST" });
    } finally {
      setIsLoadingResume(false);
    }
  };

  const handleStop = async () => {
    setIsLoading(true);
    try {
      await fetch(`${API_BASE}/api/stop`, { method: "POST" });
    } finally {
      setIsLoading(false);
    }
  };

  const handleReset = async () => {
    if (!resetArmed) {
      setResetArmed(true);
      if (armTimer.current) clearTimeout(armTimer.current);
      armTimer.current = setTimeout(() => setResetArmed(false), 3000);
      return;
    }
    if (armTimer.current) clearTimeout(armTimer.current);
    setResetArmed(false);
    setIsLoading(true);
    try {
      await fetch(`${API_BASE}/api/reset`, { method: "POST" });
    } finally {
      setIsLoading(false);
    }
  };

  const setSpeed = async (interval: number) => {
    setIsLoading(true);
    try {
      await fetch(`${API_BASE}/api/speed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interval }),
      });
    } finally {
      setIsLoading(false);
    }
  };

  const setDuration = async (days: number) => {
    setIsLoading(true);
    try {
      // Backend converts days -> ticks via the run's ticks_per_day, so the
      // button labels finally mean what they say.
      await fetch(`${API_BASE}/api/duration`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days }),
      });
      if (isPaused) {
        await fetch(`${API_BASE}/api/resume`, { method: "POST" });
      }
    } finally {
      setIsLoading(false);
    }
  };

  if (connection !== "open") {
    return null;
  }

  return (
    <div className="flex items-center gap-4 bg-white/[0.02] border border-hair rounded-sm px-3 py-1.5 ml-4">
      {/* Play/Pause/Stop */}
      <div className="flex bg-white/5 rounded-sm overflow-hidden">
        {isPaused ? (
          <button
            onClick={handleResume}
            disabled={isLoading || isLoadingResume}
            className="w-10 h-7 flex items-center justify-center bg-accent text-black hover:bg-white transition-colors disabled:opacity-50"
            title="Play"
          >
            {isLoadingResume ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Play size={14} className="fill-current" />
            )}
          </button>
        ) : (
          <button
            onClick={handlePause}
            disabled={isLoading || isPausing}
            className={`w-10 h-7 flex items-center justify-center transition-colors disabled:opacity-50 ${isPausing ? 'bg-warn/20 text-warn' : 'bg-white/10 hover:bg-white/20 text-white'}`}
            title={isPausing ? "Pausing..." : "Pause"}
          >
            {isPausing ? (
              <Loader2 size={14} className="animate-spin" />
            ) : isLoading ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Pause size={14} className="fill-current" />
            )}
          </button>
        )}
        <button 
          onClick={handleStop} 
          disabled={isLoading}
          className="w-10 h-7 flex items-center justify-center hover:bg-white/10 text-ink3 hover:text-down transition-colors disabled:opacity-50"
          title="Stop Simulation"
        >
          <Square size={14} fill="currentColor" />
        </button>
        <button
          onClick={handleReset}
          disabled={isLoading}
          className={`h-7 flex items-center justify-center gap-1 transition-colors disabled:opacity-50 border-l border-white/10 ${
            resetArmed
              ? "w-auto px-2 bg-down/20 text-down"
              : "w-10 hover:bg-white/10 text-ink3 hover:text-down"
          }`}
          title={resetArmed ? "Click again to wipe & reset" : "Wipe & Reset Simulation"}
        >
          <RotateCcw size={14} />
          {resetArmed && (
            <span className="text-[9px] font-semibold uppercase tracking-[0.1em]">Confirm</span>
          )}
        </button>
      </div>

      <div className="w-px h-4 bg-hair" />

      {/* Speed Controls */}
      <div className="flex items-center gap-2 text-xs">
        <Gauge size={14} className="text-ink3" />
        <div className="flex items-center bg-black rounded-sm border border-hair overflow-hidden">
          {[
            { label: "0.5x", val: 6.0 },
            { label: "1x", val: 3.0 },
            { label: "2x", val: 1.5 },
            { label: "5x", val: 0.6 },
            { label: "MAX", val: 0.0 }
          ].map((s) => (
            <button
              key={s.label}
              onClick={() => setSpeed(s.val)}
              disabled={isLoading}
              className={`px-2 py-0.5 border-r border-hair last:border-0 hover:bg-white/5 transition-colors ${
                tickInterval === s.val ? "bg-white/10 text-ink1 font-bold" : "text-ink3"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="w-px h-4 bg-hair" />

      {/* Duration Controls */}
      <div className="flex items-center gap-2 text-xs">
        <Clock size={14} className="text-ink3" />
        <div className="flex items-center gap-1">
          <button
            onClick={() => setDuration(7)}
            disabled={isLoading}
            className="px-2 py-0.5 rounded-sm border border-hair text-ink2 hover:text-ink1 hover:border-ink3 transition-colors"
          >
            1 Wk
          </button>
          <button
            onClick={() => setDuration(30)}
            disabled={isLoading}
            className="px-2 py-0.5 rounded-sm border border-hair text-ink2 hover:text-ink1 hover:border-ink3 transition-colors"
          >
            1 Mo
          </button>
        </div>
      </div>
    </div>
  );
}
