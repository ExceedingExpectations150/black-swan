# Black Swan — Neuro-Symbolic Market Twin

**AMD ACT 2 hackathon project.** Type a Black Swan headline into a boot
terminal — *"Sovereign default triggers global margin calls"* — and watch a
real multi-agent market react: 155 autonomous traders price 51 real-world
companies through a genuine continuous double auction, live on a
Bloomberg-style intelligence terminal (world map, candlestick charts, movers,
economy analytics, timeline scrubbing).

**Nothing is faked.** Every price on every chart is a clearing price from
real limit orders crossing in the matching engine. The Black Swan event
never touches prices directly — it shifts agent *beliefs* (fear, sentiment),
and the crash emerges from panicked order flow.

## What we built

- **A real agent-based market.** 150 retail cohorts, each a persistent
  trader with cash, per-ticker holdings, risk tolerance, and a strategy
  (fundamentalist / momentum chartist / noise trader — classic
  heterogeneous-agent microstructure). 5 institutional quant funds trade
  the forecasts of a locally-run time-series foundation model.
- **Neuro** — two model layers:
  - **TimesFM 2.5 (200M, PyTorch)** runs locally and forecasts every
    ticker's next price from its real price history each tick; the quant
    funds trade against those forecasts.
  - **Gemini/Gemma (Google API)** optionally powers the news desk, corporate
    PR agents, a macro analyst, and LLM decision-making for retail cohorts.
    Quota-resilient: when the API is unavailable, the heuristic strategies
    keep the market fully alive.
- **Symbolic** — a per-ticker continuous double auction (price-time
  priority, midpoint execution), settlement, bankruptcies, and an economy
  layer (system stress index) — deterministic, inspectable mechanics.
- **A simulated clock.** Each tick advances simulated market time
  (`24h / ticks_per_day`); charts show real dates, the terminal clock shows
  sim time, and a run is a configurable number of simulated days.

## Compute & AI resource usage

| Resource | What it does | Where it runs |
|---|---|---|
| **TimesFM 2.5 200M** (`google/timesfm-2.5-200m-pytorch`) | Per-ticker price forecasting for the institutional quant funds, every tick | **Locally via PyTorch** (CPU in the demo; the PyTorch path runs unmodified on AMD hardware/ROCm) |
| **Gemini 2.5 Flash / Gemma** (Google Generative Language API) | News desk, corporate PR posts, macro analyst narrative, LLM cohort decisions | Hosted API (best-effort; keys in `backend/.env`, see `.env.example`) |
| **Yahoo Finance** (`yfinance`) | Real anchor prices for the 51 seeded companies + real index strip (S&P/NASDAQ/DOW/FTSE/Nikkei) | Hosted API, unauthenticated |

The simulation itself (matching engine, behavioral agents, economy) is pure
Python — no external compute.

## Run it

Backend (Python 3.12, FastAPI):

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp ../.env.example .env                          # add GEMINI keys (optional)
python -m uvicorn main:app --port 8000
```

Frontend (Next.js 14):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5055
```

Open http://localhost:5055, wait for the boot sequence, type a Black Swan
event, press Enter. The dashboard reveals; Stock Market view shows daily
candlesticks bucketed from real intraday ticks. Every new simulation starts
from a fresh seeded world at tick 1.

> Backend on a non-default port? Set `NEXT_PUBLIC_API_BASE` /
> `NEXT_PUBLIC_WS_URL` in `frontend/.env.local`.

Tuning (env vars): `BLACKSWAN_RETAIL_COHORTS` (default 150),
`BLACKSWAN_SWARM_BATCH` (cohorts per LLM call, default 50).

## Main code path (start here)

```
backend/
  main.py               FastAPI app + SimulationController (run loop, sim clock,
                        fresh-start reset, /api/start|stop|reset|event, /ws)
  tick_engine.py        The per-tick orchestrator: news -> agents -> orders ->
                        CDA clearing -> settlement -> economy (steps 1-8 in its docstring)
  behavioral_agents.py  The always-on trader strategies (fundamentalist/chartist/noise)
  ai_clients.py         TimesFM forecaster (local) + Gemini router (429-rotating)
  matching_engine.py    Continuous double auction: crossing, midpoint pricing
  database.py           Seeding: real anchor prices, 155 agents, starting holdings
  models.py             SQLAlchemy schema (world/agents/orders/prices/social/economy)
  tests/                pytest suites incl. test_behavioral_agents.py (proves the
                        market clears real volume and that panic moves prices
                        through order flow only)
frontend/
  app/page.tsx          Boot terminal gate -> dashboard shell
  components/           BootTerminal, WorldMap, StockMarketView (candles),
                        TopBar (sim clock), TimelineSlider, panels
  lib/store.ts          zustand store; lib/socket.ts WS envelope dispatch
```

WebSocket contract: every event is `{ type, tick_id, ts, payload }` where
`ts` is **simulated** time.

## External services

- **Google Generative Language API** — optional; without keys the market
  still runs on TimesFM + behavioral agents (LLM features degrade
  gracefully and log warnings).
- **Yahoo Finance** — seed-time anchor prices and the live index strip.
- Nothing else. No other network calls, no telemetry.

## Originality

All simulation code (agents, matching engine, tick orchestration, economy),
the terminal frontend, and the boot experience were written for this
hackathon by the Black Swan team. Third-party components are the published
TimesFM model weights, public APIs listed above, and standard open-source
libraries (FastAPI, SQLAlchemy, Next.js, lightweight-charts, zustand).
