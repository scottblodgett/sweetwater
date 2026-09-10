# Sweetwater: the agentic layer

## Context

MCP-Farm solved a good business problem and then buried it under scaffolding. The problem is worth keeping; the archaeology is not.

The product is small and already works: **~6,300 lines of TypeScript** across five services, 42 endpoints, 19 MCP tools, all deployed on Lambda with a live Function URL. The sprawl is everything around it: **4,400 lines** of one-off rung scripts in `agent-lab/`, a separate `agent-lab-ui/` dashboard, a half-built `demo-site/`, a Terraform repo cloned inside the working tree and gitignored, and **~5,000 lines of docs** describing all of it. Four lanes, three deploy stories, twelve rungs of scaffolding still standing after the building went up.

**The four APIs and the MCP server stay exactly where they are, and MCP-Farm goes read-only.** They are a working external product. Rebuilding them in Python is translation work that teaches nothing, and it was the bulk of the previous draft.

What gets built is the part that does not exist yet: **one orchestrator running continuously, driving five sub-agents** (water, health, infrastructure, compliance, and chaos) against the live deployed ranch, in Python, in the standard structure.

### Decisions locked

|                |                                                                                                                                                              |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Repo           | `scott-jasper/sweetwater`, local `C:\temp\sweetwater`. New repo; MCP-Farm untouched.                                                                         |
| Structure      | The canonical layout from your diagram, mapped 1:1. `requirements.txt`, `src/{agent,tools,models,prompts,utils,api}`, `tests/`, `data/`, `logs/`, `main.py`. |
| Language       | Python 3.12. Next.js for the window only.                                                                                                                    |
| Agents         | Orchestrator + **five**: `water_feed`, `herd_health`, `infrastructure`, `compliance`, `chaos`.                                                               |
| Framework      | **LangGraph Python**, hand-wired supervisor + `create_react_agent` workers.                                                                                  |
| Tools          | The **19 existing tools** from the deployed MCP Function URL, per-agent allowlist enforced in code.                                                          |
| Cadence        | Continuously running tick loop in `src/agent/executor.py`, driven by `main.py`.                                                                              |
| Models         | **Local (`gemma4:e4b` via Ollama) by default, Opus on escalation.** Build Opus-only, then move jobs down with a rail.                                        |
| Logging        | structlog, JSON Lines, three streams: `tick` / `agent` / `audit`. Wired at M0, not bolted on.                                                                |
| Agent state    | Existing Supabase project, one **new** schema `sw_ops`. Nothing else touches it.                                                                             |
| How you run it | **Local venv first, Docker last.** `python main.py` against the live APIs and Supabase. No containers needed until M9.                                       |

---

### Run local first, dockerize last

Nothing in M0 through M8 needs a container. The upstreams are already deployed, so there is no local API stack to stand up, and `sw_ops` lives in Supabase, so there is no local database to run either.

```bash
python -m venv .venv && .venv/Scripts/activate && pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env      # fill in MCP_URL, the four API urls, DATABASE_URL, ANTHROPIC_API_KEY
python main.py            # the tick loop starts talking to the live ranch
```

`docker-compose.yml` sits in the tree from M0 as a placeholder, and gets filled in at **M9** once the thing works. Dockerizing a moving target is how you end up debugging a container when the bug is in your prompt.

**One exception:** the `sw_ops` store tests need a real Postgres and must never point at Supabase, same rule as `farm_systems_test`. They use your existing local Postgres install with a `sw_ops_test` schema. Still no Docker.

## Structure, mapped to your diagram

