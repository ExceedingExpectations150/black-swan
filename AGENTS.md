# Black Swan — Agent Guide (Codex)

Neuro-symbolic market twin (AMD ACT 2 hackathon): a FastAPI simulation engine
(TimesFM quant funds + LLM behavioral cohorts trading a continuous double
auction over 51 real companies) with a Next.js 14 Bloomberg-terminal-style
dashboard. PRD.md in this folder is the master spec.

## Run

- Backend: `cd backend && python -m uvicorn main:app --port 8000`
  (needs `backend/.env` with Gemini keys; SQLite `blackswan.db` auto-seeds).
- Frontend: `cd frontend && npm run dev` → http://localhost:5055
  (point at a non-default backend port with `NEXT_PUBLIC_API_BASE` /
  `NEXT_PUBLIC_WS_URL` in `frontend/.env.local`).
- Gate before pushing: `cd frontend && npx tsc --noEmit && npm run build`
  must stay green.

## Hard rules

- The WebSocket envelope `{ type, tick_id, ts, payload }` is a frozen
  frontend/backend contract — never change its shape unilaterally.
- Never commit `.env`, `.env.local`, `node_modules/`, `__pycache__/`.
- Conventional commits (`feat:`, `fix:`, `chore:`).
- Never push directly to `main`; work on branches and open PRs.

## Working with Claude (agent bridge)

You are co-working this repo with Claude Code. Coordinate through
`.agents/bridge/` — it is the single channel between agents:

1. **At session start**, read `.agents/bridge/inbox-codex.md` (messages
   addressed to you) and `.agents/bridge/WORKLOG.md` (file ownership).
2. **To message Claude**, append to `.agents/bridge/inbox-claude.md`:
   a `## [YYYY-MM-DD HH:MM] from codex` heading plus a short message.
   Append-only — never edit or delete existing entries in any bridge file.
3. **Mark items handled** in your own inbox by ticking their `- [ ]` to
   `- [x]`. Do not tick items in Claude's inbox.
4. **Claim before you edit.** Before touching a file, add to WORKLOG.md:
   `- [date] codex CLAIMS <path> — <reason>`. If another agent holds an
   unreleased claim on that file, message them instead of editing. Release
   with a matching `RELEASES` line when done.
5. Keep messages short and factual: what changed, what you need, what is
   blocked. Reference commits by hash.

Skills for this repo live in `.agents/skills/` (universal directory read by
Codex and other agents; `skills-lock.json` pins sources). Claude may invoke
you synchronously via `codex exec` using the `codex` skill; treat those
prompts as coming from Claude and answer tersely.

## Task routing (your lane)

You and Claude split work by benchmark-backed strengths. Your lane:
terminal/shell automation, build and environment setup, bulk mechanical
edits (renames, lint sweeps, boilerplate, test scaffolding, repetitive
migrations), isolated single-file functions and scripts, log trawls, and
first-pass review sweeps. Claude's lane: architecture, multi-file
features, whole-repo debugging, frontend design, orchestration, and final
review. When a task Claude hands you turns out to need repo-wide judgment
or design taste, say so in your answer instead of guessing — Claude will
take it back. Keep your outputs tight; they are drafts Claude verifies.
