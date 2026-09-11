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

The build runs M0 through M9 (`docs/architecture.md`); dockerizing is FUTURE-1 and deferred. At the end of **every** M phase,
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
| `src/api/` | `src/api/CLAUDE.md` - envelope and error conventions, the six routes, who may approve over HTTP |
| `web/` | `web/README.md` - the window: the proxy, the panels and what each reads, the three-command gate, what did not carry over from `agent-lab-ui` |
| `data/knowledge_base/` | the SOPs, one file per sensing world, **derived from `docs/sweetwater-ranch.md` and nothing else.** A rule id is citable only if it is a heading in the file the packet carried |
| `tests/` | `tests/CLAUDE.md` - never Supabase; what each rail proves |
| architecture | `docs/architecture.md` |
| model cost decisions | `docs/model-routing.md` (a ledger, not a plan) |
| log schemas | `docs/logging.md` |
| where the build stands right now | `docs/STATE.md` - the session-start briefing |
| what actually happened | `docs/JOURNEY.md` |
| what is still open across phases, and what each would take | `docs/open-issues.md` |

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
python main.py               # the continuous loop     (live from M4, SPENDS, halts at SPEND_CEILING_USD)
python main.py --no-spend    # the loop (or --once) stopped at the end of the free pass: no evidence, no model, no bill
python main.py --api         # M8. The read API, its own process: reads sw_ops, never the ranch, never a model. Needs OPS_API_TOKEN (exit 2 without). Default 127.0.0.1:8000
curl -s http://127.0.0.1:8000/health         # M8. Then /ops/incidents, /ops/report, /ops/gate, and `curl -N .../ops/stream?limit=1` to watch one tick land

pytest
ruff check .                 # the whole lint gate; `ruff format` is not used, see below
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

**A model never performs a write. From M6 it may propose one, and a human answers.** A work order
may carry `proposed_write`; three code checks (shape, tool, grounding against the page) decide
whether it pauses at all; the pause is a LangGraph `interrupt()` checkpointed in `sw_ops`, so it
outlives the process and the loop keeps ticking; `python -m src.agent.gate` is where a person says
yes or no, or from M8 `POST /ops/gate` with a bearer token from `OPS_API_TOKEN` (`decided_by` is the
name the token maps to, never a body field), and both halves land in `sw_ops.audit_receipts` under one
`audit_id` with `logs/audit.jsonl` as the projection. The gate is for agent writes. `CHAOS_ALLOW_WRITES`
is a different switch for a different actor and stays one.

**The read API never calls the ranch and never calls a model.** It reads `sw_ops`, and that sentence is
the whole architecture: a browser refresh cannot spend a token or fire a sweep. From M8 the loop writes
every tick line to `sw_ops.ticks` and every shift report to `sw_ops.shift_reports` beside the log line,
best-effort and never failing the tick, so `--api` on another box sees exactly what the loop saw.

**The window (`web/`, M9) talks to the read API and nothing else, and the browser never holds the token.**
Next.js App Router; route handlers under `web/app/api/ops/*` proxy every call server-side with `OPS_API_URL`
and `OPS_API_TOKEN` from the window's own environment (`web/.env.local`, gitignored), forward `X-Request-ID`,
and pipe the SSE stream through. Six panels: the token gauge off `/ops/stream`, the incident feed, the rails
read off the latest tick line, the shift report, the gate with approve and reject (approve asks once, because
it performs the write on the deployed ranch), and a ranch-map placeholder that says why it is empty (the
catalog is the ranch's, and one thing talks to the ranch: `docs/open-issues.md` #21). The window has its own gate,
the three `web/` commands above, and it is verified against `next dev` on this machine; the Vercel deploy waits
on where the API lives (#5) and is owed, not faked. `web/README.md` is the detail.

Exits: **0** is a clean drain after Ctrl+C, SIGTERM, or SIGBREAK; **1** is a second Ctrl+C or a tick
raising outside its own guard; **2** is a config refusal (including `--api` without a usable
`OPS_API_TOKEN`); **4** is the spend ceiling, chosen so a restart policy does not relaunch and spend
again and nobody reads it as an outage. **3** was "not built yet", retired at M8 with the last stub, and
is not reused.

**The loop has a hard per-run spend ceiling, and it HALTS.** `SPEND_CEILING_USD` (default
$10, must be positive, no unlimited value) is checked after every tick against the summed
`cost_usd` on the tick lines; reaching it writes `loop_halted` with the reason and exits 4.
Overshoot is bounded at one tick, because a fan-out in flight is already paid for. The ceiling
does not apply to `--once`, where a human is at the keyboard.

**`--once` costs money from M2.** Its last two stages assemble an evidence packet and hand
it to Opus, once per newly-opened incident: 24k to 58k tokens depending on how much of the
ledger is already `ongoing`, and **161k on the M3 tree with five agents and a shift report.**
At the Opus 5 list price in `routing.PRICE_TABLE` that is about **$0.05 per work order**; every
dollar figure written before M7 used a rate 3x too high, and the tokens beside it were the measurement.
**The Tier-1 cascade exists from M7 and ships off** (`TIER1_ENABLED=0`): measured on three ticks, it
saved one call in twelve and was thinner about the neighbour and the herd, so Opus writes every work
order until `docs/model-routing.md` gets a row that says otherwise.
**From M7A the free pass also reads the herd**: the Farm list in waves of 6 (about 18s and 20 requests a tick,
because the Farm API 500s above 6 in flight and its `status` filter cannot find a dead cow), the care record
for the animals whose state changed, and `herd_health` is handed animal incidents for the first time. A
herd order costs about the same as a sensor order. **The herd stage never fails the tick**: a Farm or Care
outage is `herd_error` on the line and no animal resolves.
In code, `run_tick(spend=False)` stops at the end of the free pass, and
`conftest.no_model_calls` makes a forgotten flag fail loudly rather than bill.

**Backoff is per upstream and the heartbeat never stops.** A sick Sensor API, MCP, or ledger
skips the free pass for an exponential window (60s base, 900s cap) and the tick still writes
its line naming who is sick; a sick Farm/Feed/Care or model skips only the spend and holds the
new incidents for the next tick. An incident whose agent raised or whose model call died in
transport is **held**, re-routed next tick, and counted on the line. A rail rejection is never
retried.

**`SW_OPS_TARGET=test` points the ledger at local `sw_ops_test` instead of Supabase**, which
is how a tick gets exercised without writing 23 incidents into the ledger a demo reads from.
Prod is the default on purpose: a default that quietly writes somewhere harmless is a default
that ships.

**`CHAOS_ENABLED` defaults to `0` and belongs at `0`** unless a demo is being driven. Off, the
overlay costs one boolean and opens no connection. On, every sensor reading is suspect by
design, which is correct for a demo and ruinous for a measurement. `CHAOS_ALLOW_WRITES` is a
separate switch, also off, and it is the only thing between a scenario and a real `PATCH` on
the deployed Farm API. Knobs and the demo recipe: `docs/STATE.md`.

Windows venv: `.venv/Scripts/python.exe`. No Docker in the build (dockerizing is FUTURE-1, deferred) - the upstreams are
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
