# CLAUDE.md

## What this is

The **agentic layer** for Sweetwater Land & Cattle Co., a fourth-generation Wyoming
cattle ranch. One orchestrator runs continuously, driving **five sub-agents**
(`water_feed`, `herd_health`, `infrastructure`, `compliance`, `chaos`) against a ranch
that is genuinely trying to break. Scenario canon: `docs/sweetwater-ranch.md`.

## Starting a session

**Read `docs/STATE.md` first, then the nested `CLAUDE.md` for the package you are about to
touch, then stop and check in.** That file is the briefing: where the build stands, the
decisions already made, the verified environment, and the live ranch facts already paid
for. It is refreshed at every milestone boundary.

Then, until asked otherwise:

- **Do not read the doc set to get oriented.** The table at the bottom of this file says
  which doc answers which question. Go to one when you have that question.
- **Do not probe the live ranch to learn its topology.** 160 sensors, 13 types, units, and
  observed ranges are in `docs/STATE.md`. Probe to verify a change, not to orient.
- **Do not read `C:\temp\MCP-Farm` or any other repo.** See the frozen-upstream rule below.
- **Keep answers short.** No recap tables of what was already agreed, no restating the plan
  back. Batch independent reads and commands into one round, and summarize command output
  rather than pasting it.

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
   tiers, and the relevant nested `CLAUDE.md` if a rule changed. Then **run every command
   the docs claim works**, including the Commands block below: M0 shipped a documented
   command the repo's own config could never satisfy, and no test caught it because tests
   do not read markdown.
3. **Commit** to `master` (Scott's preferred default branch name, not `main`). No
   feature branches, no PRs.
4. **Stop and check in with Scott.** Do not roll into the next phase unprompted.

Docs are updated at the phase boundary rather than at the end of the build, because a
reconstruction only records the decisions that worked.

## The upstream is frozen and is not ours

The four REST APIs and the MCP server are **already deployed on Lambda** and live in a
different repo (`scott-jasper/mcp-farm`, read-only). **19 flat-named tools** plus the
`ranch://sensors/map` resource are what exist. If one is genuinely missing that is a
scoped change over there and a conversation, never a drive-by. This repo adds no
endpoints to the ranch.

**The contract is the tools and the REST surface, not that repo's source.** A clone sits at
`C:\temp\MCP-Farm`; do not read it. A constant copied out of its internals is a value
nothing here can verify, and it fails silently when the other side retunes it. Everything
needed is reachable over the wire or already written down in this repo.

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
| `data/knowledge_base/` | the SOPs, one file per sensing world, **derived from `docs/sweetwater-ranch.md` and nothing else.** A rule id is citable only if it is a heading in the file the packet carried |
| `tests/` | `tests/CLAUDE.md` - never Supabase; what each rail proves |
| architecture | `docs/architecture.md` |
| model cost decisions | `docs/model-routing.md` (a ledger, not a plan) |
| log schemas | `docs/logging.md` |
| where the build stands right now | `docs/STATE.md` - the session-start briefing |
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
python main.py --once        # exactly one tick        (live, and SPENDS from M2)
python main.py               # the continuous loop     (stub until M4, exits 3)
python main.py --api         # read API only           (stub until M8, exits 3)

pytest
ruff check .                 # the whole lint gate; `ruff format` is not used, see below
mypy src main.py
```

An unbuilt mode exits **3** with a `not_implemented` line naming the milestone that
brings it, rather than failing as if it were broken.

**`--once` costs money from M2.** Its last two stages assemble an evidence packet and hand
it to Opus, once per newly-opened incident: 24k to 58k tokens depending on how much of the
ledger is already `ongoing`. In code, `run_tick(spend=False)` stops at the end of the free
pass, and `conftest.no_model_calls` makes a forgotten flag fail loudly rather than bill.

Windows venv: `.venv/Scripts/python.exe`. No Docker until M10 - the upstreams are
already deployed and `sw_ops` is in Supabase, so there is nothing local to stand up.

## Conventions

- **No em dashes.** Single dash or comma.
- Wide lines are fine. `E501` is ignored on purpose, so `ruff check` permits a long line
  that reads better than its wrapped form. **`ruff format` is deliberately not in the
  gate:** it hard-wraps at `line-length = 140` with no per-line escape hatch, so it would
  fragment exactly the lines this convention exists to protect. The linter is the gate.
- Timestamps: UTC ISO 8601 with **milliseconds** (`2026-09-10T14:30:00.000Z`), matching
  the upstream services exactly, because lexicographic ordering is relied upon.
- Never log `ANTHROPIC_API_KEY`, `DATABASE_URL`, or a full prompt or response body.
- Cross-service IDs are **plain strings, never foreign keys.** Orphans are allowed on
  purpose. Add no existence checks against another service.
