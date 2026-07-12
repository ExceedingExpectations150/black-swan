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
- **A professional trading workstation.** Boot terminal → simulation setup
  console (duration / ticks-per-day / speed per run) → dashboard with world
  map, candlestick charts, movers, news wire, a daily market reporter
  (@DailyBrief), a senior-trader chat desk grounded in live statistics,
  timeline rewind, keyboard view switching (1–7), and a live status bar
  (link / mode / tick / day / sim clock).

## Compute & AI resource usage

| Resource | What it does | Where it runs |
|---|---|---|
| **TimesFM 2.5 200M** (`google/timesfm-2.5-200m-pytorch`) | Per-ticker price forecasting for the institutional quant funds, every tick | **Locally via PyTorch** — CPU on this demo box (`torch+cpu`); runs on AMD Instinct/Radeon GPUs unmodified with the ROCm wheel (see AMD/ROCm below) |
| **Gemini 2.5 Flash / Gemma** (Google Generative Language API) | News desk, corporate PR posts, macro analyst narrative, LLM cohort decisions | Hosted API (best-effort; keys in `backend/.env`, see `.env.example`) |
| **Yahoo Finance** (`yfinance`) | Real anchor prices for the 51 seeded companies + real index strip (S&P/NASDAQ/DOW/FTSE/Nikkei) | Hosted API, unauthenticated |

The simulation itself (matching engine, behavioral agents, economy) is pure
Python — no external compute. Fireworks is not used; the only hosted AI
dependency is the optional Google Generative Language API.

### AMD / ROCm

The compute-heavy AI component — TimesFM inference, batched across all 51
tickers in one forward pass per refresh — is **standard PyTorch with no
CUDA-only code** (no custom kernels, no triton/inductor). Device selection is
device-agnostic: `backend/ai_clients.py` picks the accelerator via
`torch.cuda.is_available()`, and **PyTorch's ROCm build surfaces AMD
Instinct / Radeon GPUs through that same `torch.cuda` API** — so on an AMD box
with the ROCm wheel, TimesFM runs on the AMD GPU with **zero code changes**:

```bash
# Swap the CPU wheel for the ROCm wheel; the code is unchanged.
pip install torch --index-url https://download.pytorch.org/whl/rocm6.2
```

The resolved device is logged at startup (`TimesFM device=… backend=ROCm/HIP`)
so you can confirm it landed on the GPU. On the demo machine here the
installed wheel is `torch+cpu`, so inference runs on CPU (the portable
fallback) — verified: `device=cpu, backend=CPU`. Nothing about the code is
CPU- or NVIDIA-specific; the AMD path is the wheel, not a rewrite.

**Zero-credential run:** the project is fully runnable with no API keys at
all — TimesFM + the behavioral swarm drive the market, the newsroom files
factual wire reports computed from real simulation data, and the trading
desk answers from live statistics (verified end-to-end in keyless mode).

## Run it

### Docker (one command)

```bash
docker compose up --build
# frontend -> http://localhost:5055   backend -> http://localhost:8010
```

Brings up both containers (FastAPI + TimesFM backend, Next.js terminal
frontend), fully **keyless**. To enable LLM prose, export `GEMINI_API_KEY_PRIMARY`
/ `GEMINI_API_KEY_BACKUP` before `up` (or uncomment the `env_file` line in
`docker-compose.yml`). The first `/api/start` downloads the TimesFM checkpoint
(~1 GB) into the persisted `hf-cache` volume, so it's slow once and fast after.

**AMD GPU (ROCm):** the backend ships a second Dockerfile — `backend/Dockerfile.rocm`
— identical except it installs the ROCm PyTorch wheel, so TimesFM runs on an
AMD Instinct/Radeon GPU with no code change. Set `dockerfile: Dockerfile.rocm`
and uncomment the `devices:` / `group_add:` block in `docker-compose.yml` on a
ROCm host; the startup log prints `TimesFM device=cuda backend=ROCm/HIP` when it
lands on the GPU.

