# Security — Black Swan

Offensive pre-deployment review of the FastAPI backend + Next.js frontend,
run as an authorized pen-test of our own project. This documents what was
probed, what was exploitable, and what was fixed.

## Threat model

The backend exposes unauthenticated state-changing endpoints and a WebSocket,
takes free-text user input (the event headline and desk-chat messages) that
flows into paid LLM prompts, and persists to SQLite. It is designed to run on
localhost for the demo; `DEPLOY.md` describes an optional public deployment,
which widens the surface. The review targeted: input validation, injection,
denial-of-service / cost-exhaustion, secret exposure, CORS/WebSocket origin,
and error-message leakage.

## Findings and fixes

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| 1 | HIGH | `/api/start` accepted unbounded `duration_days` / `ticks_per_day` and `speed=0`, letting one request queue effectively infinite compute and burn Gemini quota | **Fixed** — `StartPayload` bounds duration to ≤365 days, ticks to ≤96/day; total ticks are now finite even at MAX speed |
| 2 | HIGH | `/api/chat` and `/api/event` invoke the paid Gemini API with no rate limit — a curl loop exhausts quota / cost | **Fixed** — dependency-free per-IP sliding-window limiter (20 calls / 60 s) on both endpoints |
| 3 | HIGH | No length cap on the event headline or chat message content fed into LLM prompts (huge-token cost + prompt-injection surface) | **Fixed** — `headline` ≤500 chars, chat `content` ≤2000, `messages` list ≤50; blast radius of injected sector JSON was already bounded to ±0.35 on known sectors only |
| 4 | MEDIUM | `/ws` had no Origin check (CORS middleware doesn't cover WebSockets) and no connection cap — any origin could read the feed and flood connections | **Fixed** — handshake validates `Origin` against `ALLOWED_ORIGINS`, connections capped at 64 |
| 5 | MEDIUM | `/api/start` returned `str(exc)` on engine-init failure, leaking internal paths / package internals | **Fixed** — logs server-side, returns a generic message |
| 6 | MEDIUM | `backend/blackswan.db` was tracked in git despite `*.db` in `.gitignore` (added before the ignore rule) | **Fixed** — `git rm --cached` (working copy kept; no history rewrite) |
| 7 | LOW | Both Gemini key env vars can hold the same value, making 429 key-rotation a no-op | **Documented** — set two distinct keys for real rotation; the model-fallback half still works with one |
| 8 | INFO | No auth on state-changing endpoints | **By design** for the localhost demo. If deployed publicly, put the backend behind auth / a shared secret — findings 1–3 compound otherwise |

## Verified not exploitable

- **SQL injection** — the ticker path param and all DB access use parameterized
  SQLAlchemy ORM lookups; no string-built SQL.
- **Unbounded price/social queries** — `limit=` is clamped server-side (2048 / 200).
- **Secret exposure** — Gemini keys are never echoed in any response, log line,
  or `NEXT_PUBLIC_*` env; `backend/.env` is gitignored and untracked.
- **HTTP CORS** — `allow_origins` is an explicit localhost allowlist, not wildcard.

## Final pre-public pass

A second offensive review was run immediately before making the repo public.

- **Git history secret scan — CLEAN.** `backend/.env` was never committed at
  any point in history; no API key (`AIza…`, `sk-…`, `ghp_…`, PEM) appears in
  any commit. The old `blackswan.db` blobs still reachable in history contain
  only market-sim data (prices, agent state), not secrets — repo bloat, not a
  leak. `.env.example` holds only empty placeholders. Safe to publish.
- **9 (MEDIUM, fixed)** — the WebSocket accepted any client that omitted the
  `Origin` header regardless of `ALLOWED_ORIGINS`, so a native (non-browser)
  client could bypass the allowlist in a public deployment. Now a missing
  Origin is accepted only when the allowlist is entirely localhost (dev);
  public allowlists reject it.
- **10 (LOW, fixed)** — behind a reverse proxy the per-IP rate limiter keyed on
  `request.client.host` (the proxy IP), collapsing to one shared bucket. Now
  reads the first `X-Forwarded-For` hop so the limit stays per-user.
- **Hygiene** — agent-tooling artifacts (`.claude-flow/`, `.swarm/`, `.mcp.json`,
  `CLAUDE.md.pre-ruflo`, machine-local skill symlinks) added to `.gitignore` so
  they can never land in the public history.

## Residual risk

Findings 7 and 8 are accepted for the hackathon's localhost demo model. Before
any public deployment: provision two distinct Gemini keys, gate `/api/*` behind
authentication, and run uvicorn with `--proxy-headers` on trusted hosts.
