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

---

# Ruflo Orchestration Layer (ruflo init)


## Rules

- Do what has been asked; nothing more, nothing less
- NEVER create files unless absolutely necessary — prefer editing existing files
- NEVER create documentation files unless explicitly requested
- NEVER save working files or tests to root — use `/src`, `/tests`, `/docs`, `/config`, `/scripts`
- ALWAYS read a file before editing it
- NEVER commit secrets, credentials, or .env files
- NEVER add a `Co-Authored-By` trailer to user commits unless this project's `.claude/settings.json` has `attribution.commit` set (#2078). The Claude Code Bash tool may suggest one in its default commit-message template — ignore it. `Co-Authored-By` is semantic authorship attribution under git/GitHub convention; the tool is the facilitator, not a co-author.
- Keep files under 500 lines
- Validate input at system boundaries

## Agent Comms (SendMessage-First Coordination)

Named agents coordinate via `SendMessage`, not polling or shared state.

```
Lead (you) ←→ architect ←→ developer ←→ tester ←→ reviewer
              (named agents message each other directly)
```

### Spawning a Coordinated Team

```javascript
// ALL agents in ONE message, each knows WHO to message next
Agent({ prompt: "Research the codebase. SendMessage findings to 'architect'.",
  subagent_type: "researcher", name: "researcher", run_in_background: true })
Agent({ prompt: "Wait for 'researcher'. Design solution. SendMessage to 'coder'.",
  subagent_type: "system-architect", name: "architect", run_in_background: true })
Agent({ prompt: "Wait for 'architect'. Implement it. SendMessage to 'tester'.",
  subagent_type: "coder", name: "coder", run_in_background: true })
Agent({ prompt: "Wait for 'coder'. Write tests. SendMessage results to 'reviewer'.",
  subagent_type: "tester", name: "tester", run_in_background: true })
Agent({ prompt: "Wait for 'tester'. Review code quality and security.",
  subagent_type: "reviewer", name: "reviewer", run_in_background: true })

// Kick off the pipeline
SendMessage({ to: "researcher", summary: "Start", message: "[task context]" })
```

### Patterns

| Pattern | Flow | Use When |
|---------|------|----------|
| **Pipeline** | A → B → C → D | Sequential dependencies (feature dev) |
| **Fan-out** | Lead → A, B, C → Lead | Independent parallel work (research) |
| **Supervisor** | Lead ↔ workers | Ongoing coordination (complex refactor) |

### Rules

- ALWAYS name agents — `name: "role"` makes them addressable
- ALWAYS include comms instructions in prompts — who to message, what to send
- Spawn ALL agents in ONE message with `run_in_background: true`
- After spawning: STOP, tell user what's running, wait for results
- NEVER poll status — agents message back or complete automatically

## Swarm & Routing

### Config
- **Topology**: hierarchical-mesh (anti-drift)
- **Max Agents**: 15
- **Memory**: hybrid
- **HNSW**: Enabled
- **Neural**: Enabled

```bash
npx @claude-flow/cli@latest swarm init --topology hierarchical --max-agents 8 --strategy specialized
```

### Agent Routing

| Task | Agents | Topology |
|------|--------|----------|
| Bug Fix | researcher, coder, tester | hierarchical |
| Feature | architect, coder, tester, reviewer | hierarchical |
| Refactor | architect, coder, reviewer | hierarchical |
| Performance | perf-engineer, coder | hierarchical |
| Security | security-architect, auditor | hierarchical |

### When to Swarm
- **YES**: 3+ files, new features, cross-module refactoring, API changes, security, performance
- **NO**: single file edits, 1-2 line fixes, docs updates, config changes, questions

### 3-Tier Model Routing

| Tier | Handler | Use Cases |
|------|---------|-----------|
| 1 | Agent Booster (WASM) | Simple transforms — skip LLM, use Edit directly |
| 2 | Haiku | Simple tasks, low complexity |
| 3 | Sonnet/Opus | Architecture, security, complex reasoning |

## Memory & Learning

### Before Any Task
```bash
npx @claude-flow/cli@latest memory search --query "[task keywords]" --namespace patterns
npx @claude-flow/cli@latest hooks route --task "[task description]"
```

### After Success
```bash
npx @claude-flow/cli@latest memory store --namespace patterns --key "[name]" --value "[what worked]"
npx @claude-flow/cli@latest hooks post-task --task-id "[id]" --success true --store-results true
```

### MCP Tools (use `ToolSearch("keyword")` to discover)

| Category | Key Tools |
|----------|-----------|
| **Memory** | `memory_store`, `memory_search`, `memory_search_unified` |
| **Bridge** | `memory_import_claude`, `memory_bridge_status` |
| **Swarm** | `swarm_init`, `swarm_status`, `swarm_health` |
| **Agents** | `agent_spawn`, `agent_list`, `agent_status` |
| **Hooks** | `hooks_route`, `hooks_post-task`, `hooks_worker-dispatch` |
| **Security** | `aidefence_scan`, `aidefence_is_safe`, `aidefence_has_pii` |
| **Hive-Mind** | `hive-mind_init`, `hive-mind_consensus`, `hive-mind_spawn` |

### Background Workers

| Worker | When |
|--------|------|
| `audit` | After security changes |
| `optimize` | After performance work |
| `testgaps` | After adding features |
| `map` | Every 5+ file changes |
| `document` | After API changes |

```bash
npx @claude-flow/cli@latest hooks worker dispatch --trigger audit
```

## Agents

**Core**: `coder`, `reviewer`, `tester`, `planner`, `researcher`
**Architecture**: `system-architect`, `backend-dev`, `mobile-dev`
**Security**: `security-architect`, `security-auditor`
**Performance**: `performance-engineer`, `perf-analyzer`
**Coordination**: `hierarchical-coordinator`, `mesh-coordinator`, `adaptive-coordinator`
**GitHub**: `pr-manager`, `code-review-swarm`, `issue-tracker`, `release-manager`

Any string works as a custom agent type.

## Build & Test

- ALWAYS run tests after code changes
- ALWAYS verify build succeeds before committing

```bash
npm run build && npm test
```

## CLI Quick Reference

```bash
npx @claude-flow/cli@latest init --wizard           # Setup
npx @claude-flow/cli@latest swarm init --v3-mode     # Start swarm
npx @claude-flow/cli@latest memory search --query "" # Vector search
npx @claude-flow/cli@latest hooks route --task ""    # Route to agent
npx @claude-flow/cli@latest doctor --fix             # Diagnostics
npx @claude-flow/cli@latest security scan            # Security scan
npx @claude-flow/cli@latest performance benchmark    # Benchmarks
```

26 commands, 140+ subcommands. Use `--help` on any command for details.

## Setup

```bash
claude mcp add claude-flow -- npx -y ruflo@latest mcp start
npx ruflo@latest doctor --fix
```

> The background `daemon` is optional. It runs interval workers that each spawn
> a headless `claude` session, so it consumes tokens continuously. Start it only
> if you want those sweeps: `npx ruflo@latest daemon start` (self-stops after 12h
> by default; `--ttl 0` to disable, `daemon status --all` to audit running daemons).

**Agent tool** handles execution (agents, files, code, git). **MCP tools** handle coordination (swarm, memory, hooks). **CLI** is the same via Bash.