```
sweetwater/
├── README.md
├── CLAUDE.md                     lean: orientation, the one rule, commands
├── requirements.txt              runtime pins
├── requirements-dev.txt          pytest, ruff, mypy
├── .env / .env.example           MCP_URL, *_API urls, DATABASE_URL, ANTHROPIC_API_KEY, OLLAMA_*, CHAOS_*, LOG_*
├── .gitignore
├── docker-compose.yml            placeholder until M9; you do not need it to build this
├── .claude/rules/                path-globbed rules for anything too long for a CLAUDE.md
├── src/
│   ├── agent/
│   │   ├── CLAUDE.md             the graph, the tick contract, the escalation tiers
│   │   ├── agent.py              the LangGraph supervisor + the five worker factories
│   │   ├── executor.py           THE CONTINUOUS LOOP: tick, cadence, backoff, shutdown
│   │   ├── state.py              RanchState (LangGraph), Finding, Incident dataclasses
│   │   └── memory.py             sw_ops persistence: incidents, chaos events, checkpointer
│   ├── tools/
│   │   ├── CLAUDE.md             MCP is frozen upstream; allowlist + severity-ownership rules
│   │   ├── mcp_client.py         connect to the deployed Function URL; read ranch://sensors/map
│   │   ├── allowlists.py         the five tool slices, enforced in code
│   │   ├── sensors.py            the free-pass sweep (direct httpx, bounded concurrency)
│   │   ├── evidence.py           assemble the packet in CODE (see Model routing)
│   │   ├── triage.py             per-type thresholds; severity is CODE's, not the model's
│   │   └── chaos.py              the fifth agent's hands: inject, heal, expire
│   ├── models/
│   │   ├── CLAUDE.md             the Ollama gotchas; when thinking may be turned off
│   │   ├── llm_client.py         provider registry + per-call reasoning_effort + usage receipts
│   │   ├── routing.py            job -> tier -> model, and the escalation predicate
│   │   └── embeddings.py         SOP retrieval seam (stub in V1, see note)
│   ├── prompts/
│   │   ├── system_prompts.py     shared rules: severity is not yours, cite your SOP, no invented premises
│   │   └── agent_prompts.py      the five briefs (a sub-agent inherits nothing)
│   ├── utils/
│   │   ├── helpers.py
│   │   ├── logger.py             structlog: three streams, JSON to file, pretty to console
│   │   └── config.py             every upstream from env, no hardcoded endpoints
│   └── api/
│       ├── CLAUDE.md             envelope + error conventions
│       ├── routes.py             FastAPI: /ops/incidents, /ops/report, /ops/stream, /ops/gate
│       └── schemas.py            Pydantic: Finding, Incident, ShiftReport, GateDecision, ChaosEvent
├── tests/
│   ├── CLAUDE.md                 never Supabase; sw_ops_test schema; what each rail proves
│   ├── test_agent.py             allowlists counted, routing, no-brief flail, gate resume
│   ├── test_tools.py             triage truth table, sweep concurrency, chaos determinism
│   └── test_api.py               envelope shape, gate endpoints
├── data/
│   ├── examples.json             chaos scenario catalog + golden fixtures
│   └── knowledge_base/           the SOPs, one file per sensing world
├── docs/
│   ├── sweetwater-ranch.md       the scenario canon, copied from MCP-Farm
│   ├── architecture.md           the diagram, the free/expensive split, the tiers
│   ├── model-routing.md          the tier table + the measurement log (what moved down, when, proof)
│   ├── logging.md                the schema for the three log streams
│   ├── cookbook.md               the lessons, as prose, ordered by the pain
│   ├── JOURNEY.md                what actually happened, and where it diverged
│   └── decisions/                numbered ADRs, short
├── logs/
│   ├── .gitkeep                  *.jsonl gitignored
│   ├── tick.jsonl                one line per tick        (the heartbeat)
│   ├── agent.jsonl               one line per model call  (the instrument)
│   └── audit.jsonl               one line per side effect (the receipt)
└── main.py                       `python main.py` runs the loop; `--api`, `--handshake`, `--once`
```

### Nested CLAUDE.md, and why

The root file in MCP-Farm grew to 644 lines before it got broken up, and the fix that worked was path-scoped rules plus directory-level files. Start there instead of arriving there.

