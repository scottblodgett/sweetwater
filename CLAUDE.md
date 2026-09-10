# CLAUDE.md

## What this is

The **agentic layer** for Sweetwater Land & Cattle Co., a fourth-generation Wyoming
cattle ranch. One orchestrator runs continuously, driving **five sub-agents**
(`water_feed`, `herd_health`, `infrastructure`, `compliance`, `chaos`) against a ranch
that is genuinely trying to break. Scenario canon: `docs/sweetwater-ranch.md`.

## The one rule that defines the project

On any multi-step task, report back after 5 minutes or when you discover work I
didn't ask for, whichever comes first.

## How milestones close

The build runs M0 through M10 (`docs/architecture.md`). At the end of **every** M phase,
in this order, no exceptions:

1. **Run the gate.** `pytest`, `ruff check .`, `mypy src main.py`, and the phase's own
   live verification. All green, or the phase is not done.
2. **Update the docs.** `docs/JOURNEY.md` gets what actually happened, what diverged from
   the plan, and every defect the phase caught in itself. `docs/cookbook.md` gets any
   lesson general enough to bite again. Touch `docs/model-routing.md` if a job changed
   tiers, and the relevant nested `CLAUDE.md` if a rule changed.
3. **Commit** to `main`. No feature branches, no PRs.
4. **Stop and check in with Scott.** Do not roll into the next phase unprompted.

Docs are updated at the phase boundary rather than at the end of the build, because a
reconstruction only records the decisions that worked.

## The upstream is frozen and is not ours

The four REST APIs and the MCP server are **already deployed on Lambda** and live in a
different repo (`scott-jasper/mcp-farm`, read-only). **19 flat-named tools** plus the
`ranch://sensors/map` resource are what exist. If one is genuinely missing that is a
scoped change over there and a conversation, never a drive-by. This repo adds no
endpoints to the ranch.

## Two rules that decide most arguments

1. **Code owns what a machine consumes; the model owns what a human judges.**
   Severity is `triage.py`'s, always. A model that is handed a verdict and asked to
   justify it fabricates the justification.
2. **The cheap path is a cheaper architecture, not just a cheaper model.**
   `evidence.py` assembles the packet in code so a model judges one page instead of
   driving a tool loop. Local models write well and navigate badly.

## Where the detail lives

| Reading or touching | Read first |
| --- | --- |
| `src/agent/` | `src/agent/CLAUDE.md` - graph shape, the tick contract, escalation |
| `src/tools/` | `src/tools/CLAUDE.md` - allowlists, severity ownership, chaos guards |
| `src/models/` | `src/models/CLAUDE.md` - the Ollama traps, when thinking may be off |
| `src/api/` | `src/api/CLAUDE.md` - envelope and error conventions |
| `tests/` | `tests/CLAUDE.md` - never Supabase; what each rail proves |
| architecture | `docs/architecture.md` |
| model cost decisions | `docs/model-routing.md` (a ledger, not a plan) |
| log schemas | `docs/logging.md` |
| what actually happened | `docs/JOURNEY.md` |

## Tech stack

Python **3.11** (3.12 is not installed on this machine; 3.14 breaks native wheels),
LangGraph + `create_react_agent` workers, Pydantic v2, structlog, httpx, SQLAlchemy
async + asyncpg + Alembic, FastAPI, pytest. Next.js for the window only (M9).

Agent state lives in a **new `sw_ops` schema** in the existing Supabase project.
Nothing in this repo touches the ranch schemas (`farm`, `feed`, `animal_care`); the
only way to reach ranch data is over HTTP through the deployed APIs.

## Commands

```bash
python main.py --handshake   # prove the deployed ranch is reachable, then exit
python main.py --once        # exactly one tick
python main.py               # the continuous loop
python main.py --api         # read API only

pytest
ruff check . && ruff format --check .
mypy src main.py
```

Windows venv: `.venv/Scripts/python.exe`. No Docker until M9 - the upstreams are
already deployed and `sw_ops` is in Supabase, so there is nothing local to stand up.

## Conventions

- **No em dashes.** Single dash or comma.
- Wide lines are fine (ruff `line-length = 140`). Do not fragment a line vertically
  that reads fine horizontally.
- Timestamps: UTC ISO 8601 with **milliseconds** (`2026-09-10T14:30:00.000Z`), matching
  the upstream services exactly, because lexicographic ordering is relied upon.
- Never log `ANTHROPIC_API_KEY`, `DATABASE_URL`, or a full prompt or response body.
- Cross-service IDs are **plain strings, never foreign keys.** Orphans are allowed on
  purpose. Add no existence checks against another service.