### Local (no Docker)

Backend (Python 3.12, FastAPI):

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows (source .venv/bin/activate on Linux)
pip install -r requirements.txt                  # first run downloads the TimesFM checkpoint (~800MB)
cp ../.env.example .env                          # Gemini keys OPTIONAL — runs without any
python -m uvicorn main:app --port 8000
```

Frontend (Next.js 14):

```bash
cd frontend
npm install
npm run dev        # http://localhost:5055
```

Open http://localhost:5055, wait for the boot sequence, type a Black Swan
event, press Enter, configure the run in the setup console (duration,
ticks per day, speed), and launch. Keys 1–7 switch views; the Stock Market
view shows daily candlesticks bucketed from real intraday ticks; the
status bar tracks tick/day/sim-time live. Every refresh is a clean start:
a new simulation always begins from a fresh seeded world at tick 1.

> Backend on a non-default port? Set `NEXT_PUBLIC_API_BASE` /
> `NEXT_PUBLIC_WS_URL` in `frontend/.env.local`.

Tuning (env vars): `BLACKSWAN_RETAIL_COHORTS` (default 150),
`BLACKSWAN_SWARM_BATCH` (cohorts per LLM call, default 50),
`BLACKSWAN_LLM_PRIMARY` / `BLACKSWAN_LLM_FALLBACK` (default
`gemini-2.5-flash` / `gemini-2.0-flash`), `BLACKSWAN_NEWS_EVERY_N` /
`BLACKSWAN_SWARM_EVERY_N` (LLM call pacing — the newsroom publishes every
N ticks and ONE rotating cohort batch gets an LLM voice every M ticks, so
free-tier quota is spent deliberately instead of 429-storming; heuristic
traders and factual wire reports carry every other tick with real data).

The agent pipeline per tick: **your headline → event analyst** (LLM, with
a transparent keyword fallback) derives per-sector impacts → **company
agents** absorb them (sentiment + repriced fundamentals) → **TimesFM
forecasts conditioned** toward those fundamentals → **quant funds ladder
orders** toward the conditioned forecast while the **behavioral swarm**
trades its beliefs → the **matching engine** clears — prices only ever
come from crossing orders. A daily reporter (@DailyBrief) files an
end-of-day wire report at each simulated-day boundary, and the Trading
Desk chat (`POST /api/chat`) answers with insight grounded in live
momentum/volatility/forecast statistics.

## Main code path (start here)

```
backend/
  main.py               FastAPI app + SimulationController (run loop, sim clock,
                        fresh-start reset, /api/start|stop|reset|event|chat, /ws)
  tick_engine.py        The per-tick orchestrator: news -> agents -> orders ->
                        CDA clearing -> settlement -> economy (steps 1-8 in its docstring)
  behavioral_agents.py  The always-on trader strategies (fundamentalist/chartist/noise)
  event_analyst.py      Your headline -> per-sector impact JSON (LLM, keyword fallback)
  ai_clients.py         TimesFM forecaster (local, batched) + Gemini router (429-rotating)
  matching_engine.py    Continuous double auction: crossing, midpoint pricing
  economy.py            Sector rollups, movers, system stress index
  social_agents.py      News desk, PR agents, DailyReporter (@DailyBrief wire reports)
  trader_agent.py       Senior-trader chat: live momentum/volatility/forecast briefs
  database.py           Seeding: real anchor prices, 155 agents, starting holdings
  models.py             SQLAlchemy schema (world/agents/orders/prices/social/economy)
  tests/                suites incl. test_behavioral_agents.py (proves the market
                        clears real volume and that panic moves prices through
                        order flow only) — run each directly with python
frontend/
  app/page.tsx          Boot terminal gate -> dashboard shell + keyboard nav
  components/           BootTerminal, SimulationSetupModal, WorldMap,
                        StockMarketView (candles), ChatPanel (Trading Desk),
                        TopBar (sim clock), TimelineSlider, StatusBar, panels
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