| File                   | Carries                                                                                                                                                                        |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `CLAUDE.md` (root)     | What this is, the one rule (**report back after 5 minutes or when you find work I did not ask for**), the commands, and a signpost to each nested file. Target under 80 lines. |
| `src/agent/CLAUDE.md`  | The graph shape, `RanchState` fields, the tick contract (what a tick must always do even when it fails), the escalation predicate.                                             |
| `src/tools/CLAUDE.md`  | **The upstream is frozen.** Allowlists are code not prompt. Severity belongs to `triage.py`. Chaos's two injection paths and their guards.                                     |
| `src/models/CLAUDE.md` | The Ollama gotchas, `reasoning_effort` as an explicit per-call argument, and the one rule about when thinking may be off.                                                      |
| `src/api/CLAUDE.md`    | The `{data, meta}` envelope and the error shape, matching the ranch APIs.                                                                                                      |
| `tests/CLAUDE.md`      | Never point tests at Supabase. `sw_ops_test` schema. What each rail proves, so nobody "fixes" a rail by loosening it.                                                          |
| `.claude/rules/*.md`   | Anything long enough to bloat a directory file, with a `paths:` glob.                                                                                                          |

> **`models/embeddings.py` is a seam, not a feature, in V1.** Each sensing world's SOP set is a handful of rules. Loading a whole SOP file into the agent's prompt beats retrieving over it, and pretending otherwise is the cleverer option rather than the simpler one. The file exists so retrieval has an obvious home when the corpus grows past a prompt.

---

## Architecture

```
                    main.py
                       │
        ┌──────────────▼─────────────────────────────────────────┐
        │  executor.py    CONTINUOUS TICK LOOP                    │
        │                                                         │
        │  every tick:   chaos maybe-fires ──┐                     │
        │                sweep ~160 sensors  │  free, 0 tokens     │
        │                triage (code)       │  free, 0 tokens     │
        │                reconcile sw_ops    │  free, 0 tokens     │
        │                route NEW incidents │  free, 0 tokens     │
        │                fan out ────────────┼──┐   ← only spend   │
        │                synthesize          │  │                  │
        │                gate on writes      │  │                  │
        └────────────────────────────────────┼──┼──────────────────┘
                                             │  │ asyncio.gather
   ┌──────────┬───────────┬──────────────┬───┘  │
 chaos     water_feed  herd_health  infrastructure  compliance
   │          └───────────┴──────────────┴───────────┘
   │                          │ MCP Streamable HTTP
   │              ┌───────────▼────────────┐
   │              │  DEPLOYED MCP SERVER   │  Lambda Function URL
   │              │  19 tools + ranch://map │  (frozen, unchanged)
   │              └───────────┬────────────┘
   │                 Farm / Feed / Sensor / Care APIs
   │                          ▲
   └──────────────────────────┘  chaos writes real animal events here;
                                 sensor faults go to the sw_ops overlay
```

### Steps that cost nothing, and the one that does

160 sensors are far too many to hand a model every tick. The free pass narrows the ranch to what is actually wrong; only newly-opened incidents reach an LLM.

- **`GET /sensors?limit=500` once** for the catalog (id, type, location, coordinates, status). That endpoint's query schema is pagination only, no `type` filter, so filtering happens in Python. The map-not-paging pattern arrives by necessity here rather than by choice.
- **Live reads are one GET per sensor** (`GET /sensors/:id` synthesizes fresh each call). ~160 requests per tick, bounded by an `asyncio.Semaphore(20)`. Unbounded fan-out at 160 invites a wall of Lambda cold starts and reads as a load test.
- **`triage.py` owns severity.** Per-type thresholds in code, a warning tier between nominal and critical for every critical-capable type, and a `warn_once` guard on any unrecognized sensor type so a new type can never silently fall through a default branch.
- **`memory.py` reconciles into `sw_ops`**, keyed on sensor plus category, so a persisting fault is `ongoing` and never re-alarmed.

### The five agents and their tool slices

The deployed server exposes 19 flat tool names (no namespaces), so each allowlist is an explicit name set. `create_react_agent` receives a **filtered** list; a test counts each set rather than asserting it.

