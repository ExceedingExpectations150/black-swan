"use client";

// Startup terminal: full-black screen, green monospace boot sequence typed out
// character by character, ending in a prompt for the Black Swan event. On
// submit it arms the event (/api/event) and starts the simulation (/api/start),
// then reveals the dashboard. If the backend is unreachable the dashboard is
// revealed anyway and the SimulationSetupModal takes over as the fallback.

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/socket";
import { useStore } from "@/lib/store";

const BOOT_LINES: string[] = [
  "BLACK SWAN TERMINAL v1.0",
  "",
  "> booting neuro-symbolic market twin .......... OK",
  "> loading global company registry ............ 51 firms",
  "> TimesFM quant funds ........................ ONLINE",
  "> Gemma behavioral cohorts ................... ONLINE",
  "> continuous double auction + anchor feed .... LIVE",
  "",
  "> system ready. awaiting directive.",
  "",
];

const CHARS_PER_TICK = 3;
const CHAR_MS = 16;
const LINE_PAUSE_MS = 70;

// Same defaults the SimulationSetupModal uses: MAX speed, 30 days, daily ticks.
const DEFAULT_START = { speed: 0.0, duration_days: 30, ticks_per_day: 1 };

type Phase = "boot" | "prompt" | "launching";

export default function BootTerminal({ onLaunch }: { onLaunch: () => void }) {
  const [rendered, setRendered] = useState<string[]>([]);
  const [partial, setPartial] = useState("");
  const [phase, setPhase] = useState<Phase>("boot");
  const [value, setValue] = useState("");
  const [launchLines, setLaunchLines] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Type the boot sequence out, one character at a time.
  useEffect(() => {
    let line = 0;
    let char = 0;
    let timer: ReturnType<typeof setTimeout>;

    const step = () => {
      if (line >= BOOT_LINES.length) {
        setPhase("prompt");
        return;
      }
      const text = BOOT_LINES[line];
      if (char < text.length) {
        char = Math.min(text.length, char + CHARS_PER_TICK);
        setPartial(text.slice(0, char));
        timer = setTimeout(step, CHAR_MS);
      } else {
        setRendered((r) => [...r, text]);
        setPartial("");
        line += 1;
        char = 0;
        timer = setTimeout(step, LINE_PAUSE_MS);
      }
    };
    timer = setTimeout(step, 300);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (phase === "prompt") inputRef.current?.focus();
  }, [phase]);

  const handleSubmit = async () => {
    const headline = value.trim();
    if (!headline || phase !== "prompt") return;
    setPhase("launching");
    setLaunchLines([
      `> BLACK SWAN ARMED: "${headline}"`,
      "> releasing agents into the market ...",
      "> ENGAGING.",
    ]);
    try {
      await fetch(`${API_BASE}/api/event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ headline }),
      });
      await fetch(`${API_BASE}/api/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(DEFAULT_START),
      });
      // The terminal armed and started the run itself, so the setup modal
      // must stay hidden while the first tick is still being computed.
      useStore.getState().setBootLaunched();
    } catch {
      // If the backend is unreachable we still reveal the dashboard; the
      // SimulationSetupModal will show (tick 0) and can start the run instead.
    }
    setTimeout(onLaunch, 1400);
  };

  return (
    <div className="crt fixed inset-0 z-50 overflow-hidden bg-black">
      <div className="scanlines pointer-events-none absolute inset-0" />
      <div className="flex h-full flex-col justify-center px-6 py-8 md:px-16">
        <pre className="term-green whitespace-pre-wrap font-mono text-[13px] leading-relaxed md:text-sm">
          {rendered.join("\n")}
          {partial && `\n${partial}`}
          {phase === "prompt" && (
            <>
              {"\n"}
              <span className="term-bright">DEFINE BLACK SWAN EVENT:</span>
            </>
          )}
        </pre>

        {phase === "prompt" && (
          <div className="mt-4 flex items-center gap-2 font-mono text-sm">
            <span className="term-bright">❯</span>
            <div className="relative flex-1 max-w-3xl">
              <input
                ref={inputRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
                spellCheck={false}
                autoComplete="off"
                placeholder="e.g. sovereign default triggers global margin calls"
                className="term-input w-full bg-transparent outline-none"
              />
            </div>
          </div>
        )}

        {phase === "launching" && (
          <pre className="term-green mt-2 whitespace-pre-wrap font-mono text-sm">
            {launchLines.join("\n")}
            <span className="term-cursor">█</span>
          </pre>
        )}
      </div>
    </div>
  );
}
