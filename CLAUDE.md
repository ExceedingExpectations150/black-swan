# Black Swan — Agent Guide (Claude)

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

## Working with Codex (agent bridge)

You are co-working this repo with OpenAI Codex. Coordinate through
`.agents/bridge/` — it is the single channel between agents:

1. **At session start**, read `.agents/bridge/inbox-claude.md` (messages
   addressed to you) and `.agents/bridge/WORKLOG.md` (file ownership).
2. **To message Codex**, append to `.agents/bridge/inbox-codex.md`:
   a `## [YYYY-MM-DD HH:MM] from claude` heading plus a short message.
   Append-only — never edit or delete existing entries in any bridge file.
3. **Mark items handled** in your own inbox by ticking their `- [ ]` to
   `- [x]`. Do not tick items in Codex's inbox.
4. **Claim before you edit.** Before touching a file, add to WORKLOG.md:
   `- [date] claude CLAIMS <path> — <reason>`. If another agent holds an
   unreleased claim on that file, message them instead of editing. Release
   with a matching `RELEASES` line when done.
5. Keep messages short and factual: what changed, what you need, what is
   blocked. Reference commits by hash.

For synchronous collaboration (delegating a task to Codex mid-session and
reading its answer immediately), use the project-local `codex` skill at
`.agents/skills/codex/SKILL.md` — it wraps `codex exec` with the right
sandbox and resume flags. Use the bridge inboxes for asynchronous,
cross-session coordination.

## Task routing (Claude Max + Codex Pro are separate quotas)

The user pays for both subscriptions. Work delegated to Codex costs zero
Claude tokens, so route by who is better at the task AND how token-heavy
it is (benchmarks as of 2026-07: SWE-bench Pro Claude 69-80% vs GPT ~59%;
Terminal-Bench GPT ~83% vs Claude ~75%; Codex uses ~4x fewer tokens on
mechanical tasks; blind reviewers prefer Claude's code 67% vs 25%).

**Delegate to Codex by default** (it wins these, and they burn tokens):
- Terminal/shell automation, build and environment setup, dependency fixes
- Bulk mechanical edits: renames, lint sweeps, boilerplate, test
  scaffolding, repetitive migrations across many files
- Isolated single-file functions, algorithms, quick scripts
- Log trawls and large-output analysis (high volume, low judgment)
- First-pass review sweeps used as a second opinion

**Keep on Claude** (it wins these; spend the Max quota here):
- Architecture, multi-file features, complex refactors, whole-repo
  debugging (repo-level comprehension is Claude's largest lead)
- Frontend design, UI polish, anything taste-sensitive
- Long-horizon autonomous runs and orchestration (Claude stays the
  orchestrator: it decomposes, delegates to Codex, verifies the result)
- Final review judgment, PR writing, docs, anything touching the frozen
  WS envelope contract

Claude always verifies delegated work before committing it (build gate +
spot-read). Codex output is a draft until Claude has checked it.