| Agent            | Tools                                                                                                                    | Owns                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| `water_feed`     | `list_sensors` `read_sensor` `get_sensor_readings` `list_feed_products` `list_inventory` `consume_feed`_ `restock_feed`_ | nobody dies of thirst or hunger; reserves before a storm  |
| `herd_health`    | `list_animals` `get_animal` `list_observations` `create_observation`_ `list_care_tasks` `update_care_task`_              | sick, down, dead, or missing animals; the care write path |
| `infrastructure` | `list_sensors` `read_sensor` `get_sensor_readings` `list_pastures` `list_shelters`                                       | containment, power, fuel, no spill no fine                |
| `compliance`     | `list_sensors` `read_sensor` `get_sensor_readings` `list_pastures` `list_animals`                                        | AUM stocking, habitat, keep the payments                  |
| `chaos`          | none of the above. Its own hands in `tools/chaos.py`                                                                     | breaks the ranch on purpose                               |

`*` = write, gated. All four responders read `ranch://sensors/map` once instead of paging.

**The line worth defending: `herd_health` cannot read a sensor, and nothing but `water_feed` can touch feed.** That is what makes the supervisor real instead of decorative. Water-feed reports the Alkali Flat tank dry. Herd-health reports three cows in that pasture with no observation in eight days. Neither can see the other's evidence, and the supervisor is the only thing that can fuse them into one work order. That is "the world is too wide for one prompt" earning rent instead of being asserted in a doc.

Three agents legitimately share the sensor read tools, and being honest about that: the isolation that matters is the brief, the SOP set, and which sensor types each agent is pointed at, not tool-name exclusivity. Carving that up would mean editing a frozen server.

---

## The chaos agent

The antagonist. Its job is to make the ranch a genuinely hard place so the other four have real work, and so the demo is alive rather than a slideshow.

### Two injection paths, and the asymmetry is forced

**Sensor faults go to an overlay in `sw_ops`.** The deployed Sensor API is stateless, DB-free, and synthesizes every reading in code, so there is nowhere to write a fault into it. Its own fault injector (`chaos.ts`, from II.12) **deliberately refuses to register when `AWS_LAMBDA_FUNCTION_NAME` is set**, specifically so deployed prod can never be faulted. That guard is correct and stays. So: chaos writes rows to `sw_ops.chaos_events`, and `tools/sensors.py` applies them on top of the honest live read before triage ever sees it. **The deployed API stays truthful; the new repo owns the lie, in one place, under test.**

**Animal events are written for real** through `PATCH /animals/:animalId` and `POST /animals/:animalId/observations`. A coyote kill is a status change plus an observation a human would actually read, and `herd_health` discovers it through its real tools with no overlay at all. Guarded by `CHAOS_ALLOW_WRITES` and confined to a configurable animal-id cohort so the rest of the herd stays pristine for other demos.

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

- **Seeded.** A `random.Random(CHAOS_SEED)` picks scenario, targets, and timing, so a given seed replays the same demo. Chaos that cannot be reproduced is a flake, not a fixture, and that lesson was paid for once already.
- **Every event has a TTL.** Expiry restores the sensor, which is what produces `resolved` incidents and exercises the reconcile logic. Without healing, everything is broken an hour in and the feed goes quiet.
- **The model is not in the load-bearing path.** The seeded PRNG picks _what breaks_; an optional LLM pass authors the observation prose a human reads. Code owns anything a machine consumes, the model owns what a human judges. Same ownership rule that settled the severity question.

> **Design call flagged for override:** you asked for chaos as the fifth agent, so it is a peer node in the graph and appears as one. But its decision core is deterministic code, not a model, for the reproducibility reason above. If you want chaos genuinely model-driven (it invents scenarios you did not write down), say so and I will make the PRNG the fallback instead of the driver.

---

## Model routing: local by default, Opus where it earns it

You asked for my thoughts, so here they are rather than a shrug.

### The rule you already own

II.6b settled this and the sentence is worth quoting exactly: **turning thinking off is free exactly when the model is not the one CLASSIFYING.** `demo-site` could run gemma4:e4b with reasoning off because `triage.mjs` had already decided severity in code and the model only wrote prose a human reads. II.6 handed the model the verdict, and thinking off took it from **94/97 correct to 2/100**, fabricating justifications for labels it had already picked.

