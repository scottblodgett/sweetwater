# Sweetwater: the agentic layer

## Context

MCP-Farm solved a good business problem and then buried it under scaffolding. The problem is worth keeping; the archaeology is not.

The product is small and already works: **~6,300 lines of TypeScript** across five services, 42 endpoints, 19 MCP tools, all deployed on Lambda with a live Function URL. The sprawl is everything around it: **4,400 lines** of one-off rung scripts in `agent-lab/`, a separate `agent-lab-ui/` dashboard, a half-built `demo-site/`, a Terraform repo cloned inside the working tree and gitignored, and **~5,000 lines of docs** describing all of it. Four lanes, three deploy stories, twelve rungs of scaffolding still standing after the building went up.

**The four APIs and the MCP server stay exactly where they are, and MCP-Farm goes read-only.** They are a working external product. Rebuilding them in Python is translation work that teaches nothing, and it was the bulk of the previous draft.

What gets built is the part that does not exist yet: **one orchestrator running continuously, driving five sub-agents** (water, health, infrastructure, compliance, and chaos) against the live deployed ranch, in Python, in the standard structure.

### Decisions locked

|                |                                                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Repo           | `scottblodgett/sweetwater`, local `C:\temp\sweetwater`. New repo; MCP-Farm untouched.                                                                        |
| Structure      | The canonical layout from your diagram, mapped 1:1. `requirements.txt`, `pyproject.toml`, `src/{agent,tools,models,prompts,utils,api}`, `tests/`, `data/`, `logs/`, `main.py`. `web/` (M9) is the window, its own npm project, not a workspace. |
| Language       | Python **3.11.9**. 3.12 is not installed on this machine and 3.14 breaks native wheels. Next.js for the window only.                                          |
| Agents         | Orchestrator + **five**: `water_feed`, `herd_health`, `infrastructure`, `compliance`, `chaos`.                                                               |
| Framework      | **LangGraph Python**, hand-wired supervisor + `create_react_agent` workers.                                                                                  |
| Tools          | The **19 existing tools** from the deployed MCP Function URL, per-agent allowlist enforced in code.                                                          |
| Cadence        | Continuously running tick loop in `src/agent/executor.py`, driven by `main.py`.                                                                              |
| Models         | **Local (`gemma4:e4b` via Ollama) by default, Opus on escalation.** Build Opus-only, then move jobs down with a rail.                                        |
| Logging        | structlog, JSON Lines, three streams: `tick` / `agent` / `audit`. Wired at M0, not bolted on.                                                                |
| Agent state    | Existing Supabase project, one **new** schema `sw_ops`. Nothing else touches it.                                                                             |
| How you run it | **Local venv first, Docker last.** `python main.py` against the live APIs and Supabase. No containers in any M phase. Dockerizing is FUTURE-1, deferred.      |

---

### Run local first, dockerize last

