"use client";

// The simulation backend (FastAPI + TimesFM + WebSocket) is a stateful,
// always-on process — it is not deployed alongside this Vercel frontend.
// On a hosted visit there is therefore nothing at API_BASE to talk to, and
// every panel would sit empty forever.
//
// This gate probes the backend once on load. Reachable -> the app renders as
// normal. Unreachable -> reviewers get the run-it-locally instructions instead
// of a dead dashboard.

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/socket";

const PROBE_TIMEOUT_MS = 6000;
const REPO_URL = "https://github.com/ExceedingExpectations150/black-swan";

type Status = "probing" | "online" | "offline";

export default function BackendGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("probing");

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);

    fetch(`${API_BASE}/api/state`, { signal: controller.signal })
      .then((r) => setStatus(r.ok ? "online" : "offline"))
      .catch(() => setStatus("offline"))
      .finally(() => clearTimeout(timer));

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, []);

  if (status === "online") return <>{children}</>;
  if (status === "probing") return <ProbeScreen />;
  return <OfflineScreen />;
}

function ProbeScreen() {
  return (
    <div className="crt fixed inset-0 z-50 overflow-hidden bg-black">
      <div className="scanlines pointer-events-none absolute inset-0" />
      <div className="flex h-full items-center justify-center">
        <pre className="term-green font-mono text-sm">
          {"> contacting simulation backend "}
          <span className="term-cursor">█</span>
        </pre>
      </div>
    </div>
  );
}

const SETUP = `git clone ${REPO_URL}
cd black-swan

# 1. backend  (FastAPI + TimesFM quant funds)
cd backend
python -m venv .venv && . .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --port 8010

# 2. frontend (new terminal, from the repo root)
cd frontend
npm install
npm run dev

# 3. open http://localhost:5055`;

function OfflineScreen() {
  return (
    <div className="crt fixed inset-0 z-50 overflow-y-auto bg-black">
      <div className="scanlines pointer-events-none absolute inset-0" />
      <div className="mx-auto max-w-3xl px-6 py-12 md:px-10">
        <pre className="term-green whitespace-pre-wrap font-mono text-[13px] leading-relaxed md:text-sm">
          {"BLACK SWAN TERMINAL v1.0\n\n"}
          <span className="term-bright">
            {"> SIMULATION BACKEND NOT REACHABLE FROM THIS DEPLOYMENT\n\n"}
          </span>
          {"Black Swan is a live neuro-symbolic market twin: a continuous tick loop\n" +
            "running 51 seeded companies, LLM behavioral cohorts, and local TimesFM\n" +
            "quant funds over a continuous double auction, streamed to this terminal\n" +
            "over a WebSocket.\n\n" +
            "That backend is a stateful always-on process holding a ~2 GB PyTorch\n" +
            "model in memory. It cannot run on Vercel's serverless platform, which\n" +
            "hosts this frontend, so the hosted page has no engine to talk to.\n\n"}
          <span className="term-bright">{"> RUN THE FULL SYSTEM LOCALLY (~5 min):\n"}</span>
        </pre>

        <pre className="term-green mt-4 overflow-x-auto rounded-sm border border-hair bg-white/[0.03] p-4 font-mono text-[12px] leading-relaxed">
          {SETUP}
        </pre>

        <pre className="term-green mt-6 whitespace-pre-wrap font-mono text-[13px] leading-relaxed md:text-sm">
          {"Add two Google Gemini API keys to backend/.env as GEMINI_API_KEY_PRIMARY\n" +
            "and GEMINI_API_KEY_BACKUP (see .env.example) to enable the LLM cohorts.\n" +
            "Without them the TimesFM quant funds still trade and the market still\n" +
            "moves — nothing is mocked.\n\n"}
          <span className="term-bright">{"> SOURCE: "}</span>
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="term-bright underline"
          >
            {REPO_URL}
          </a>
          {"\n"}
          <span className="term-cursor">█</span>
        </pre>
      </div>
    </div>
  );
}