The good news is structural: **`triage.py` owns severity in this design already.** So no sub-agent is classifying severity, and the expensive-model requirement mostly evaporates. What is left is one job that genuinely needs a strong actor, and it is not the one you would guess.

### The job that actually breaks local models is tool-driving, not writing

II.11's numbers are the ones to design around. At full context budget, qwen3.5:9b on the wander path made **16 `list_sensors` calls, zero `read_sensor` calls, and never answered**. On the map path it read 8 of 28 water sensors and returned a **confident false all-clear** where Opus found 4 tanks below the floor. Meanwhile gemma4:e4b, handed an assembled set of incidents and asked to write advisories, went 5 sweeps / 160 sensors / 72 incidents / **0 templates, 0 retries, 0 check failures**, free, ~11s a call.

So: **local models write well and navigate badly.** A false all-clear is the single worst output a ranch monitor can produce, and open-ended tool loops are how you get one.

### Which means the cheap path should be a cheaper ARCHITECTURE, not just a cheaper model

The free pass already knows exactly which sensor is bad and has the ranch map in hand. So instead of telling a sub-agent "go find out what's wrong," `tools/evidence.py` assembles the packet in code: the incident, that sensor's recent history, its sibling sensors at the same location, the animals in that pasture, and the relevant SOP. The model gets one call and no tool loop. It is not navigating, it is judging a page.

That is cheaper, faster, more reliable, _and_ it is the version a local model is demonstrably good at. Three wins from one decision, which is usually a sign the decision is right.

### Two tiers, and an escalation predicate

| Tier              | Model                           | Jobs                                                                                                                                         | Why                                                                                                     |
| ----------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **1, default**    | local (`gemma4:e4b` via Ollama) | judge an assembled evidence packet; write the work order; chaos observation prose; shift-report assembly                                     | Nothing here classifies. Code already ranked severity. A human reads and judges the output. Free.       |
| **2, escalation** | Opus                            | real tool-driving investigation; cross-domain fusion when 2+ sensing worlds are hit in one tick; anything proposing a write through the gate | These decide what is _true_, or mutate a real ranch. Both are classification in the sense that matters. |

Escalation fires when **any** of these holds, and the reason gets logged:

- the Tier-1 judge returns `insufficient_information` (the II.10 escalation pattern, reused)
- `triage.py` marked the incident **critical**
- two or more sensing worlds opened incidents in the same tick (the storm-front case, which is the whole reason the supervisor exists)
- a `Finding` proposes a write

**Cost shape that falls out:** a calm ranch costs approximately nothing, and a storm costs real money. That is Phase III's "cost scales with change, not wall-clock" arriving three phases early as a side effect of the architecture rather than as a tuning exercise.

### The rail that makes this safe

A Tier-1 local model may **never** produce an all-clear. `triage.py` already flagged the incident in code, so "nothing is wrong here" from the cheap judge is a contradiction, not a finding. `validate.py` rejects it and escalates to Tier 2 rather than trusting it. That is the II.11 false-all-clear failure mode turned into a check instead of a footnote.

### Build order: Opus first, then move jobs down one at a time

Do **not** build the cascade at M2. Build Tier 2 only, log `model`, `tokens`, `latency_ms`, and `finish_reason` on every call, and then move one job at a time to local with a rail that catches the regression. Measure before you fix is the spine of the whole Phase II arc and it applies to its own cost lever. `docs/model-routing.md` keeps the ledger: what moved down, when, and what proved it was safe.

### Ollama specifics that will bite otherwise

