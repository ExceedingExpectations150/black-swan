# The Black Swan Development Workflow

The exact pipeline used to build, polish, and verify this project — written
down so any session (Claude, Codex, or human) can rerun it.

## Roles

| Actor | Lane |
|---|---|
| **Claude (Fable 5)** — orchestrator | Architecture, multi-file features, whole-repo debugging, frontend design, final review judgment. Decomposes work, delegates, verifies everything before commit. |
| **Codex (GPT, via `codex exec`)** | Isolated mechanical edits, terminal/build chores, bulk sweeps, first-pass reviews. Cheaper per token and on a separate quota — see the routing table in `CLAUDE.md` / `AGENTS.md`. |
| **Ultracode workflows** | Fan-out phases: multi-lens bug hunts, adversarial verification panels. |

## The loop (per polish cycle)

```
1  SCOUT      read the actual code paths for each reported symptom; find root
              causes before writing anything (never fix the symptom)
2  SPLIT      route each fix: judgment/multi-file -> Claude, isolated
              mechanical -> Codex (codex exec --sandbox workspace-write,
              one file per task, verify its diff before accepting)
3  FIX        smallest correct change; every changed line traces to a symptom
4  TEST       backend: python -m py_compile + pytest (tests/ — including
              tests that prove MARKET REALITY: volume clears, panic moves
              prices through order flow only)
              frontend: npx tsc --noEmit; npm run build only when the dev
              server is STOPPED (building while dev runs corrupts .next)
5  RUN        restart backend fresh; drive the real flow headlessly first
              (curl /api/event + /api/start, assert tick/sim-time/volume),
              then eyeball it in Chrome (boot -> dashboard -> charts)
6  REVIEW     ultracode Workflow: 4 finder lenses (python correctness,
              market mechanics, frontend state, time integrity) ->
              adversarial verifier per finding (prompted to REFUTE; only
              unrefuted findings count) -> LOOP UNTIL DRY: repeat rounds
              until 2 consecutive rounds surface nothing new
7  APPLY      fix confirmed findings (same routing as step 2), rerun step 4
8  SHIP       commit (conventional messages), push branch, draft PR only —
              never merge to main from a session
```

## Looping machinery

- **Review loops**: the Workflow tool's loop-until-dry pattern (step 6) —
  fixed counts miss the tail; two consecutive empty rounds is the exit.
- **Recurring/watch loops**: the built-in `/loop` skill (self-paced when no
  interval given) for "keep checking until X" tasks.
- Ecosystem alternatives if ever wanted: `getpaseo/paseo@paseo-loop`,
  `fstandhartinger/ralph-wiggum` (install requires naming the source).

## Non-negotiable gates

- **No synthetic market data.** Prices move only through the matching
  engine on real orders. Any "make the chart look alive" shortcut is a bug.
- **`tests/test_behavioral_agents.py` must pass** — it enforces the above.
- **`npx tsc --noEmit` and `npm run build` green** before any push.
- **Fresh-run E2E** after backend changes: run completes all its ticks,
  a second start resets to tick 1 on a reseeded world.
- **Visual verification in Chrome** for anything user-visible; automation
  tabs throttle timers, so judge speed only in a foregrounded tab.

## Known environment gotchas

- Never `npm run build` while `next dev` runs (white screen; fix: kill dev,
  `rm -rf .next`, restart).
- Killing a dev-server shell can orphan node on the port — free it with
  `netstat -ano | findstr :5055` + `taskkill /F /PID <pid>`.
- SQLite + SQLAlchemy here runs `autoflush=False`: reads inside a seeding
  transaction do NOT see pending adds — flush explicitly (this exact trap
  silently produced a market where nobody owned shares).
- Backend logs: uvicorn hides app-level INFO; check the DB directly
  (sqlite3 in read-only mode) when verifying agent/order behavior.
