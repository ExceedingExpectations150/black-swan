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

// --- Dithered Bitcoin coin flip -------------------------------------------
// Ordered-dither (Bayer 4x4) render of a coin spinning on its vertical axis,
// drawn in the terminal's phosphor green. Pure canvas: no assets, loops
// seamlessly, unmounts with the terminal.

const COIN_GRID = 56; // logical pixel grid (dither resolution)
const COIN_SCALE = 3; // chunky on-screen pixels
const BAYER4 = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
];

function DitheredCoin() {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const size = COIN_GRID * COIN_SCALE;
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext("2d");
    const off = document.createElement("canvas");
    off.width = COIN_GRID;
    off.height = COIN_GRID;
    const octx = off.getContext("2d", { willReadFrequently: true });
    if (!ctx || !octx) return;
    ctx.imageSmoothingEnabled = false;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const C = COIN_GRID / 2;
    const R = COIN_GRID * 0.42;
    let raf = 0;
    const t0 = performance.now();

    const draw = (now: number) => {
      // cos(angle) = apparent coin width; the flip.
      const angle = reduced ? 0.55 : ((now - t0) / 1000) * 2.6;
      const w = Math.cos(angle);
      const aw = Math.max(Math.abs(w), 0.045); // never fully vanish edge-on

      // 1) Grayscale coin into the small offscreen buffer.
      octx.fillStyle = "#000";
      octx.fillRect(0, 0, COIN_GRID, COIN_GRID);
      octx.save();
      octx.translate(C, C);
      octx.scale(aw, 1);
      // face: black inside — only the outlines carry green. The soft radial
      // wash stays far below every Bayer threshold except near the rim, where
      // its antialiased falloff dithers into a stippled edge.
      const g = octx.createRadialGradient(0, 0, R * 0.55, 0, 0, R);
      g.addColorStop(0, "#000000");
      g.addColorStop(0.9, "#1a1a1a");
      g.addColorStop(1, "#3a3a3a");
      octx.fillStyle = g;
      octx.beginPath();
      octx.arc(0, 0, R, 0, Math.PI * 2);
      octx.fill();
      // rim: double green outline like a struck coin
      octx.strokeStyle = "#ffffff";
      octx.lineWidth = 2.5;
      octx.stroke();
      octx.beginPath();
      octx.arc(0, 0, R * 0.82, 0, Math.PI * 2);
      octx.lineWidth = 1;
      octx.strokeStyle = "#b0b0b0";
      octx.stroke();
      // ₿ on the front face only; the back is a plain shaded disc
      if (w > 0.12) {
        octx.fillStyle = "#ffffff";
        octx.font = `700 ${Math.round(R * 1.15)}px "Segoe UI Symbol", monospace`;
        octx.textAlign = "center";
        octx.textBaseline = "middle";
        octx.fillText("₿", 0, 1);
      }
      octx.restore();

      // 2) Ordered dither: luminance vs Bayer threshold -> green or black.
      const img = octx.getImageData(0, 0, COIN_GRID, COIN_GRID);
      const d = img.data;
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = "#16c60c";
      for (let y = 0; y < COIN_GRID; y++) {
        for (let x = 0; x < COIN_GRID; x++) {
          const lum = d[(y * COIN_GRID + x) * 4] / 255; // gray, so R channel is enough
          const threshold = (BAYER4[y % 4][x % 4] + 0.5) / 16;
          if (lum > threshold) {
            ctx.fillRect(x * COIN_SCALE, y * COIN_SCALE, COIN_SCALE, COIN_SCALE);
          }
        }
      }

      if (!reduced) raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <canvas
      ref={ref}
      aria-hidden
      className="mb-7"
      style={{ width: COIN_GRID * COIN_SCALE, height: COIN_GRID * COIN_SCALE }}
    />
  );
}

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
        <DitheredCoin />
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