Nothing in M0 through M9 needs a container. The upstreams are already deployed, `sw_ops` lives in Supabase, and
the window deploys to Vercel. `docker-compose.yml` is a placeholder for **FUTURE-1**, if and when the hosting
decision (`docs/open-issues.md` #5) calls for it. The one local dependency is a Postgres for the store tests,
which must never point at Supabase. How to run it: `README.md`.

## Structure, mapped to your diagram

```
sweetwater/                                lands at
├── README.md                       M0
├── CLAUDE.md                       M0    lean: orientation, the one rule, commands
├── pyproject.toml                  M0    ruff, mypy, and pytest config in one file. line-length 140, E501 ignored
├── requirements.txt                M0    runtime pins
├── requirements-dev.txt            M0    pytest, ruff, mypy
├── .env / .env.example             M0    MCP_URL, *_API urls, DATABASE_URL, ANTHROPIC_API_KEY, OLLAMA_*, CHAOS_*, LOG_*
├── .gitignore                      M0
├── docker-compose.yml          (FUTURE-1) placeholder; you do not need it to build this, and the phase that fills it is deferred
├── alembic.ini                     M1    points at alembic/; the URL comes from env, never from this file
├── alembic/
│   ├── env.py                      M1    resolves the target through the same resolver main.py uses
│   └── versions/000N_*.py          M1+   0001 sw_ops + incidents, 0002 chaos_events, 0003 debounce, 0004 gate, 0005 held; 0006 subject_id / subject_type (M7A)
├── .claude/rules/                  M0    path-globbed rules for anything too long for a CLAUDE.md
├── src/
│   ├── agent/
│   │   ├── CLAUDE.md               M0    the graph, the tick contract, the escalation tiers
│   │   ├── agent.py                M1    the routing table; the LangGraph supervisor + the five worker factories (M3)
│   │   ├── workers.py              M2    what a worker's ANSWER must satisfy: the rails, to_work_order, the fan-out
│   │   ├── executor.py             M1    THE CONTINUOUS LOOP: the tick body; cadence, backoff, shutdown (M4); the gate stage (M6)
│   │   ├── investigator.py         M10   the third model job: a bounded LangGraph tool loop on the Tier-1 model through langchain-mcp-adapters, fired by `insufficient_information`; measured, ships off
│   │   ├── gate.py                 M6    the human gate: one interrupt() graph per proposed write, propose / pending / decide, and the CLI (`python -m src.agent.gate`)
│   │   ├── state.py                M1    RanchState (LangGraph), Finding, Incident, WorkOrder
│   │   └── memory.py               M1    sw_ops persistence: incidents; chaos events (M5); checkpointer (M6)
│   ├── tools/
│   │   ├── CLAUDE.md               M0    MCP is frozen upstream; allowlist + severity-ownership rules
│   │   ├── mcp_client.py           M0    connect to the deployed Function URL; read ranch://sensors/map
│   │   ├── allowlists.py          (M3)   the five tool slices, enforced in code
│   │   ├── sensors.py              M1    the free-pass sweep (direct httpx, bounded concurrency)
│   │   ├── herd.py                (M7A)  the second free sweep: herd catalog, observations, care tasks, off Farm and Care, same three rules
│   │   ├── evidence.py            (M2)   assemble the packet in CODE (see Model routing)
│   │   ├── triage.py               M1    per-type thresholds; severity is CODE's, not the model's
│   │   └── chaos.py               (M5)   the fifth agent's hands: inject, heal, expire
│   ├── models/
│   │   ├── CLAUDE.md               M0    the Ollama gotchas; when thinking may be turned off
│   │   ├── llm_client.py          (M2)   provider registry + per-call reasoning_effort + usage receipts
│   │   ├── routing.py              M7    job -> tier -> model, the price table, the escalation predicate; inverted at M10 (Tier 1 first for every job, `page_too_long` the one pre-call reason)
│   │   └── embeddings.py           --    SOP retrieval seam; stays a seam in V1, see the note below
│   ├── prompts/
│   │   ├── system_prompts.py      (M2)   shared rules: severity is not yours, cite your SOP, no invented premises
│   │   └── agent_prompts.py       (M3)   the four responder briefs + the supervisor's (chaos's arrives with chaos, M5)
│   ├── utils/
│   │   ├── helpers.py              M0
│   │   ├── logger.py               M0    structlog: three streams, JSON to file, pretty to console
│   │   └── config.py               M0    every upstream from env, no hardcoded endpoints
│   └── api/
│       ├── CLAUDE.md               M0    envelope + error conventions
│       ├── routes.py              (M8)   FastAPI: /ops/incidents, /ops/report, /ops/stream, /ops/gate
│       └── schemas.py             (M8)   Pydantic: Finding, Incident, ShiftReport, GateDecision, ChaosEvent
├── tests/
│   ├── CLAUDE.md                   M0    never Supabase; sw_ops_test schema; what each rail proves
│   ├── conftest.py                 M1    the sw_ops_test fixtures, and the skip when no local Postgres answers
│   ├── test_agent.py               M1    tick contract, routing, the store, schema guards, config + logging; allowlists counted (M3), the no-brief pair (M3, and it did not flail: see docs/transcripts/no-brief-transcript.md), gate resume (M6), the cascade and the planted local all-clear that must escalate (M7), the investigator on the real adapter and the inverted tiers (M10)
│   ├── test_tools.py               M1    triage truth table, sweep concurrency; chaos determinism (M5)
│   └── test_api.py                (M8)   envelope shape, gate endpoints
├── data/
│   ├── examples.json              (M5)   chaos scenario catalog + golden fixtures
│   └── knowledge_base/            (M2)   the SOPs, plus herd.md at M7A. Six files, not four: infrastructure splits into plant / wellhead / sensors, because a packet is billed for every rule in the file it carries (see SOP_FOR_CATEGORY)
├── docs/
│   ├── plan.md                     M1    this file: what it is and why. The tree above is the authority; the architecture and the log schemas live here since the post-M9 review
│   ├── state.md                    M0    the session-start briefing, refreshed at every boundary
│   ├── sweetwater-ranch.md         M0    the scenario canon, copied from MCP-Farm
│   ├── model-routing.md            M0    the tier table + the measurement log (what moved down, when, proof)
│   ├── cookbook.md                 M0    the lessons, as prose, ordered by the pain
│   ├── journey.md                  M0    what actually happened, and where it diverged
│   ├── open-issues.md              M9+   everything still open, in plain language, and what each would take
│   └── transcripts/                M3+   the pinned fixtures: the no-brief pair (M3) and the M7 side-by-side
├── logs/
│   ├── .gitkeep                    M0    *.jsonl gitignored
│   ├── tick.jsonl                  M0    one line per tick        (the heartbeat)
│   ├── agent.jsonl                 M0    one line per model call  (the instrument), first line at M2
│   └── audit.jsonl                 M0    one line per side effect (the receipt), first line at M6
├── web/                            M9    the window: Next.js App Router against `/ops/*` through server-side proxies, so `OPS_API_TOKEN` never reaches a browser. Landed first as `agent-lab-ui` untouched, then ported in place
│   ├── app/                        M9    layout, the one page, `api/ops/*` route handlers (the proxy, including the SSE stream)
│   ├── components/                 M9    the five panels: gauge, feed, rails, summary, gate; the map placeholder
│   ├── lib/                        M9    the envelope types mirroring `src/api/schemas.py`, the fetch and EventSource helpers
│   └── README.md                   M9    run, env, what carried over from `agent-lab-ui` and what did not
└── main.py                         M0    `python main.py` runs the loop; `--api`, `--handshake`, `--once`
```

**The marker column is "lands at," and it is load-bearing.** A bare `M1` means the file is
live as of that milestone. A parenthesized `(M3)` means it sits in the tree as a
docstring-only placeholder naming the milestone that fills it, so an **unbuilt leaf reads as
unbuilt rather than as a hole**, which is exactly the ambiguity an empty `prompts/` produced
once. `--` means it stays a seam in V1. A trailing `(M4)` inside a description marks the part
of a live file that is still to come. `__init__.py` files are omitted as noise.

**One deliberate revision, at the M2 boundary: `src/agent/workers.py` is new to this tree.**
The plan had `agent.py` carrying the routing table and the worker factories together. M2 split
the second half out, because the two answer different questions - `agent.py` says **which agent
owns a category**, `workers.py` says **what an agent's answer must satisfy** - and folding the
rails, the checker, and the fan-out into the routing table reproduces exactly the confusion the
M1 `routing.py` rename existed to remove. Recorded here rather than left to be discovered,
because the M1 lesson was that silent drift is the defect and no test reads a diagram. The
supervisor still lands in `agent.py` at M3.

### Nested CLAUDE.md, and why

The root file in MCP-Farm grew to 644 lines before it got broken up, and the fix that worked was directory-level
files. Start there instead of arriving there. The root `CLAUDE.md` carries what a session needs before its first
action, the one rule, the commands, and a signpost to each nested file, and its signpost table is the list of what
each nested file carries. Target under 80 lines of prose; the Commands block and the signpost table sit on top of
that and are re-run and re-read at every close. `.claude/rules/*.md` is for anything long enough to bloat a
directory file, with a `paths:` glob; nothing has needed one yet.

> **`models/embeddings.py` is a seam, not a feature, in V1.** Each sensing world's SOP set is a handful of rules. Loading a whole SOP file into the agent's prompt beats retrieving over it, and pretending otherwise is the cleverer option rather than the simpler one. The file exists so retrieval has an obvious home when the corpus grows past a prompt.

---

## Architecture

### What this repo is, and what it is not

**Is:** one orchestrator running continuously, driving five sub-agents against a live
ranch.

**Is not:** the ranch. The four REST APIs and the MCP server are already deployed on
Lambda, live in the separate `mcp-farm` repository, and are **frozen and read-only** from here. 19
flat-named tools plus the `ranch://sensors/map` resource are the entire surface. This
repo adds nothing to them.

That boundary is the most important fact in the design. The ranch is an independent
external product; this is a client of it that happens to be smart.

### The shape

```
                    main.py
                       │
        ┌──────────────▼─────────────────────────────────────────┐
        │  executor.py    CONTINUOUS TICK LOOP                    │
        │                                                         │
        │  every tick:   chaos maybe-fires ──┐                     │
        │                sweep ~160 sensors  │  free, 0 tokens     │
        │                herd: 1,195 head    │  free, 0 tokens     │  M7A: the Farm list in waves, the care record for the changed set
        │                triage (code)       │  free, 0 tokens     │
        │                reconcile sw_ops    │  free, 0 tokens     │
        │                route NEW incidents │  free, 0 tokens     │
        │                fan out ────────────┼──┐   <- only spend  │
        │                synthesize          │  │                  │
        │                gate on writes      │  │                  │
        └────────────────────────────────────┼──┼──────────────────┘
                                             │  │ asyncio.gather (bounded)
   ┌──────────┬───────────┬──────────────┬───┘  │
 chaos     water_feed  herd_health  infrastructure  compliance
   │          └───────────┴──────────────┴───────────┘
   │                          │ MCP Streamable HTTP
   │              ┌───────────▼─────────────┐
   │              │  DEPLOYED MCP SERVER    │  Lambda Function URL
   │              │  19 tools + ranch://map │  (frozen, unchanged)
   │              └───────────┬─────────────┘
   │                 Farm / Feed / Sensor / Care APIs
   │                          ▲
   └──────────────────────────┘  chaos writes real animal events here;
                                 sensor faults go to the sw_ops overlay
```

### Six of eleven stages cost nothing

160 sensors and 1,195 head are far too many to hand a model every tick, and a model asked to *find* the
problem burns its budget navigating instead. From M7A the free pass has two sweeps: the sensors, and
the herd off the Farm and Care APIs (`src/tools/herd.py`), which is the only discovery path
`herd_health` has and the reason it is no longer idle by design. So the free pass narrows the ranch to what
is actually wrong, and **only newly-opened incidents ever reach an LLM.**

- **`GET /sensors?limit=500` once** for the catalog, or better, the `ranch://sensors/map`
  resource. That endpoint's query schema is pagination only (no `type` filter), so
  filtering happens in Python. The map-not-paging pattern arrives here by necessity, and
  is the right shape anyway: an agent paging a collection cannot distinguish an empty page
  from the end of the list.
- **Live reads are one GET per sensor**, because `GET /sensors/:id` synthesizes a fresh
  value on every call. ~160 requests per tick, bounded by `SWEEP_CONCURRENCY`. Unbounded
  at 160 is a wall of Lambda cold starts and reads to the other side as a load test.
- **`triage.py` owns severity**, in code, from per-type thresholds, with a warning tier
  between nominal and critical and a `warn_once` on any unrecognized type.
- **`memory.py` reconciles into `sw_ops`**, keyed on sensor plus category, so a persisting
  fault is `ongoing` and never re-alarmed.

### The five agents and their tool slices

Flat tool names mean each allowlist is an explicit literal set, enforced in code and
counted by a test. `create_react_agent` receives a **filtered** list.

| Agent | Tools | Owns |
| --- | --- | --- |
| `water_feed` | `list_sensors` `read_sensor` `get_sensor_readings` `list_feed_products` `list_inventory` `consume_feed`* `restock_feed`* | nobody dies of thirst or hunger; reserves before a storm |
| `herd_health` | `list_animals` `get_animal` `list_observations` `create_observation`* `list_care_tasks` `update_care_task`* | sick, down, dead, or missing animals; the care write path |
| `infrastructure` | `list_sensors` `read_sensor` `get_sensor_readings` `list_pastures` `list_shelters` | containment, power, fuel, no spill no fine |
| `compliance` | `list_sensors` `read_sensor` `get_sensor_readings` `list_pastures` `list_animals` | AUM stocking, habitat, keep the payments |
| `chaos` | **none**, by construction. Its hands are `tools/chaos.py`, direct over REST | breaks the ranch on purpose |

`*` = write. Never called by a model. From M6 a responder may **propose** one in its work order
(`proposed_write`), the proposal pauses in the gate, and a human performs it or refuses it.

**There are eight write tools on the deployed surface, not four.** The four marked above are the
ones inside a responder's slice. The other four - `assign_to_pasture`, `remove_from_pasture`,
`assign_to_shelter`, `remove_from_shelter` - move animals and sit in `UNASSIGNED_TOOLS`. They
are in `WRITE_TOOLS` from M3 regardless, so `assert_callable` already refuses them.

**Chaos claims none of them, and should not.** Chaos writes animal events
**direct over REST** (`PATCH /animals/:animalId` on the Farm API, `POST /animals/:animalId/observations`
on the Care API), because it is a test harness rather than a responder: it needs no judgment,
no tool loop, and no model. Its slice is asserted at **zero tools** by M3's own count rail.
Routing it through the MCP write tools would put it behind the M6 gate for no benefit and would
make the one component whose job is to break things the hardest one to run. `CHAOS_ALLOW_WRITES`
is its gate, and `CHAOS_ANIMAL_COHORT` is its blast radius.

**The gate (M6).** A model still drives no tool loop. A work order may carry one optional `proposed_write`; three
code checks (shape, tool, grounding) run on it; a survivor pauses in a LangGraph `interrupt()` checkpointed in
`sw_ops`, one graph per proposal, so the pause outlives the process and the tick does not wait. A human resumes it
from `python -m src.agent.gate` or `POST /ops/gate`, and both halves leave a receipt under one `audit_id`.
`GATE_LANDED` is True since M6 and made writes proposable, not callable. The three boundaries in code
(`tools_for`, `bound_tools_for` / `proposable_tools_for`, `assert_callable`) are `src/tools/CLAUDE.md`'s; the pause
and its rules are `src/agent/CLAUDE.md`'s.

**The line worth defending: `herd_health` cannot read a sensor, and nothing but
`water_feed` can touch feed.** That is what makes the supervisor real rather than
decorative. Water-feed reports the Alkali Flat tank dry. Herd-health reports three cows in
that pasture with no observation in eight days. Neither can see the other's evidence, and
the supervisor is the only thing that can fuse them into one work order.

Being honest about the seam: three agents legitimately share the sensor read tools. The
isolation that matters is the **brief**, the **SOP set**, and **which sensor types each
agent is pointed at**, not tool-name exclusivity. Carving that further would mean editing
a frozen server.

### Where state lives

| Store | Holds | Owned by |
| --- | --- | --- |
| `sw_ops` schema (Supabase) | incidents, chaos events, shift reports, the LangGraph checkpointer | this repo |
| `farm` / `feed` / `animal_care` schemas | the ranch | the other repo. **Never touched from here.** |
| Sensor readings | nowhere. Synthesized per call. | nobody |

The only path to ranch data is HTTP through the deployed APIs. Cross-service IDs are
plain strings, never foreign keys, and orphans are allowed on purpose.

Two readers sit on `sw_ops` and neither can reach the ranch: the read API (`--api`, M8) reads the
ledger and serves it in the ranch's envelope, and the window (`web/`, M9) reads the API through its
own server-side proxy, so a browser holds no token and cannot spend one. The orchestrator is the
only process that talks upstream.

---

## The chaos agent

The antagonist. Its job is to make the ranch a genuinely hard place so the other four have real work, and so the demo is alive rather than a slideshow.

### Two injection paths, and the asymmetry is forced

**Sensor faults go to an overlay in `sw_ops.chaos_events`**, applied by `tools/sensors.py` on top of the honest
live read before triage sees it. The deployed Sensor API is stateless and synthesizes every reading in code, and
its own fault injector refuses to arm on Lambda, so there is nowhere to write a fault into it. **The deployed API
stays truthful; this repo owns the lie, in one place, under test.**

**Animal events are written for real**, `PATCH /animals/:animalId` on the Farm API and
`POST /animals/:animalId/observations` on the Care API, so `herd_health` discovers them through its own sweep with
no overlay at all. Guarded by `CHAOS_ALLOW_WRITES` and confined to `CHAOS_ANIMAL_COHORT`. The request shapes, the
enums, and the guards: `src/tools/CLAUDE.md`.

### Event catalog (`data/examples.json`)

| Scenario             | Mechanism                                                                          | Who should catch it                                         |
| -------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Sensor dead / dark   | overlay: status `offline`, reading `null`                                          | infrastructure                                              |
| Sensor not working   | overlay: status `degraded`, stale value                                            | infrastructure                                              |
| Out-of-range reading | overlay: implausible value (`-500`, 9,000 gal)                                     | whichever world owns the type                               |
| Tank draining        | overlay: water-level walked down over several ticks                                | water_feed                                                  |
| Fence down           | overlay: `fence-voltage` to ~0 kV                                                  | infrastructure                                              |
| Fuel / battery bleed | overlay: `fuel-level` or `battery-charge` to the low tail                          | infrastructure                                              |
| **Coyote kill**      | **real write**: animal status + observation                                        | herd_health                                                 |
| Animal down / sick   | **real write**: status + observation                                               | herd_health                                                 |
| Storm front          | **correlated**: 3 fence sensors in one allotment + a tank + cattle through the gap | infrastructure **and** herd_health, fused by the supervisor |

The correlated scenarios are the interesting ones. Independent random faults test each agent alone; a storm front is the only thing that tests whether the supervisor can actually fuse two agents' evidence.

### Deterministic, and it heals

Seeded (`CHAOS_SEED`, a pure `plan()`, `randrange` never `choices`), so a seed replays a demo; every event has a
TTL, and expiry is what produces `resolved` incidents. The PRNG picks what breaks; no model is anywhere in it.
Decided at M5: chaos is a peer node in the graph and its decision core is deterministic code.

---

---

## Model routing: local by default, Opus where it earns it

`docs/model-routing.md` is both the design and the ledger, and it is the authority: the rule about when
thinking may be off, the two tiers, the escalation predicate, the all-clear rail, the build order (Opus
first, then move one job down at a time with a row that proves it), and the Ollama traps. What the build
found: there are exactly three model jobs (the per-incident work order, the fused shift report, and from
M10 the investigator), the cascade was built at M7 and measured, and at M10 the tiers were inverted: Tier 1
is asked first for every work order and for the fused report whatever the severity, and Opus is reached only
by a reason read off the local answer, plus one pre-call reason code can know (`page_too_long`). What the
inversion measured, and what `TIER1_ENABLED` ships as, is the M10 section of that ledger. Code-level rules
for the model layer: `src/models/CLAUDE.md`.

**The loop, M10.** Between M2 and M9 no model drove a tool: `evidence.py` assembles the page in code and the
judge reads one page. That is still every first judgment. The one tool loop is `src/agent/investigator.py`,
and it fires only after a Tier-1 judge has said the page was too thin: the read tools in that agent's slice
reach the local model through `langchain-mcp-adapters` (`load_mcp_tools` on the same MCP session the catalog
uses, an interceptor as the allowlist belt), LangGraph's `create_react_agent` runs the loop under a step
ceiling and a wall-clock deadline, code renders the raw tool results into a facts block, and the enriched
page is judged once more at Tier 1. A loop that did not finish on its own leaves nothing on the page. It is
measured in the ledger and ships off; the bounds and the reasons are `src/agent/CLAUDE.md`.

---

## Logging: three streams, JSON Lines, and one field that matters most

Implementation: `src/utils/logger.py`. Wired at **M0**, before anything it measures existed, because an
instrument added after the fact only measures what you already suspected. This section is the schema
for the three files and the tables the loop writes beside them; `docs/journey.md` has how each field arrived.

### Why structlog, and why over the stdlib

The files must be machine readable (the read API and the test rails both parse them)
while the console must be human readable during a thirty-minute watch. structlog gives
both from one call. Configured **over** the stdlib rather than beside it, so `httpx` and
`langchain` chatter lands in the same stream instead of a second, differently-formatted
one that has to be correlated by eye.

### Format

JSON Lines, one object per line. UTC ISO 8601 with **milliseconds**
(`2026-09-10T14:30:00.000Z`), matching the four upstream services exactly, because
lexicographic ordering is relied upon and sorting mixed precision misorders silently.

**`run_id` and `tick` appear on every line in all three files.** That is the whole
correlation story: three streams join on a grep, with no log aggregator required.

Console gets `ConsoleRenderer` when `LOG_CONSOLE_PRETTY=1`, JSON otherwise (for a
container). Files are **always** JSON regardless of environment.

### `logs/tick.jsonl` - the heartbeat, one line per tick

```jsonc
{ "ts":"2026-09-10T14:30:00.000Z","run_id":"9a10c5f00357","tick":42,"duration_ms":8140,
  "store":"prod","catalog_source":"mcp_resource",
  "sensors_read":160,"sensors_failed":0,
  "herd_animals":6,"herd_errors":0,"herd_error":null,
  "findings":21,"critical":6,"opened":2,"ongoing":8,"resolved":4,"pending":7,"dismissed":9,"held_unread":0,
  "agents_routed":["water_feed","infrastructure"],
  "work_orders":9,"work_orders_shipped":9,"work_orders_rejected":0,"escalated":1,
  "tier":2,"tier1_orders":1,"escalations":8,"escalation_reasons":["critical","critical","insufficient_information","insufficient_information","critical","critical","insufficient_information","insufficient_information"],
  "input_tokens":78773,"output_tokens":11186,"cost_usd":0.523485,
  "worlds":["infrastructure","water_feed"],"shift_report":"model","shift_report_violations":[],
  "ledger":{"opened":9,"ongoing":8,"resolved":4},
  "held":0,"skipped_upstreams":[],
  "writes_proposed":1,"writes_duplicate":0,"writes_failed":0,"writes_pending":1,
  "chaos_fired":0,"chaos_healed":0,"chaos_missed":[] }
```

The M7 shape (the M7 fields and the token and cost values are from tick A of the M7 measurement,
2026-09-11). A field is added to this line when the stage that produces it exists, not
before, so a `null` here always means the stage ran and had nothing to say.

**The cascade's four fields, M7, inverted at M10.** `tier` is the highest tier that wrote anything this
tick (the fused report at its own tier from M10), or `null` when no model was called, so a calm tick
reads as no tier rather than as the cheap one. `tier1_orders` is how many stored work orders the local
model wrote. `escalations` is how many stored orders were written by Tier 2 for a reason, and
`escalation_reasons` is one code per such order, in order: `page_too_long`, `rejected`,
`insufficient_information`, `proposed_write`, `no_answer` (`routing.ESCALATION_REASONS`; `critical`
left the vocabulary at M10), plus `report:<reason>` when Opus rewrote the fused report. `escalated`,
older, is the model's own `escalate` flag on the order and means "a human above the crew should know";
it is a different fact. `input_tokens` and `output_tokens` include the Tier-1 attempt behind an
escalation; `cost_usd` bills only the Tier-2 half, per order at that order's model, and the report at
`ShiftReport.tier`. **It excludes `TIER_COMPARE` shadows**, which are not stored orders; the real bill
of a compare run is this plus `compare.jsonl`'s `escalation=""` rows at Opus's rate.

**The report's two and the investigator's three, M10.** `report_tier` is which tier wrote the report a
model wrote (`null` when code assembled it without a call) and `report_escalation` is why Opus was paid
for it when it was. `investigations` is how many loops ran this tick, `investigation_steps` their tool
calls in total, and `investigation_outcomes` a map of outcome to count (`answered`, `step_ceiling`,
`deadline`, `no_tool_calls`, `error`). A high `no_tool_calls` or `step_ceiling` beside a low `answered`
is the local model thrashing, which is the row the ledger wants to write from one line.

**The gate's four fields, M6.** `writes_proposed` is how many proposals paused for a human this
tick; `writes_duplicate` is how many were suppressed because the same write for the same incident
was already waiting; `writes_failed` is how many the gate could not persist (their incidents are
held with reason `gate_unavailable`); `writes_pending` is everything waiting across the ledger after
this tick, **or `null` when the tick had nothing to propose and never opened the gate**, because a
count nobody measured must not read as zero.

**`cost_usd` arrived at M4, not M7 as planned**, because the loop's spend ceiling is
denominated in dollars and a ceiling in tokens is a multiplication somebody does wrong at 2am.
From M7 it is summed per order at that order's model from `routing.PRICE_TABLE`, Tier 1 at $0.00;
before M7 it was one assumed rate, which turned out to be 3x the Opus 5 list price. Exactly
`0.0` on a tick that billed nothing. The loop sums it per run and halts at `SPEND_CEILING_USD`.

**`pending` and `dismissed` are the debounce** (migration 0003). `pending` was flagged this sweep
and not yet seen on `INCIDENT_CONFIRM_SWEEPS` consecutive sweeps; `dismissed` was pending and read
clean. Neither is routed or billed. A high `dismissed` beside a low `opened` is the simulator's
dice being filtered out, and it is the number that used to be the bill.

**The loop's five fields.** `held` is incidents carried unanswered into the next tick because
their agent raised, the model died in transport, or the spend stages were in backoff; they are
re-routed next tick, and a rail rejection is never among them. `skipped_upstreams` names who was
inside a backoff window; a line with `["sensor"]`, `error: null`, and `sensors_read: 0` is a
heartbeat during an outage, not a calm ranch. `chaos_fired` and `chaos_healed` are what the tick
armed and expired when chaos is on. **`chaos_missed` is the one to grep for**: an event this run
injected that healed without its sensor ever being read. Non-empty means a fault was born and
died between two sweeps and the ranch read calm the whole time.

**A tick in free-pass backoff writes a short line**, `skipped_upstreams`, `held`, `cost_usd`,
`error`, `failed_stage`, and nothing about sensors or the ledger, because nothing was attempted.
A tick that writes no line is indistinguishable from a dead loop, and that rule holds hardest
exactly when an upstream is down.

**`worlds` and `shift_report` are read as a pair, and that is the only reason both are here.**
`worlds` is the storm-front count, `shift_report` is `"model"` or `"code"`, and together they
say whether the supervisor paid to fuse the tick or assembled it for free. One world is always
`"code"` by construction (`FUSION_THRESHOLD`), so a two-world tick reading `"code"` is either a
free pass or a rail that fired, which is what `shift_report_violations` disambiguates. **A
non-empty violations list beside `"code"` is the fallback having shipped**, and it is how a
truncated supervisor was found at the M3 boundary.

**`input_tokens` and `output_tokens` include the supervisor's call from M3 onward**, not just
the responders'. The M2 flat-cost query below still reads correctly - the supervisor bills only
on a tick that already fanned out - but a per-work-order average taken off this line is now
slightly high, and `logs/agent.jsonl` is where to go for the split.

**`chaos_fired` was planned for M5 and landed at M4**, with the loop, because wiring an injector
into a tick is cadence work. `run_tick` heals, injects, and fires animal events right after the
catalog (the plan needs the topology), and its own failure never fails the tick: a
`chaos_step_failed` warning and honest readings. `SweepResult.overlay_observed` is the other
half, the event ids whose sensor the sweep actually read, and it is what `chaos_missed` is
computed against.

**Written at tick end, always, including when the tick failed** (with `error` and
`failed_stage`). A tick that produces no line is indistinguishable from a dead loop, and
that distinction is the entire product at 2am.

`failed_stage` is set **before** each stage is attempted. A line reading
`failed_stage: null` beside an error says a tick died without saying where, which is the
one question the field exists to answer. Caught during M0 exactly this way.

One line per tick means the cost curve is one `jq` away, which is the only way anyone
notices it stop being flat.

### `logs/agent.jsonl` - the instrument, one line per model call

```jsonc
{ "ts":"…","run_id":"…","tick":3,"agent":"water_feed","tier":2,
  "provider":"bedrock","model":"us.anthropic.claude-opus-5","reasoning_effort":"none",
  "max_tokens":2048,"tool_calls":1,"input_tokens":4816,"output_tokens":1102,
  "finish_reason":"tool_use","latency_ms":14095,
  "content_types":["tool_use"],"incident_key":"feed-bin-03:feed_low" }
```

That is a real M2 line. A real Tier-1 line, M7:

```jsonc
{ "ts":"…","run_id":"…","tick":1,"agent":"infrastructure","tier":1,
  "provider":"ollama","model":"gemma4:e4b","reasoning_effort":"none",
  "max_tokens":2048,"num_ctx":16384,"tool_calls":1,"input_tokens":3651,"output_tokens":443,
  "finish_reason":"stop","latency_ms":5982,
  "content_types":["json"],"incident_key":"coyote-draw-gate:sensor_offline" }
```

Tier 1 adds `num_ctx` and Tier 2 does not have one; `content_types` is `["json"]` when the
schema-constrained answer parsed and `["text"]` when it did not; `tool_calls` is 1 for a parsed
answer on either tier. From M10 an investigator turn is a line too, with `job="investigate"`, `turn`,
and `tool_calls` as the number of tools the model asked for on that turn (0 on its closing turn);
`latency_ms` is 0 on those lines because the graph, not `llm_client`, timed the call, and the whole
loop's wall clock is on the console `investigation` line. A line with no `job` is a work order or,
when `agent` is `supervisor`, the fused report. The required fields are the ones in `log_agent_call`'s signature and
everything else is per-call context. An escalation is two lines with the same `incident_key`, one
per tier, and the console stream has `tier1_escalated` between them naming the reason. `content_types` is the block types the response actually contained, which is how
"answered with no tool call" reads differently from "never answered."

**`finish_reason` is the most valuable field in this whole scheme and it is required.**
Truncation means the model never got to answer; completion means it answered and answered
badly. One is a config bug, one is a model-selection decision, and **in the response
text they look identical**. Telling them apart cost a real investigation once.

**The vocabulary is per provider**, so any query over this field names the healthy **set** (`src/models/CLAUDE.md`, cookbook #10).

**Written immediately on return, before validation runs**, so a response that fails a
check still leaves a receipt of what was actually returned rather than vanishing into a
retry.

### `logs/compare.jsonl` - the measurement, M7, only when `TIER_COMPARE=1`

One line per packet judged by both tiers on the identical page: `incident_key`, `agent`,
`severity`, `escalation` (`""` when the local order stood and Opus was a shadow, else the reason),
`page` (what code put on the page and a grader checks against: `sensor_id`, `last_value`,
`siblings`, `siblings_flagged` per triage, `head_count`, `citable_rules`, `sop`), and `tier1` /
`tier2` (headline, assessment, actions, rules, unknowns, `insufficient_information`,
`proposed_write`, status, violations, receipt). **It carries model prose on purpose**: two work
orders per line, already stored in `sw_ops`, because grading them is the whole point of the file. It
is not one of the three operational streams, it is empty unless the knob is on, and the knob SPENDS.
The M7 grading of six pairs is pinned as `docs/transcripts/m7-compare-transcript.md`. From M10 a line
may carry `job="shift_report"` (`incident_key` is `tick:N`, `page` is the keys, worlds, order count,
and page length, `tier1` / `tier2` are the two reports) and a work-order line carries `investigation`
(the four receipt fields) when the loop ran before the pair was written.

### `logs/audit.jsonl` - the receipt, one line per side effect

```jsonc
{ "ts":"…","run_id":"…","tick":42,"audit_id":"2f7c…","phase":"proposed",
  "tool":"restock_feed","args":{"sku":"alkali-flat-water-2","quantity":16.7},
  "proposed_by":"water_feed","incident_key":"alkali-flat-water:water_low" }
{ "ts":"…","run_id":"…","tick":0,"audit_id":"2f7c…","phase":"decided",
  "decision":"approve","decided_by":"scooter","result":"written","latency_to_decision_ms":73991,
  "tool":"restock_feed","incident_key":"alkali-flat-water:water_low","reason":"","upstream":"{…first 200 chars of the upstream body…}" }
```

**Confirmed against real lines on 2026-09-11**, M6, with two corrections to what this file used
to promise. The `decided` line repeats `tool` and `incident_key`, because it is written by a
different process (the CLI) hours later and a reader grepping one id should not have to join two
lines to know what was decided; and it carries `reason` (required on a reject, empty on an
approve) and `upstream` (the first 200 characters of what the ranch answered, or the exception).
Its `tick` is the deciding process's tick, which for the CLI is `0`; the proposing tick is on the
`proposed` line. `result` is a short code, not a status number: `written`,
`upstream_error_<category>`, `transport_<ExceptionName>`, `not_executed` on a reject, and
`checkpointer_unavailable` on a `dropped`.

`decision` takes five values across two writers. From the gate: `approve` and `reject` (a human,
`decided_by` is their name), and `dropped` (`decided_by:"gate"`, the pause could not be persisted
after the `proposed` line was written; the incident is held and the write is proposed again).
From chaos, below: `blocked` and `auto_allowed`.

**Two lines per side effect, correlated by `audit_id`.** Deliberately two rather than
one: a `proposed` with no matching `decided` is a pause nobody ever answered, and that
should read as a dangling record you can grep for, not as an absence you have to already
suspect. From M6 that dangling record is the **normal** shape of an open pause: the gate writes
`proposed` when the work order proposes and `decided` only when a person answers, and
`python -m src.agent.gate list` is how a person finds the ones still waiting. A duplicate
proposal (same incident, same tool, already waiting) writes no line at all, because it is not a
new side effect.

This is the file that makes "we watch your ranch" a defensible claim rather than a
pitch.

**A blocked side effect writes both lines too.** When `CHAOS_ALLOW_WRITES=0` stops an animal
mutation, chaos still emits `proposed` and then `decided` with `decision:"blocked"`,
`decided_by:"chaos_guard"`, `result:"writes_disabled"`. The receipt that nothing was mutated
is worth exactly as much as the receipt that something was, and it keeps the
`uniq -c | awk '$1!=2'` query below meaningful: a guard that logged only the refusal would
show up in that query as a dangling proposal.

**`tool` is the MCP tool name when one was used, and the HTTP route when one was not.**
Chaos writes go direct over REST rather than through MCP, so its lines read
`"tool":"PATCH /animals/:animalId"`. Naming a tool it never called would be a tidier field
and a false receipt.

### From M8, two of the three streams and the receipt are also rows

`python main.py --api` is its own process, on its own box if need be, and it reads `sw_ops` and
nothing else: never a log file, because a file is what a second process cannot see. So the loop
writes three things to the ledger **beside** the log line, and the API is a projection of the ledger.

| Table | Written by | Holds | Read by |
| --- | --- | --- | --- |
| `sw_ops.ticks` | `executor._record_tick`, after `log_tick`, on every tick including a failed one and the loop's own line for a tick that raised outside its guard | `run_id`, `tick`, `at`, `store`, `duration_ms`, `cost_usd`, `error`, `failed_stage` as columns, and the **whole tick line as `fields` jsonb**. Unique on `(run_id, tick)`. `id` is the SSE cursor | `GET /ops/stream` |
| `sw_ops.shift_reports` | the same call, when the tick produced a report (a `--no-spend` tick does, assembled in code) | the `ShiftReport` fields plus `incident_keys`, the keys the page was handed, so `linked` stays checkable | `GET /ops/report` |
| `sw_ops.audit_receipts` | `gate.propose` (the `proposed` row, then the file line) and `gate._execute` (the `decided` row, then the line), through the checkpointer's own connection | the two halves of a receipt, primary key `(audit_id, phase)` | nothing yet; the constraint is the point |

Two rules about the order. **The tick line is written first and the row second**, because the line is
the heartbeat and must not depend on the ledger being up: a row that fails is `tick_row_failed` on the
console (a warning naming the API as the thing missing the tick) and never a failed tick. **The receipt
row is written first and the file line second**, because the table is the record and the file is the
projection: `docs/open-issues.md` #10 closed here. A `proposed` row that cannot be written takes the same
`dropped` path as a checkpointer failure; a `decided` row that cannot be written is `audit_receipt_failed`
(with `receipt_phase`, not `phase`, so the audit rail does not count the console line as a receipt), and
the decision still finishes and still reaches the file, because by then the write on the ranch may
already have happened. The primary key makes "every `audit_id` appears exactly twice" a constraint the
database enforces, and a rail inserts a third to prove it is refused.

**What stays file-only.** The chaos guard's `blocked` / `auto_allowed` pairs below have no row
(`docs/open-issues.md` #20); the tick's `fields` jsonb is the line as written, so a field added to the line
appears in the row with no migration.

### `chaos_*` on the console stream - the overlay says so out loud

**There are three JSONL files and there is no fourth.** `chaos_*` lines go to the console
stream, like every other application event, and only the animal write path reaches
`audit.jsonl`. An overlay is a development and demo instrument rather than a side effect on
the ranch, and a fourth rotating file for it would be a stream nobody greps.

The events, and what each one is for:

| Event | Says |
| --- | --- |
| `chaos_injected` | one event armed, with `scenario`, `mode`, `target_id`, `group_id`, `expires_at` |
| `chaos_expired` | a TTL ran out and a sensor is honest again. This is what produces `resolved` incidents |
| `chaos_overlay_applied` | **carries `honest_value` beside `faked_value`.** The single most useful line in the stream |
| `chaos_overlay_unavailable` | the store could not be read, so the sweep stayed truthful. A degraded overlay must never be a failed tick |
| `chaos_at_ceiling` | `CHAOS_MAX_ACTIVE` reached, nothing new armed |
| `chaos_group_deferred` | a correlated group would have crossed the ceiling, so **none** of it fired. Half a storm front is a worse fixture than no storm front |
| `chaos_insert_deduplicated` | `offered` vs `inserted`. A replayed seed or an already-active fault on the same target, both skips rather than errors. See `docs/cookbook.md` #19 |
| `chaos_write_blocked` | a guard refused an animal mutation, with the reason |
| `chaos_write_partial` | the `PATCH` landed and the observation did not. **Not retried**, because the status is already changed and a retry would double-write the observation |
| `chaos_animal_written` | a real mutation went through, which only happens with `CHAOS_ALLOW_WRITES=1` |
| `chaos_unknown_fault_mode`, `chaos_fault_unusable`, `chaos_no_target_for_type`, `chaos_no_cohort_target` | four `warn_once` lines for a catalog that asks for something the ranch cannot supply. A scenario that silently does nothing is the failure mode here, same disease as an unrecognized sensor type reading as nominal |
| `chaos_cli_catalog` | the CLI resolved the live topology, with `source` and `sensors` |

`chaos_overlay_applied` is the line that makes a faulted demo legible instead of
mysterious. Without `honest_value` beside `faked_value`, a reader of the log cannot tell a
ranch that is being lied to from a ranch that is actually broken, which is the same
indistinguishability triage is **supposed** to have and the operator is not.

Because the stream is the console, a query means redirecting a run rather than reading a
file, and three things about that are easy to get wrong. **`LOG_CONSOLE_PRETTY=0` is
required**, or the renderer emits aligned text and `jq` gets nothing it can parse. **The
event name is `msg`, not `event`**, in every mode: a processor renames structlog's
positional field once, before any renderer, so there is exactly one spelling. And **the
stream is not pure JSON** - a CLI prints its own human-readable summary to the same place,
and a traceback is not JSON either, so a `jq` filter over it has to tolerate lines that do
not parse:

```bash
LOG_CONSOLE_PRETTY=0 SW_OPS_TARGET=test CHAOS_ENABLED=1 python -m src.tools.chaos inject --tick 2 2>&1 | jq -Rrc 'fromjson? | select((.msg//"")|startswith("chaos_")) | [.msg,.scenario//"-",.target_id//"-"]|@tsv'
```

`-R` with `fromjson?` is what makes that tolerance work: without it, the first non-JSON line
kills the query with a parse error and the exit code blames the data. This exact command was
documented in its naive form first, and it failed on all three counts at once.

### Rotation differs by stream, and the difference is the point

| Stream | Rotation | Why |
| --- | --- | --- |
| `tick.jsonl` | 10 MB, 5 back | a diagnostic; oldest is discardable |
| `agent.jsonl` | 10 MB, 5 back | same |
| `audit.jsonl` | daily, **no size cap** | a receipt. A size cap on an audit trail means the trail ends exactly when the ranch got busiest. From M8 a projection of `sw_ops.audit_receipts`, so a rotation colliding with a decision from another process (`docs/open-issues.md` #10) loses at worst a projected line. |

### Never logged

`ANTHROPIC_API_KEY`, `DATABASE_URL`, `OPS_API_TOKEN`, an `Authorization` header, or any full prompt or response body. Secrets are
replaced with `[redacted]`; bulk bodies with `[omitted: set LOG_TRANSCRIPTS=1]` rather
than deleted, so a reader can tell "there was a prompt we chose not to store" from
"there was no prompt."

`LOG_TRANSCRIPTS=1` writes full bodies to `logs/transcripts/{run_id}/{tick}-{agent}-{incident_key}.json`,
one per stored work order (the page and the order), plus `{tick}-supervisor-tick-{n}.json` for a fused
shift report (the page and the report, so `linked` is readable after the tick). **Dead until M7A**: the
function existed from M0 and nothing called it, found the first time a herd order's prose was needed.
Off by default because prompts dwarf everything else on disk. Invaluable for exactly one
job: a finding that reads wrong and a log that cannot say why.

### Reading the logs is the real test of whether the instrument works

```bash
jq -r '[.tick,(.input_tokens//0),(.output_tokens//0),(.work_orders_shipped//0),(.escalated//0)]|@tsv' logs/tick.jsonl
jq -r '[.tick,(.tier//"-"),(.tier1_orders//0),(.escalations//0),((.escalation_reasons//[])|join(",")),(.cost_usd//0)]|@tsv' logs/tick.jsonl   # M7: who wrote the tick and why Opus was paid
jq -r 'select(.finish_reason | IN("stop","end_turn","tool_use","stop_sequence") | not)' logs/agent.jsonl
jq -r '.audit_id' logs/audit.jsonl | sort | uniq -c | awk '$1!=2'   # every id here must be in `python -m src.agent.gate list`
```

Cost flat on calm ticks, spiking only where an escalation is logged beside it. Anything
in the second query is a config bug, not a weak model. Anything in the third is a pause
nobody answered: from M6 that is a legitimate open pause **only if** the same id is in the gate
CLI's `list`, and anything else in that query is a decision path that skipped its log line
(`gate.unpaired_audit_ids` is the same check in code, and the rail in `tests/`).


The loop's own lines, in the main stream rather than `tick.jsonl`: `loop_start`, `loop_stopped`
(clean drain, exit 0), `loop_halted` (`reason=spend_ceiling`, exit 4), `loop_unrecoverable`
(exit 1), `loop_draining` on the first interrupt, `stop_requested` with the signal name,
`upstream_backoff` / `upstream_recovered` per upstream, `tick_overran`, `incidents_held`,
`held_rerouted`, and `chaos_event_missed`. M6 adds `held_restored` / `held_restore_failed` at loop
start and `held_not_persisted` when the column could not be written; and the gate's own:
`write_paused` (the one to watch for, with the audit id and the CLI hint), `write_decided`,
`write_proposal_duplicate`, `write_proposal_dropped`, `write_key_unknown`, `write_proposal_shape` /
`write_proposal_ungrounded` when a check fired, and `gate_unavailable` when the checkpointer could
not be opened at all.


`logs/*.jsonl` is gitignored; `logs/.gitkeep` is not. A captured run worth keeping goes
into `docs/` next to the finding it supports.

---

## Milestones

Each ends runnable and verifiable. **M0 through M9 need no containers and no new AWS**, just a venv, the already-live endpoints, an Anthropic key, and Vercel for the window. I stop and report at every boundary.

**M0 Skeleton, logging, and a live handshake.** The full tree above including the nested `CLAUDE.md` files and the `docs/` skeleton, plus `requirements.txt`, `config.py`, `logger.py` with all three streams wired, and `mcp_client.py` connected to the deployed Function URL over Streamable HTTP. _Verify:_ `python main.py --handshake` prints exactly **19** tool names, `read_resource("ranch://sensors/map")` returns ~160 sensors across 32 locations, and the handshake itself writes a well-formed line to `tick.jsonl`. Logging lands first on purpose: every rung in Phase II was only legible because the instrument was built before the thing it measured.

**M1 The free pass.** `tools/sensors.py`, `tools/triage.py`, `agent/memory.py`, Alembic migrations for `sw_ops`. Zero tokens spent. _Verify:_ three consecutive sweeps; sweep 2 reports the same fault as `ongoing` not `opened`; `alkali-flat-water` and `east-allotment-fence` appear every run because they carry fixed profiles; a planted unknown sensor type trips `warn_once` instead of vanishing.

> `asyncpg` against Supabase's pooler needs `statement_cache_size=0`. Same disease as `prepare: false` on postgres.js, different driver. Set it once in the engine factory with the reason in a comment so nobody cleans it up.

**M2 One agent, Opus only.** `tools/evidence.py` assembles the packet in code, then a single agent reads its SOP file and returns a Pydantic `Finding` (severity echoed not authored, work order, citations), invoked only on newly-opened incidents. Deliberately before any fan-out, and deliberately **Tier 2 only** so there is a known-good baseline to measure local models against later. _Verify:_ token cost flat across three sweeps while incident count grows; every work order names a real sensor and quotes its real reading; `agent.jsonl` carries `finish_reason` on every call; one narrow pytest grades the **reason text** for grounding facts, not just the severity label.

**M3 The four responders.** Supervisor, allowlists, explicit briefs, bounded fan-out, `WorkOrder` as the handoff contract, cross-domain synthesis into one shift report. **The one thing worth proving rather than porting:** run a sub-agent once with no brief, capture it flailing, then pass the brief and capture it working, committed side by side. Sub-agents inherit nothing, and that fact is what this whole architecture rests on. _Verify:_ a test asserts each agent's exact tool count and that no agent can name a tool outside its set. _What actually happened:_ it did not flail. Both answers pass every rail with zero violations, and the unbriefed one writes one action instead of five and hands nothing to a named neighbour. The pair is in `docs/transcripts/no-brief-transcript.md` and `docs/transcripts/with-brief-transcript.md`; the finding is that the rails cannot detect a missing brief. `Finding` above was wrong: it is triage's type, and what a sub-agent hands up is a `WorkOrder`.

**M4 The continuous loop.** `executor.py`: tick cadence from config, graceful shutdown, per-tick structured log line, exponential backoff on upstream failure, and a tick that survives one sub-agent raising. _Verify:_ run for 30 minutes unattended; every tick logged; kill an upstream by pointing it at a bad URL mid-run and watch it back off and recover rather than die.

**M5 Chaos, the fifth agent.** `tools/chaos.py`, the overlay, the real-write path, the scenario catalog, TTL healing. _Verify:_ same `CHAOS_SEED` replays an identical event sequence across two runs; a storm front produces one fused work order rather than two unrelated ones; a healed sensor shows up as `resolved`; with `CHAOS_ALLOW_WRITES=0` no animal is ever mutated.

**M6 Gate and validation.** LangGraph Postgres checkpointer so a pause outlives the process, `interrupt()` on the write tools (**eight, not four**: the four in a responder's slice plus the four animal-placement writes that arrive with `chaos` at M5; all eight are in `WRITE_TOOLS` from M3 and `assert_callable` already refuses them), and three level-3 return-path checks: **shape**, **key**, **grounding**. The key match is on a real incident key, never on a model-written index. _Verify:_ let a tick pause on a `create_observation`, kill the process, restart, resume with reject and then approve; a planted-bad-response suite asserts _which_ check fires.

**M7 Model routing, one job at a time.** `models/routing.py` and the escalation predicate. Move the cheapest job to local first (chaos observation prose), confirm the rails hold, then the work-order write, then packet-judging. Each move gets a row in `docs/model-routing.md` with the before and after numbers. **The all-clear rail goes in before the first job moves down**, not after. _Verify:_ a calm tick costs $0.00 and logs `tier: 1`; a critical incident escalates and logs `escalation_reasons: ["critical"]`; a planted local-model all-clear on a code-flagged incident is rejected and escalated rather than believed.

**M7A The herd sweep, the coyote gap.** Added at the M7 boundary rather than planned, because nothing in the tick read the Care API: the free pass was a sensor sweep, triage a sensor truth table, and `herd_health` cannot read a sensor, so a dead cow written by chaos was invisible to the monitor. **No new endpoint and no MCP change.** `GET /animals` on the Farm API already carries the `status` field chaos patches, and observations and care tasks are on the Care API. `tools/herd.py` is a second free sweep, direct over httpx like `sensors.py` and under the same three rules: herd catalog once, care tasks once, observations bounded by `SWEEP_CONCURRENCY` and fetched only for animals whose state changed, inside a 24-hour window so the ranch's history does not open an incident per old note; errors returned as data; an empty herd catalog is a failed stage, not an empty herd; the stage returns the animals that actually answered. Animal categories land in `triage.py`, in code: `deceased` critical, `inactive` warning with `sold` ruled out explicitly because one of those is a ranch running normally, a `high` observation critical for `injury` / `mobility` and warning otherwise, `care_overdue` warning. Deceased is critical on purpose: under M7's predicate it escalates to Tier 2, which is where a dead cow belongs. Migration `0006` renames `incidents.sensor_id` / `sensor_type` to `subject_id` / `subject_type` (`sensor` rows and `animal` rows), the key stays `subject:category` (`cow-0903:deceased`), and `reconcile` takes the *subjects* that answered so a Care API outage resolves no animal. `ROUTES` gains the animal categories for `herd_health`, and STATE.md decision 5 is rewritten: `herd_health` owns animals and nothing else. An evidence packet for a cow: the record, its pasture, its recent observations, its open care tasks, its herd-mates in that pasture, and **no sensor readings**, so the dead cow and the dry tank stay in two packets and fusion stays the supervisor's job. `knowledge_base/herd.md`, derived from the canon and nothing else, because the citation rail needs it before the first packet ships. _Read over the wire before the sweep is written:_ the herd count, and whether observations list ranch-wide with a since filter or only per animal; that decides the request bill, not whether an endpoint is missing. _Verify:_ one paid run on the test ledger with `CHAOS_ENABLED=1 CHAOS_ALLOW_WRITES=1`, seed advanced to `coyote_kill` on the cohort: the next tick opens `cow-090x:deceased`, `herd_health` names the animal and quotes the observation, proposes `create_observation`, and the gate pauses for real, which closes `docs/open-issues.md` #1, #11, and #15; `chaos restore` puts the cohort back to `active` and the incident resolves; then `storm_front` fuses into one report with `linked` naming both worlds, closing #2.

**M8 The read API.** `api/routes.py`: `/ops/incidents`, `/ops/report`, `/ops/stream` (SSE), `/ops/gate` for approve/reject, `/health`. Same envelope conventions as the ranch APIs so the whole system reads consistently.

**M9 The window.** Next.js on Vercel: incident feed and ranch map against `/ops/*`. Light mode. Sensor coordinates are already in the catalog, so the map needs no new backend work. This replaces the `agent-lab-ui/` dashboard and the half-built `demo-site/` from MCP-Farm, neither of which comes over: both read the ranch directly, and this one reads `sw_ops` through the M8 API and shows what the agents decided rather than what the sensors said. **M9 is the last M phase**, so its close also carries what the old M10 held: `README.md` brought current, `docs/cookbook.md` ordered by the pain rather than the technique, and `docs/journey.md`'s final pass, which is a pass and not a reconstruction because it was written at every boundary.

**FUTURE-1 Dockerize. Deferred, renamed from M10 on 2026-09-11.** Not part of the build; picked up if and when issue #5 (where the loop runs) lands on a host that wants a container. When it does: a `Dockerfile` for the orchestrator, a second for the API, and `docker-compose.yml` wiring them plus an optional local Postgres for offline work. The same image is what a Lambda container or a small always-on box would run, so this is the last step and also the first step of whatever hosting you pick. The doc close that used to live here moved to M9.

---

## What does not come over

Named so nothing gets quietly resurrected:

- **The APIs and the MCP server.** They stay on Lambda; MCP-Farm goes read-only. If a tool turns out to be genuinely missing, that is a scoped change to the old repo and a conversation, not a drive-by.
- The 23 `agent-lab/*.mjs` rung scripts. The findings are the asset; the scripts already spent themselves earning them.
- `agent-lab-ui/` and `demo-site/`. M9's window absorbs both.
- `docs/prd.md`. Stale as a system description by its own admission. `docs/sweetwater-ranch.md` comes over as canon.

## Verification, end to end

1. `pytest`, `ruff check`, `mypy --strict` all green.
2. `python main.py` in a venv, then watch the log for 30 minutes. Ticks land, chaos fires and heals, incidents open and resolve. Only at FUTURE-1, if it happens, does this become `docker compose up`, and it must behave identically.
3. Read a shift report by eye. Right sensing world, real sensor, real reading, and would a hand on shift know what to do.
4. Human gate by hand: let a tick want a care write, watch it block, kill the process, restart, reject, then approve.
5. Replay determinism: same `CHAOS_SEED`, two runs, identical event sequence.
6. Read the logs, which is the real test of whether the instrument works: the four queries under "Reading the logs" in the Logging section above. Cost flat on calm ticks; nothing outside the healthy `finish_reason` set; every `audit_id` twice or once while its pause is open.
7. Load the Vercel URL and watch three ticks land without a refresh.

## Open items

Tracked in `docs/open-issues.md` (#5 and "Multi-tenancy"). The two the plan named:

- **Where this runs in production.** ECS is off the table. You build and run it in a venv, decide the host, and dockerize (FUTURE-1) only if the host wants a container. Lambda container image on a short EventBridge schedule is the cheap answer for a tick loop; a small always-on box is the honest answer for "constantly running." Worth deciding once M4 exists and you can see how long a tick actually takes.
- **Multi-tenancy.** "Stand up the next ranch in a morning" implies per-client isolation of `sw_ops`. Not in V1.