- **Use `ChatOllama` from `langchain-ollama`, not `ChatOpenAI` pointed at the shim.** `ChatOllama` talks the native `/api/chat` endpoint where **`num_ctx` is actually honored**. On the OpenAI-compatible shim it is silently ignored, which is what produced the whole 4,096-token investigation: thinking models spent the entire output budget reasoning and returned an `AIMessage` with no tool call and no content, and it looked exactly like "small models are too weak." Python gets to skip that trap by picking the right client.
- **Set `num_ctx` explicitly to 16384** and never rely on a default.
- **`reasoning_effort` is an explicit per-call argument in `llm_client.py`, never an ambient env var.** A knob that silently changes verdicts belongs where the actor is built. That exact mistake meant a landed fix never reached the rung it was written for, because a second actor construction had quietly stopped receiving it.
- **`finish_reason` gets logged on every call.** It is the difference between "the model is too weak" and "the model never got to answer," and a log holding only the final text cannot tell them apart.

---

## Logging: three streams, JSON Lines, and one field that matters most

`utils/logger.py` uses **structlog** over the stdlib `logging` module. structlog because the files need to be machine-readable (the window and the eval rails both read them) while the console needs to be human-readable during a 30-minute watch, and structlog gives both from one call. Over the stdlib rather than beside it, so `httpx` and `langchain` chatter lands in the same stream instead of a second one.

**Format:** JSON Lines, one object per line, UTC ISO 8601 timestamps (`2026-09-10T14:30:00.000Z`), and `run_id` plus `tick` on **every** line in all three files so the three streams join on a grep. Console in dev gets `ConsoleRenderer`; files always get JSON regardless of environment.

### `logs/tick.jsonl` — the heartbeat, one line per tick

```jsonc
{
  "ts": "…",
  "run_id": "…",
  "tick": 42,
  "duration_ms": 8140,
  "sensors_read": 160,
  "sensors_failed": 0,
  "findings": 7,
  "opened": 2,
  "ongoing": 5,
  "resolved": 1,
  "agents_invoked": ["water_feed", "infrastructure"],
  "escalations": 1,
  "escalation_reasons": ["critical"],
  "tokens_in": 3120,
  "tokens_out": 812,
  "cost_usd": 0.0184,
  "chaos_fired": "storm_front",
}
```

This is the II.0 gauge promoted from a teaching instrument to a permanent one. One line per tick means the cost curve is a single `jq` away, which is the only way you will notice it stop being flat. **Written at tick end, always, including when the tick failed** (with `error` and `failed_stage`) - a tick that produces no line is indistinguishable from a dead loop.

### `logs/agent.jsonl` — the instrument, one line per model call

```jsonc
{
  "ts": "…",
  "run_id": "…",
  "tick": 42,
  "agent": "infrastructure",
  "tier": 1,
  "provider": "ollama",
  "model": "gemma4:e4b",
  "reasoning_effort": "none",
  "num_ctx": 16384,
  "tool_calls": 0,
  "input_tokens": 1698,
  "output_tokens": 214,
  "finish_reason": "stop",
  "latency_ms": 11200,
  "escalated_from": null,
  "validation": { "shape": "pass", "key": "pass", "grounding": "pass" },
}
```

**`finish_reason` is the single most valuable field in this whole scheme** and it is not negotiable. `"length"` means the model never got to answer, `"stop"` means it answered badly. One is a config bug and one is a model-selection decision, they present identically in the output text, and telling them apart once cost a real investigation. **Written immediately on return, before validation runs**, so a response that fails a check still leaves a receipt of what was actually returned.

### `logs/audit.jsonl` — the receipt, one line per side effect

```jsonc
{ "ts":"…","run_id":"…","tick":42,"audit_id":"…","phase":"proposed",
  "tool":"create_observation","args":{"animalId":"cow-0777","…":"…"},
  "proposed_by":"herd_health","incident_key":"cow-0777:down" }
{ "ts":"…","run_id":"…","tick":42,"audit_id":"…","phase":"decided",
  "decision":"approve","decided_by":"scott","result":"201","latency_to_decision_ms":94000 }
```

**Two lines per side effect, correlated by `audit_id`:** one at propose time, one at decision time. That is deliberate, because a propose with no matching decide is a pause nobody answered, and it should be visible as a dangling record rather than an absence. Append-only, rotated daily by date and **never** truncated by size. This is the file that makes "we watch your ranch" a defensible claim instead of a pitch.

