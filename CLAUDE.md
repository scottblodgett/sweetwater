# CLAUDE.md

## What this is

The **agentic layer** for Sweetwater Land & Cattle Co., a fourth-generation Wyoming cattle ranch. One
orchestrator runs continuously, driving **five sub-agents** (`water_feed`, `herd_health`, `infrastructure`,
`compliance`, `chaos`) against a ranch that is genuinely trying to break. Scenario canon:
`docs/sweetwater-ranch.md`. The ranch itself (four REST APIs and an MCP server with 19 tools) is deployed
elsewhere, frozen, and read-only from here; this repo adds nothing to it and never reads its source
(`C:\temp\MCP-Farm`). The contract is the wire. Detail: `docs/STATE.md`, "The boundary rule."

## Starting a session

**Read `docs/STATE.md` first, then the nested `CLAUDE.md` for the package you are about to touch, then stop
and check in.** STATE is the briefing: where the build stands, the decisions already made, the environment,
the live ranch facts already paid for, and the traps. Then, until asked otherwise: do not read the doc set
to get oriented (the table below says which doc answers which question); do not probe the live ranch to
learn its topology (STATE has it; probe to verify a change); keep answers short, batch independent reads
and commands, and summarize output rather than pasting it.

## The one rule that defines the project

On any multi-step task, report back after 5 minutes or when you discover work I didn't ask for, whichever
comes first.

## How a phase closes

The build ran M0 through M9 (`docs/Plan.md`); dockerizing is FUTURE-1 and deferred. At the end of every
phase, in this order, no exceptions: **1.** run the gate (`pytest`, `ruff check .`, `mypy src main.py`, the
three `web/` commands, and the phase's own live verification), all green or the phase is not done; **2.**
update the docs (`docs/JOURNEY.md` gets what happened and every defect the phase caught in itself,
`docs/cookbook.md` gets any lesson general enough to bite again, `docs/model-routing.md` if a job changed
tiers, `docs/STATE.md` refreshed, the nested `CLAUDE.md` if a rule changed), then **run every command the
docs claim works**, because tests do not read markdown; **3.** commit to `master` (not `main`), no feature
branches, no PRs; **4.** stop and check in with Scott. Do not roll into the next phase unprompted.

## Two rules that decide most arguments

1. **Code owns what a machine consumes; the model owns what a human judges.** Severity is `triage.py`'s,
   always. A model handed a verdict and asked to justify it fabricates the justification.
2. **The cheap path is a cheaper architecture, not just a cheaper model.** `evidence.py` assembles the
   packet in code so a model judges one page instead of driving a tool loop. Local models write well and
   navigate badly.

A model never performs a write; from M6 it may propose one and a human answers (`src/agent/CLAUDE.md`, the
gate). The read API never calls the ranch or a model (`src/api/CLAUDE.md`). The window never holds the API
token (`web/README.md`). The loop has a spend ceiling that halts with exit 4, backoff per upstream, and a
heartbeat that never stops (`src/agent/CLAUDE.md`, the loop). `--once` spends from M2, about $0.05 a work
order; the demo knobs, `SW_OPS_TARGET=test`, and `CHAOS_ENABLED` belonging at 0 are in `docs/STATE.md`.

## Where the detail lives

| Reading or touching | Read first |
| --- | --- |
| `src/agent/` | `src/agent/CLAUDE.md` - the tick contract, the loop, the rails, the gate, escalation |
| `src/tools/` | `src/tools/CLAUDE.md` - the frozen upstream on the wire, allowlists, severity ownership, the herd sweep, chaos guards |
| `src/models/` | `src/models/CLAUDE.md` - the Ollama traps, when thinking may be off, two credentials |
| `src/api/` | `src/api/CLAUDE.md` - envelope and error conventions, the six routes, who may approve over HTTP |
| `web/` | `web/README.md` - the proxy, the panels, the three-command gate |
| `data/knowledge_base/` | the SOPs, one file per sensing world, **derived from `docs/sweetwater-ranch.md` and nothing else.** A rule id is citable only if it is a heading in the file the packet carried |
| `tests/` | `tests/CLAUDE.md` - never Supabase, never a model; what each rail proves |
| what it is and why: the tree, the architecture, the log schemas, the milestones | `docs/Plan.md` |
| model cost decisions | `docs/model-routing.md` (a ledger, not a plan) |
| where the build stands right now | `docs/STATE.md` - the session-start briefing |
| what actually happened | `docs/JOURNEY.md` |
| the lessons, by pain | `docs/cookbook.md` |
| what is still open, and what each would take | `docs/open-issues.md` |

Stack and layout: `docs/Plan.md`. Interpreter: `.venv/Scripts/python.exe`, Python 3.11. No Docker.

## Commands

```bash
python main.py --handshake   # prove the deployed ranch is reachable, then exit
python main.py --once        # exactly one tick        (live, and SPENDS from M2)
python main.py               # the continuous loop     (live from M4, SPENDS, halts at SPEND_CEILING_USD)
python main.py --no-spend    # the loop (or --once) stopped at the end of the free pass: no evidence, no model, no bill
python main.py --api         # M8. The read API, its own process: reads sw_ops, never the ranch, never a model. Needs OPS_API_TOKEN (exit 2 without). Default 127.0.0.1:8000
curl -s http://127.0.0.1:8000/health         # M8. Then /ops/incidents, /ops/report, /ops/gate, and `curl -N .../ops/stream?limit=1` to watch one tick land

pytest
ruff check .                 # the whole lint gate; `ruff format` is deliberately not used (it would wrap the long lines E501 permits)
mypy src main.py

python -m src.tools.chaos status   # M5. Also plan / inject / expire / restore, recipe in docs/STATE.md
python -m src.agent.gate list      # M6. The writes agents proposed and nobody has answered. Also approve <audit_id> --by NAME, reject <audit_id> --by NAME --reason TEXT

TIER1_ENABLED=1 python main.py --once                 # M7. The cascade: warning-severity work orders on the local model, Opus for critical and for every escalation. Ships OFF
TIER1_ENABLED=1 TIER_COMPARE=1 python main.py --once  # M7. The measurement: every local order shadowed by Opus on the same page into logs/compare.jsonl. SPENDS

cd web && npm install            # M9. The window, its own npm project (Node 22). Once
cd web && npm run dev            # M9. http://localhost:3000 against the API named in web/.env.local (OPS_API_URL, OPS_API_TOKEN; copy web/.env.example)
cd web && npx tsc --noEmit       # M9. The window's gate, part 1 of 3: no type errors
cd web && npm run lint           # M9. Part 2: the linter the template ships with (eslint-config-next), zero problems
cd web && npm run build          # M9. Part 3: `next build` succeeds
```

## Conventions

- **No em dashes.** Single dash or comma.
- Wide lines are fine. `E501` is ignored on purpose; `ruff check` is the gate and `ruff format` is not (STATE decision 14).
- Timestamps: UTC ISO 8601 with **milliseconds** (`2026-09-10T14:30:00.000Z`), matching the upstream exactly.
- Never log `ANTHROPIC_API_KEY`, `DATABASE_URL`, `OPS_API_TOKEN`, or a full prompt or response body.
- Cross-service IDs are **plain strings, never foreign keys.** Orphans are allowed on purpose.
