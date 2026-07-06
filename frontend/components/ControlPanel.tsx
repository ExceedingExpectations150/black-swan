"use client";

import { useState } from "react";
import { Play, Square, Zap, Loader2 } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export default function ControlPanel() {
  const [eventText, setEventText] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [pending, setPending] = useState<"start" | "stop" | "event" | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const post = async (path: string, body?: object) => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = `${path} -> HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body.detail) detail = `${detail}: ${body.detail}`;
      } catch {
        // non-JSON error body; keep the status-only message
      }
      throw new Error(detail);
    }
    return res.json();
  };

  const handleStart = async () => {
    setPending("start");
    setFeedback(null);
    try {
      const data = await post("/api/start");
      setIsRunning(true);
      setFeedback(
        data.status === "already_running"
          ? "Simulation already running."
          : `Simulation started — tick ${data.next_tick}.`
      );
    } catch (err) {
      setFeedback(`Start failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setPending(null);
    }
  };

  const handleStop = async () => {
    setPending("stop");
    setFeedback(null);
    try {
      const data = await post("/api/stop");
      setIsRunning(false);
      setFeedback(
        data.status === "not_running"
          ? "Simulation was not running."
          : `Simulation halted at tick ${data.last_tick}.`
      );
    } catch (err) {
      setFeedback(`Stop failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setPending(null);
    }
  };

  const handleInjectEvent = async () => {
    const headline = eventText.trim();
    if (!headline) return;
    setPending("event");
    setFeedback(null);
    try {
      await post("/api/event", { headline });
      setFeedback(`Black Swan injected: "${headline}"`);
      setEventText("");
    } catch (err) {
      setFeedback(`Event failed: ${err instanceof Error ? err.message : err}`);
    } finally {
      setPending(null);
    }
  };

  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-5 backdrop-blur-md">
      <h2 className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
        Simulation Control
      </h2>

      <div className="mt-4 flex gap-3">
        <button
          onClick={handleStart}
          disabled={pending !== null || isRunning}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-emerald-500/15 px-4 py-2.5 text-sm font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-400/30 transition hover:bg-emerald-500/25 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {pending === "start" ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <Play size={15} />
          )}
          Start
        </button>
        <button
          onClick={handleStop}
          disabled={pending !== null || !isRunning}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-rose-500/15 px-4 py-2.5 text-sm font-semibold text-rose-300 ring-1 ring-inset ring-rose-400/30 transition hover:bg-rose-500/25 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {pending === "stop" ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <Square size={15} />
          )}
          Stop
        </button>
      </div>

      <div className="mt-4">
        <label
          htmlFor="black-swan-input"
          className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-slate-400"
        >
          Black Swan Event
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="black-swan-input"
            type="text"
            value={eventText}
            onChange={(e) => setEventText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleInjectEvent()}
            placeholder='e.g. "Sovereign default triggers global margin calls"'
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-black/40 px-3 py-2.5 font-mono text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none focus:ring-1 focus:ring-amber-400/40"
          />
          <button
            onClick={handleInjectEvent}
            disabled={pending !== null || eventText.trim().length === 0}
            className="inline-flex items-center gap-2 rounded-lg bg-amber-500/15 px-4 py-2.5 text-sm font-semibold text-amber-300 ring-1 ring-inset ring-amber-400/30 transition hover:bg-amber-500/25 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {pending === "event" ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Zap size={15} />
            )}
            Inject
          </button>
        </div>
      </div>

      {feedback && (
        <p className="mt-3 font-mono text-xs text-slate-400" role="status">
          {feedback}
        </p>
      )}
    </section>
  );
}