### Rules

- **Never logged:** `ANTHROPIC_API_KEY`, `DATABASE_URL`, or any full prompt or response body. Prompts are the token bill and they are enormous.
- **Full transcripts behind a flag.** `LOG_TRANSCRIPTS=1` writes `logs/transcripts/{run_id}/{tick}-{agent}.json`. Off by default, invaluable when a finding reads wrong.
- **Rotation:** `RotatingFileHandler` for tick and agent (10 MB, 5 back); `TimedRotatingFileHandler` daily for audit, no size cap.
- **`logs/*.jsonl` is gitignored**, `logs/.gitkeep` is not. Captured runs worth keeping go in `docs/` next to the finding they support, the way `*.run.txt` did.

---

## Milestones

Each ends runnable and verifiable. **M0 through M8 need no containers and no new AWS**, just a venv, the already-live endpoints, and an Anthropic key. I stop and report at every boundary.

**M0 Skeleton, logging, and a live handshake.** The full tree above including the nested `CLAUDE.md` files and the `docs/` skeleton, plus `requirements.txt`, `config.py`, `logger.py` with all three streams wired, and `mcp_client.py` connected to the deployed Function URL over Streamable HTTP. _Verify:_ `python main.py --handshake` prints exactly **19** tool names, `read_resource("ranch://sensors/map")` returns ~160 sensors across 32 locations, and the handshake itself writes a well-formed line to `tick.jsonl`. Logging lands first on purpose: every rung in Phase II was only legible because the instrument was built before the thing it measured.

**M1 The free pass.** `tools/sensors.py`, `tools/triage.py`, `agent/memory.py`, Alembic migrations for `sw_ops`. Zero tokens spent. _Verify:_ three consecutive sweeps; sweep 2 reports the same fault as `ongoing` not `opened`; `alkali-flat-water` and `east-allotment-fence` appear every run because they carry fixed profiles; a planted unknown sensor type trips `warn_once` instead of vanishing.

> `asyncpg` against Supabase's pooler needs `statement_cache_size=0`. Same disease as `prepare: false` on postgres.js, different driver. Set it once in the engine factory with the reason in a comment so nobody cleans it up.

**M2 One agent, Opus only.** `tools/evidence.py` assembles the packet in code, then a single agent reads its SOP file and returns a Pydantic `Finding` (severity echoed not authored, work order, citations), invoked only on newly-opened incidents. Deliberately before any fan-out, and deliberately **Tier 2 only** so there is a known-good baseline to measure local models against later. _Verify:_ token cost flat across three sweeps while incident count grows; every work order names a real sensor and quotes its real reading; `agent.jsonl` carries `finish_reason` on every call; one narrow pytest grades the **reason text** for grounding facts, not just the severity label.

**M3 The four responders.** Supervisor, allowlists, explicit briefs, `asyncio.gather` fan-out, `Finding` as the handoff contract, cross-domain synthesis into one shift report. **The one thing worth proving rather than porting:** run a sub-agent once with no brief, capture it flailing, then pass the brief and capture it working, committed side by side. Sub-agents inherit nothing, and that fact is what this whole architecture rests on. _Verify:_ a test asserts each agent's exact tool count and that no agent can name a tool outside its set.

**M4 The continuous loop.** `executor.py`: tick cadence from config, graceful shutdown, per-tick structured log line, exponential backoff on upstream failure, and a tick that survives one sub-agent raising. _Verify:_ run for 30 minutes unattended; every tick logged; kill an upstream by pointing it at a bad URL mid-run and watch it back off and recover rather than die.

**M5 Chaos, the fifth agent.** `tools/chaos.py`, the overlay, the real-write path, the scenario catalog, TTL healing. _Verify:_ same `CHAOS_SEED` replays an identical event sequence across two runs; a storm front produces one fused work order rather than two unrelated ones; a healed sensor shows up as `resolved`; with `CHAOS_ALLOW_WRITES=0` no animal is ever mutated.

