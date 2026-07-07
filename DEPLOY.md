# Deploying Black Swan as a hosted web app

Two pieces, deployed separately:

- **Frontend** (Next.js) → **Vercel** — static/edge, trivial.
- **Backend** (FastAPI + local TimesFM/torch + WebSocket) → an **always-on
  container host** (Render used below; Fly.io / Railway work too). This is
  NOT serverless: it runs a continuous tick loop and a WebSocket, and holds a
  ~2 GB PyTorch model in memory.

Order matters: deploy the backend first (you need its URL for the frontend),
then the frontend, then point the backend's CORS at the frontend URL.

---

## 0. Prerequisites (one-time, needs your accounts)

1. **GitHub repo.** This repo has no remote yet. Create one and push:
   ```bash
   gh repo create black-swan --private --source . --push
   # or: create an empty repo on github.com, then
   # git remote add origin https://github.com/<you>/black-swan.git && git push -u origin HEAD
   ```
2. **Two Gemini API keys** (already in your local `backend/.env`) — you'll
   paste these into the host as secrets, never commit them.
3. Accounts on **Vercel** and **Render** (both have GitHub sign-in).

---

## 1. Backend → Render (Docker)

1. Render → **New → Web Service** → connect the GitHub repo.
2. Settings:
   - **Root Directory:** `backend`
   - **Runtime:** Docker (Render auto-detects `backend/Dockerfile`)
   - **Instance type:** **at least 2 GB RAM** (Standard). The free/512 MB tier
     OOMs on torch + TimesFM.
   - **Instances:** **1** (do NOT autoscale — the tick loop and WebSocket
     broadcast are in-process; multiple instances would each run their own
     simulation and split clients).
3. **Environment variables:**
   | Key | Value |
   |---|---|
   | `GEMINI_API_KEY_PRIMARY` | your key |
   | `GEMINI_API_KEY_BACKUP` | your second key |
   | `ALLOWED_ORIGINS` | `https://<your-vercel-domain>` (fill in after step 2) |
4. Deploy. Note the URL, e.g. `https://black-swan-api.onrender.com`.

**First `/api/start` is slow:** the TimesFM checkpoint (~1 GB) downloads from
Hugging Face on the first simulation start and is cached on the instance.

**Persistence:** the SQLite `blackswan.db` lives on the container's ephemeral
disk and resets on every redeploy (fine for a demo — it reseeds). To persist,
attach a Render **Disk** mounted at `/app` and set
`DATABASE_URL=sqlite:////app/data/blackswan.db`.

---

## 2. Frontend → Vercel

1. Vercel → **Add New → Project** → import the repo.
2. Settings:
   - **Root Directory:** `frontend`
   - Framework preset: **Next.js** (auto)
3. **Environment variables** (Production):
   | Key | Value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE` | `https://black-swan-api.onrender.com` |
   | `NEXT_PUBLIC_WS_URL` | `wss://black-swan-api.onrender.com/ws` |

   Note `wss://` (TLS WebSocket) and the `/ws` path.
4. Deploy. Note the URL, e.g. `https://black-swan.vercel.app`.

---

## 3. Close the loop

1. Back in Render, set `ALLOWED_ORIGINS=https://black-swan.vercel.app` and
   redeploy the backend (CORS must allow the exact frontend origin, no trailing
   slash).
2. Open the Vercel URL → the boot terminal → type a Black Swan event →
   the dashboard should stream live over `wss://`.

---

## Gotchas

- **Single backend instance only** (see step 1.3).
- **Gemini free-tier quota** still applies — the cohort/PR LLM calls will 429
  under load. The event macro-shock (`tick_engine.EVENT_SHOCK_*`) keeps the
  market moving regardless; enable paid billing on the Gemini key for real
  cohort-driven behavior.
- **yfinance** occasionally rate-limits cloud IPs; the index strip falls back
  to "—" and anchors use the last good values (never fabricated).
- **Cold starts:** Render free/hobby services sleep; the first request after
  idle is slow, and the WebSocket only carries live ticks once `/api/start`
  runs (the boot terminal does this on submit).
- **Docker image size** is large (torch CPU + timesfm). First build is slow.

## Local full-stack (no hosting)

```bash
# backend
cd backend && python -m venv .venv && . .venv/Scripts/activate  # (bash: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn main:app --port 8010

# frontend (new shell)
cd frontend && npm install
NEXT_PUBLIC_API_BASE=http://localhost:8010 NEXT_PUBLIC_WS_URL=ws://localhost:8010/ws npm run dev
```
