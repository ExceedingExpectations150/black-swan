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

## Residual risk

Findings 7 and 8 are accepted for the hackathon's localhost demo model. Before
any public deployment: provision two distinct Gemini keys and gate `/api/*`
behind authentication.