**M6 Gate and validation.** LangGraph Postgres checkpointer so a pause outlives the process, `interrupt()` on all four write tools, and three level-3 return-path checks: **shape**, **key**, **grounding**. The key match is on a real incident key, never on a model-written index. _Verify:_ let a tick pause on a `create_observation`, kill the process, restart, resume with reject and then approve; a planted-bad-response suite asserts _which_ check fires.

**M7 Model routing, one job at a time.** `models/routing.py` and the escalation predicate. Move the cheapest job to local first (chaos observation prose), confirm the rails hold, then the work-order write, then packet-judging. Each move gets a row in `docs/model-routing.md` with the before and after numbers. **The all-clear rail goes in before the first job moves down**, not after. _Verify:_ a calm tick costs $0.00 and logs `tier: 1`; a critical incident escalates and logs `escalation_reasons: ["critical"]`; a planted local-model all-clear on a code-flagged incident is rejected and escalated rather than believed.

**M8 The read API.** `api/routes.py`: `/ops/incidents`, `/ops/report`, `/ops/stream` (SSE), `/ops/gate` for approve/reject, `/health`. Same envelope conventions as the ranch APIs so the whole system reads consistently.

**M9 The window.** Next.js on Vercel: incident feed and ranch map against `/ops/*`. Light mode. Sensor coordinates are already in the catalog, so the map needs no new backend work.

**M10 Dockerize, and close the docs.** Now that it works: a `Dockerfile` for the orchestrator, a second for the API, and `docker-compose.yml` wiring them plus an optional local Postgres for offline work. The same image is what a Lambda container or a small always-on box would run, so this is the last step and also the first step of whatever hosting you pick. Then `README.md`, `docs/cookbook.md` ordered by the pain rather than the technique, and `docs/JOURNEY.md` written from the milestone notes as you go rather than reconstructed at the end.

---

## What does not come over

Named so nothing gets quietly resurrected:

- **The APIs and the MCP server.** They stay on Lambda; MCP-Farm goes read-only. If a tool turns out to be genuinely missing, that is a scoped change to the old repo and a conversation, not a drive-by.
- The 23 `agent-lab/*.mjs` rung scripts. The findings are the asset; the scripts already spent themselves earning them.
- `agent-lab-ui/` and `demo-site/`. M8's window absorbs both.
- `docs/prd.md`. Stale as a system description by its own admission. `docs/sweetwater-ranch.md` comes over as canon.

## Verification, end to end

1. `pytest`, `ruff check`, `mypy --strict` all green.
2. `python main.py` in a venv, then watch the log for 30 minutes. Ticks land, chaos fires and heals, incidents open and resolve. Only at M9 does this become `docker compose up`, and it must behave identically.
3. Read a shift report by eye. Right sensing world, real sensor, real reading, and would a hand on shift know what to do.
4. Human gate by hand: let a tick want a care write, watch it block, kill the process, restart, reject, then approve.
5. Replay determinism: same `CHAOS_SEED`, two runs, identical event sequence.
6. Read the logs, which is the real test of whether the instrument works:
   - `jq -r '[.tick,.tokens_in,.tokens_out,.cost_usd,.escalations]|@tsv' logs/tick.jsonl` - cost flat on calm ticks, spikes only where an escalation is logged alongside it.
   - `jq -r 'select(.finish_reason!="stop")' logs/agent.jsonl` - anything here is a config bug, not a weak model.
   - every `audit_id` in `audit.jsonl` appears twice. A single occurrence is a pause nobody answered.
7. Load the Vercel URL and watch three ticks land without a refresh.

## Open items

- **Where this runs in production.** ECS is off the table. You build and run it in a venv, dockerize at M9, and decide the host after that. Lambda container image on a short EventBridge schedule is the cheap answer for a tick loop; a small always-on box is the honest answer for "constantly running." Worth deciding once M4 exists and you can see how long a tick actually takes.
- **Multi-tenancy.** "Stand up the next ranch in a morning" implies per-client isolation of `sw_ops`. Not in V1.
