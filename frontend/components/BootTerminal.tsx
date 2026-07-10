"use client";

import { useEffect, useState } from "react";

const BOOT_LINES: string[] = [
  "NEWS & MARKET SIMULATOR v1.0",
  "",
  "> initializing simulation engine ............. OK",
  "> loading global company registry ............ 51 firms",
  "> booting generative news desk ............... ONLINE",
  "> connecting timeseries forecasting .......... ONLINE",
  "> continuous double auction feed ............. LIVE",
  "",
  "> system ready.",
  "> ENGAGING...",
];

const CHARS_PER_TICK = 3;
const CHAR_MS = 16;
const LINE_PAUSE_MS = 100;

export default function BootTerminal({ onLaunch }: { onLaunch: () => void }) {
  const [rendered, setRendered] = useState<string[]>([]);
  const [partial, setPartial] = useState("");

  useEffect(() => {
    let line = 0;
    let char = 0;
    let timer: ReturnType<typeof setTimeout>;

    const step = () => {
      if (line >= BOOT_LINES.length) {
        // Animation finished, wait a moment then launch
        timer = setTimeout(onLaunch, 500);
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
  }, [onLaunch]);

  return (
    <div className="crt fixed inset-0 z-50 overflow-hidden bg-black">
      <div className="scanlines pointer-events-none absolute inset-0" />
      <div className="flex h-full flex-col justify-center px-6 py-8 md:px-16">
        <pre className="term-green whitespace-pre-wrap font-mono text-[13px] leading-relaxed md:text-sm">
          {rendered.join("\n")}
          {partial && `\n${partial}`}
          <span className="term-cursor">█</span>
        </pre>
      </div>
    </div>
  );
}
