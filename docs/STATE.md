# STATE

The session-start briefing. **Read this one file, then the nested `CLAUDE.md` for the package you are about to touch, then stop and ask.** It exists so a new session does not have to read the doc set, probe the live ranch, or re-derive a decision that is already made.

Refreshed at every milestone boundary, same step as `JOURNEY.md`. If it disagrees with the code, the code wins and this file is stale, say so.

**Last refreshed:** 2026-09-10, at the **M4** boundary. M3 and M5 were built in parallel
sessions on one `master` and M5 closed first, so the phase numbers are not the commit order.
`git log` is the authority on what landed when.

---

## Where the build stands

| | |
| --- | --- |
| Done | **M0**: tree, three log streams, `config.py`, `mcp_client.py`, live handshake. **M1**: the free pass, `catalog -> sweep -> triage -> reconcile -> route`, five stages and zero tokens, plus the `sw_ops` store and Alembic. **M2**: the first phase that spends. `evidence.py`, `llm_client.py`, `system_prompts.py`, `workers.py`, the water and feed SOPs, and one agent (`water_feed`) writing a real work order per newly-opened incident. **M3**: the four responders and the supervisor. `allowlists.py` and the five tool slices, `agent_prompts.py` and the five briefs, `workers.fan_out` under one global ceiling, `agent.synthesize` and the shift report, and the four remaining SOPs. **M5**: chaos. The seeded overlay in `sw_ops.chaos_events` applied inside `sweep()`, the guarded animal write path, migration `0002`, the `python -m src.tools.chaos` CLI, and 64 new rails. Built beside M3, not after it. **M4**: the continuous loop. `run_loop` in `executor.py` with a start-to-start cadence, a spend ceiling that halts (exit 4), per-upstream backoff, a held set for unanswered incidents, chaos wired into the tick with a miss check, and a drain-on-interrupt shutdown that works on Windows. `cost_usd` on the tick line. `--no-spend`. |
| Gate | **306 tests**, `ruff check .` clean, `mypy` clean on 29 files, one alembic head, and every command in root `CLAUDE.md` re-run. M4's live checks: a **30-tick, 30-minute free run** at 60s cadence through a proxy that was killed and restored mid-run (backoff 60s then 120s, three skipped ticks each with a line, recovery on its own, two real log rotations, one real `chaos_event_missed`, SIGBREAK drain, exit 0, $0.00), then a **paid run on the prod ledger** that shipped 26/26 work orders over two fused ticks and **halted itself at the $3 ceiling with exit 4.** |
| Next | **M6, the gate and validation.** `GATE_LANDED` flips, `interrupt()` plus the Postgres checkpointer, and the held set's durable form if it is wanted. Or the debounce conversation first (see the M4 section), because it changes the bill more than anything M6 or M7 does. |
| HEAD | The M4 boundary on `master`. A hash here is stale by one commit by construction, so trust `git log` over this cell and the milestone over both. |
| Owed | **The coyote verification is still owed, and it is bigger than it was written up as.** Nothing in the tick reads the Care API: no stage produces an animal finding, so `herd_health` has no discovery path regardless of chaos or the supervisor. That is a new free stage (an animal sweep) plus a triage category, its own scoped item, not three lines. The three lines that *were* owed (`inject_for_tick` in the tick, `chaos_fired` on the line) landed at M4. Also owed, and needing an explicit yes: a column on `incidents` to make the held set survive a restart. |

Everything still open across phases, with what each would take: `docs/issues.md`. Milestone list and the shape: `docs/architecture.md`. The layout, the milestone order, and which leaf lands when: `docs/Plan.md`, which is the authority the tree matches. What happened and what diverged: `docs/JOURNEY.md`. Phase-close ritual: root `CLAUDE.md`.

---

## Where the code lives. Two modules were renamed at the M1 boundary.

`docs/Plan.md`'s tree is the authoritative layout, and the code was conformed to it rather than the reverse. There is **no `tick.py` and no `src/agent/routing.py`**; a search for either finds nothing, and that is the current state, not a missing file.

| Module | Holds | Grows |
| --- | --- | --- |
| `src/agent/executor.py` | `run_tick` (nine stages, the `spend` flag, `held` / `skip` / chaos inputs, the one `tick.jsonl` line), `run_loop` (cadence, ceiling, shutdown), `Backoff`, the exit codes, `STAGE_UPSTREAM`, `RETRIABLE_VIOLATIONS` | nothing structural. The LangGraph wiring, if it ever earns its place, wraps `run_tick` here |
| `src/agent/agent.py` | `ROUTES` (all 18 categories to an owner), `AGENTS` / `RESPONDERS`, and the supervisor's own stage: `render_shift_page`, `assemble_shift_report`, `check_shift_report`, `synthesize` | the checkpointer hooks at M6 |
| `src/agent/workers.py` | the rails (`check`), `to_work_order`, `judge_packet`, `run_agent` for any of the four, `fan_out` under one shared semaphore | nothing structural |
| `src/tools/allowlists.py` | `DEPLOYED_TOOLS` (all 19), `WRITE_TOOLS` (all 8), `SLICES`, `tools_for` / `bound_tools_for` / `assert_callable`, and `GATE_LANDED` | **M6 flips `GATE_LANDED`, and only M6 may** |
| `src/agent/state.py` | `RanchState`, `Finding`, `Incident`, `WorkOrder`, `ShiftReport` | |
| `src/agent/memory.py` | `sw_ops` only: reconcile, the engine allowlist, and the chaos-event store (`insert_chaos_events`, `active_chaos_events`, `expire_chaos_events`, `chaos_counts_by_status`) | the checkpointer at M6 |
| `src/tools/chaos.py` | the scenario catalog, the pure seeded `plan()`, `inject_for_tick`, `apply_overlay`, the guarded animal write path, and the CLI | more scenarios; nothing structural |
| `src/tools/evidence.py` | `assemble`, `EvidencePacket.render`, `SOP_FOR_CATEGORY` covering all 18 categories | nothing structural |
| `src/models/llm_client.py` | `resolve_provider`, `build_client`, `call_tier2`, `ModelResponse`, `THINKING_BUDGET`, `ASSUMED_RATE_USD_PER_M` and `cost_usd` | Tier 1 and the real pricing table at M7, and **not before** |
| `src/prompts/system_prompts.py` | what a **machine** consumes: `WORK_ORDER_SCHEMA`, `SHIFT_REPORT_SCHEMA`, the tool names and descriptions, `system_prompt()` | more schemas |
| `src/prompts/agent_prompts.py` | what a **model** reads: `INHERITED_RULES`, `MANDATES` (the four responders only, `chaos` absent and a test asserts it), `SUPERVISOR_MANDATE`. **`MANDATES` moved here at M3**; it is no longer in `system_prompts.py` | chaos's brief, if chaos ever needs one |
| `src/models/routing.py` | nothing yet | job to tier to model at M7. **A different question than `agent.py`'s routing**, which is why the two do not share a name |
| `tests/` | three files: `test_agent.py`, `test_tools.py`, `test_api.py` | |

Seven other leaves are docstring-only placeholders naming the milestone that fills them. A stub never claims to be implemented, and the marker column in `docs/Plan.md`'s tree is how you tell unbuilt from missing.

`data/knowledge_base/` holds **six** files, not four: `water.md` (`WATER-01` to `WATER-06`), `feed.md` (`FEED-01` to `FEED-04`), `infrastructure.md` (`INFRA-01` to `INFRA-06`), `sensors.md` (`SENSOR-01` to `SENSOR-05`), `wellhead.md` (`WELL-01` to `WELL-04`), and `compliance.md` (`COMP-01` to `COMP-05`). Infrastructure splits into three because a fence, a broken probe, and a gas wellhead are three unrelated bodies of knowledge, and `SOP_FOR_CATEGORY` is what maps each of the 18 categories onto one of them. **All six are derived from `docs/sweetwater-ranch.md`, one file per sensing world, and nothing else in this repo may source them.** A rule id is citable only if it is a heading in the file the packet carried, so an SOP file is the definition of what a work order is allowed to cite.

---

## Decisions already made. Do not re-litigate these.

1. **`sw_ops` exists and is migrated.** Schema plus an `incidents` table, SQLAlchemy async, asyncpg, `alembic_version` living inside `sw_ops` rather than `public`. One resolver picks the target for both `alembic` and `main.py`, because a migration applied to one database and a tick written to another presents as an empty ledger rather than as an error. **Any further Supabase migration is run only after asking Scott explicitly.**
2. **The sweep goes direct to `SENSOR_API` over httpx**, bounded by `SWEEP_CONCURRENCY`, because 160 reads per tick through a tool wrapper is pure overhead. The catalog comes from the `ranch://sensors/map` MCP resource, with `GET /sensors?limit=500` as fallback and second source of truth.
3. **Triage thresholds are derived from the ranch mission in `docs/sweetwater-ranch.md` and the observed distributions below.** Never lifted from the upstream service's source. See the boundary rule.
4. **Triage writes the human sentence in code.** Deterministic prose per finding, per the voice rules in `src/tools/CLAUDE.md`. A model never authors severity and never authors an all-clear.
5. **No sensor incident routes to `herd_health`**, because by design it cannot read a sensor. It stays idle until chaos writes real animal events at M5. That is correct, not a gap.
6. **One bad read is `pending`; two in a row is an incident.** `INCIDENT_CONFIRM_SWEEPS`, default 2, migration `0003`, decided and migrated on Supabase 2026-09-10 with Scott's explicit yes. A pending row that reads clean is `dismissed`, never `resolved`. The reason is the M4 measurement: the Sensor API redraws every reading, so at 1 the loop paid for 10 to 24 work orders per tick about tanks that were never empty.
7. **A sensor that did not answer resolves nothing.** `reconcile` takes the set of sensors that actually replied. "No finding" and "no reading" are different facts, and conflating them lets one upstream outage close every incident and report an all-clear.
8. **An empty catalog is a failed tick, not a calm one.** Nothing is swept and nothing is reconciled, because resolving every incident on the strength of a map we could not read is the worst available outcome.
9. **Structured output is a forced tool call, not a "reply in JSON" instruction.** The schema is enforced by the API, and - the reason that actually matters - `finish_reason` stays honest: `tool_use` is a real answer, `max_tokens` is a config bug. With free-form JSON both arrive as text and the distinction is gone.
10. **Thinking is off for the work-order job, and `reasoning_effort` is an explicit per-call argument.** Turning thinking off is free exactly when the model is not the one classifying, and `triage.py` classified. Per-call rather than ambient so turning it on for one job later does not touch any other call site.
11. **A work order is never dropped.** A model that never answered, timed out, or got truncated still produces a `WorkOrder` with `status="no_answer"` and the reason in `assessment`. A tick that silently loses an incident is indistinguishable from a ranch with nothing wrong.
12. **A write tool is declared, withheld from the model, and refused at runtime, all three, until M6 flips `GATE_LANDED`.** A declared-but-withheld tool is a documented seam; a live write tool with no gate is a bug waiting for a demo. `GATE_LANDED` is a boolean in one module and not a config value, because an env var is something somebody sets on a laptop at 11pm to make a demo work.
13. **Severity ownership extends to the shift report.** The supervisor may not restate a severity, and `linked` - its only causal claim - is checked in code against the incident keys the page actually carried. This is why `reasoning_effort` stays off even for fusion: the claim is verified rather than trusted.
14. **`ruff format` is deliberately not in the gate.** `E501` is ignored on purpose so a long line may stay long; the formatter hard-wraps at 140 with no escape hatch, so the two contradict. `ruff check` is the lint gate. Do not add the formatter back.
15. **The loop's spend ceiling halts; it never skips a tick and carries on.** `SPEND_CEILING_USD`, default $10, no unlimited value, exit **4**. Overshoot is one tick by design. Decided before the loop body was written, because M4 is the first phase where the money runs with nobody watching.
16. **Backoff is per upstream and the heartbeat never stops.** A tick in backoff writes its line naming who is sick. One dead service never stops the ranch watch, and the loop itself never sleeps past one cadence.
17. **A `no_answer` for a retriable reason is held and re-routed; a rail rejection never is.** The held set is in-process until a column on `incidents` gets an explicit yes.
18. **`cost_usd` is on the tick line from M4 at a stated assumed rate**, one constant in `llm_client.py`. M7 replaces the constant, not the field.
19. **Shutdown is written for Windows.** No `add_signal_handler`; the Runner's Ctrl+C handling plus `signal.signal` for SIGTERM/SIGBREAK, and the in-flight tick drains.

## The boundary rule, sharpened

`scott-jasper/mcp-farm` is the frozen upstream. A local clone exists at `C:\temp\MCP-Farm`. **Do not read it.** The 19 tools, the `ranch://sensors/map` resource, and the four REST APIs are the contract; that repo's source is not. Constants copied out of its internals are values nothing here can verify and that fail silently when they drift. Everything needed is reachable over the wire or already written down in this repo.

Learned the hard way on 2026-09-10, in this repo, by doing exactly that and retracting it. Full entry: `docs/cookbook.md` #5.

---

## Environment, verified 2026-09-10

- **Interpreter is `.venv/Scripts/python.exe`.** System `python` is 3.11.9 with none of the dependencies installed. Every command in the docs assumes the venv.
- **`.env` is filled in and working.** All five upstreams, both database URLs, chaos cohort. **It also has `CHAOS_ENABLED=1`**, which contradicts the line below saying it belongs at 0 and was the cause of `docs/cookbook.md` #26. The tests now disarm it themselves; a measurement run has to set `CHAOS_ENABLED=0` explicitly, as the M4 paid run did. Set it to 0 in `.env` when the demo is not being driven.
- **`ANTHROPIC_API_KEY` is still empty, and M4's paid run shipped on Bedrock anyway.** This machine authenticates to **AWS Bedrock** (`CLAUDE_CODE_USE_BEDROCK=1`, `us-east-1`, session-scoped temporary credentials), and `resolve_provider()` in `llm_client.py` picks first-party Anthropic when a key exists and Bedrock otherwise. Session credentials expire, so a loop meant to run for days needs a real key. From M4 an expiry mid-run is survivable: `ExpiredTokenException` becomes a `transport_error` no-answer, the incident is **held**, and the `model` upstream backs off; nothing is lost and nothing retries on a cadence against a dead credential.
- **`anthropic[bedrock]` is a first-order dependency now.** It was absent from `requirements.txt` and arrived transitively through `langchain-anthropic` without the extra, so the first live call failed on `No module named 'botocore'`. `llm_client.py` calls the raw SDK, not `ChatAnthropic`, because `langchain-anthropic` has no Bedrock path and M2 has no tool loop.
- **Ollama is up** on `localhost:11434`. Not needed until M7.
- **Supabase**: PostgreSQL 17.6, connects as `postgres`, can create schemas. **`sw_ops` now exists**, migrated to `0003`, with `alembic_version` inside it. The connection also has write access to `farm`, `feed`, `animal_care`, and `sensor`, and must never use it. The engine pins `search_path` to the target schema, and a test fails if any SQL in this repo names a ranch schema.
- **Local Postgres**: 16.4, database **`farm_systems_test`**, connects as `postgres` with no password. It already holds the upstream project's `farm`, `feed`, `animal_care`, and `sensor` schemas, so **`sw_ops_test` is the only schema this repo may create or drop.** A two-value allowlist in `memory.py` refuses anything else, and the store tests skip rather than fail when no local Postgres answers. This is the exact accident that cost three answer keys in a previous life of the project.
- Both database URLs in `.env` use the bare `postgresql://` scheme. `config.py` upgrades them to `postgresql+asyncpg://` in a validator, since SQLAlchemy otherwise reaches for psycopg2, which is not installed. Supabase's pooler additionally needs `statement_cache_size=0`, set once in the engine factory with the reason in a comment. Do not clean either of those up.
- **`SW_OPS_TARGET`** picks the store: unset or `prod` is Supabase, `test` is the local `sw_ops_test`. Prod is the default on purpose, because a default that quietly writes somewhere harmless is a default that ships.

---

## The live ranch, measured over HTTP. Do not re-probe to learn this.

**160 sensors, 32 locations, 13 types.** Envelope is `{ "data": ..., "meta": { count, limit, offset } }`.

- `GET /sensors?limit=500` returns the whole catalog in one page. Pagination only, no `type` filter, so filtering happens in Python.
- `GET /sensors/:id` returns the catalog entry plus `latestReading: { value, recordedAt }`, **synthesized fresh on every call and unanchored to the previous one.** Never cache it, never expect two reads a second apart to agree.
- `GET /sensors/:id/readings?limit=N` returns invented history, newest first, one every ten minutes. **It is synthesized independently of `/sensors/:id` and does not contain the value the sweep read**, even when its newest point carries a later timestamp. Both are honest; they are two draws. The evidence packet therefore labels the series as shape and trend only.
- **`/animals` and `/pastures` are on the Farm API, not the Care API.** A wrong base URL 404s rather than naming itself.
- **Join across services on the id, never the display name.** The sensor map spells a location `"Alkali Flat (alkali-flat)"` where REST says `"Alkali Flat"`, and the Farm API's pasture for sensor location `"East Allotment"` is `"East BLM Allotment"`. A name match returns an **empty set, not an error**, which reads exactly like a pasture with no animals in it.
- `GET /health` on the Sensor API returns `{ status, service }`. There is no `/locations` route.
- A dark sensor returns `status: "offline"` **and** `latestReading: null`.
- A faulted probe returns **`-500`** with `status: "online"`. That is a sensor fault, not a cold snap, and must not be triaged as a temperature reading.
- `gate` values are **boolean**, not numeric. Every other type is a number.
- 160 bounded reads at concurrency 20 complete comfortably inside the tick budget, all 200s.

One sample per sensor from a single sweep on 2026-09-10, so treat these as indicative of the operating range, not as limits:

| Type | Unit | n | observed low | median | observed high |
| --- | --- | --- | --- | --- | --- |
| `water-level` | gallons | 28 | 0.3 | 17.9 | 27.4 |
| `temperature` | °F | 26 | -21.2 (plus one `-500` fault) | 48.2 | 101.1 |
| `humidity` | % | 26 | 5.1 | 42.8 | 97.1 |
| `gate` | open/closed | 24 | boolean, one offline with null | | |
| `soil-moisture` | % | 18 | 14.1 | 28.9 | 57.1 |
| `feed-bin-weight` | lbs | 12 | 363.2 | 1167.0 | 1727.6 |
| `fence-voltage` | kV | 8 | 0.4 | 6.1 | 9.9 |
| `battery-charge` | % | 8 | 11.7 | 82.1 | 99.3 |
| `fuel-level` | % | 2 | 4.9 | 42.6 | 80.3 |
| `wellhead-pressure` | psi | 2 | 189.5 | 310.0 | 430.4 |
| `stream-flow` | cfs | 2 | 2.4 | 7.2 | 11.9 |
| `wind-speed` | mph | 2 | 7.5 | 10.2 | 12.9 |
| `snow-depth` | in | 1 | 9.7 | | |

The seven sensors that misbehave on purpose are listed at the bottom of `docs/sweetwater-ranch.md`. They read badly on every call, which makes them the only findings guaranteed to persist across ticks. Healthy sensors also draw extreme values a fraction of the time, so expect findings to churn tick to tick. M1 measured **18 to 23 per tick with chaos off**, which supersedes the 10-to-20 estimated here before the sweep existed. That churn is the feed working, not a bug to suppress.

---

## What M1 actually produces, so a new session does not re-derive it

The free pass is live and token-free. Numbers from the two verification runs on 2026-09-10, against the live ranch with chaos off:

- **160 sensors read, 0 failed**, both runs, catalog from the MCP resource.
- **18 to 23 findings per tick.** Run 1 opened 18. Run 2 showed **opened 15 / ongoing 8 / resolved 10**, which is the churn `docs/sweetwater-ranch.md` predicts and the proof that `reconcile` works.
- Routed to `infrastructure` and `water_feed`, with `compliance` appearing on run 2. `herd_health` and `chaos` get nothing yet, by design.
- Triage covers **13 sensor types across 18 categories**, every critical band with a warning tier under it, and a per-type physical-plausibility window that catches the `-500` sentinel as a fault rather than as a cold snap.
- Incident key is `sensor:category`. A partial unique index enforces one live incident per key while keeping the history of the resolved ones.

---

## What M2 actually produces, so a new session does not re-derive it

The tick now has seven stages. Five are free; the last two spend. `run_tick(spend=False)` stops at the end of `route`, which is how every rail above the model layer exercises the whole pipeline for nothing, and `conftest.no_model_calls` is the autouse guard behind the flag.

Per newly-opened incident that `water_feed` owns:

1. **`evidence.assemble`** buys roughly **four extra HTTP calls** - that sensor's history, its siblings at the same location, the pasture, the animal roster - and renders one page. Bounded by `SWEEP_CONCURRENCY`, invisible next to a 160-sensor sweep.
2. **`call_tier2`** sends that page plus the brief in **one** call, structured output as a **forced tool call** (`write_work_order`), thinking **off**. Measured **~4,800 to 5,600 in / ~1,100 out**, 14 to 16s, 4 in flight (`AGENT_CONCURRENCY = 4`).
3. **`workers.check`** runs the rails and stores a `WorkOrder`.

**The cost lever is the SOP file, not the evidence.** The SOP is the majority of those input tokens; the assembled facts are a few hundred. Worth knowing before M7 optimizes the wrong half.

Three live ticks on 2026-09-10, chaos off, against prod `sw_ops`:

| | tick 1 | tick 2 | tick 3 |
| --- | --- | --- | --- |
| packets judged | 9 | 6 | 4 |
| tokens (in + out) | 58,339 | 38,328 | 24,161 |
| ledger rows after | 25 | ~45 | 65 |

**19 work orders, 19 shipped, 0 rejected, 0 no-answer, every call `finish_reason=tool_use`.** Cost tracks **newly-opened** incidents, not open ones, which is the architecture's central claim arriving as a measurement. Rule citations discriminate rather than pattern-match: `WATER-05` on 12 of 19, but `east-allotment-water` cited only `WATER-02`.

**Severity is stored from the incident, never from the model's echo.** The echo lives in its own field so a disagreement is recorded rather than smoothed over, and a mismatch is a blocking violation. Five rails block (`severity_mismatch`, `all_clear`, `invented_rule`, `no_payload`, `schema_invalid`); two record and still ship (`no_rule_cited`, `sensor_not_named`). The all-clear rail reads the **actions list**, not the prose, because "the second tank at 16.7 gal is fine" is a correct sentence and the actual diagnosis. Details in `src/agent/CLAUDE.md`.

**There is no cascade, no local fallback, and no retry.** Tier 1 is absent rather than stubbed. M7 moves jobs down one at a time with a rail and a row in `docs/model-routing.md`, whose first row is now the Tier 2 baseline to beat.

---

## What M3 actually produces, so a new session does not re-derive it

The tick has **nine** stages now. Six are free, two spend per newly-opened incident, and the last one decides for itself.

**The tool slices are enforced in code, and the counts are the spec: 7 / 6 / 5 / 5 / 0.** A test asserts each one exactly, so a slice cannot grow by one tool without a deliberate edit to a number a human reads. Three agents legitimately share the three sensor read tools and that is **not** carved up: the isolation that matters is the brief, the SOP set, and which sensor types reach each agent, and carving it further would mean editing a frozen server. `herd_health` has no sensor reads at all, by design rather than omission.

**No write tool reaches a model before M6.** Three functions, and they are not redundant:

| | |
| --- | --- |
| `tools_for(agent)` | the declaration. Includes the writes, so the asserted counts are real counts |
| `bound_tools_for(agent)` | what a model would be handed. Subtracts `WRITE_TOOLS` while `GATE_LANDED` is False |
| `assert_callable(tool)` | the runtime guard in `mcp_client.call_tool`. Raises `WriteGateError`. This one covers **us**, not the model |

`WRITE_TOOLS` names all **eight** deployed writes, not the four that appear in a slice. `assign_to_pasture`, `remove_from_pasture`, `assign_to_shelter`, and `remove_from_shelter` are in no slice and belong in none, which is exactly why they are named: those are the ones somebody adds later while chasing one read out of the same API.

**`DEPLOYED_TOOLS` is all 19, read off the wire and not out of the upstream's source.** `--handshake` compares the deployed list against it and fails on drift in either direction. That check lives in the handshake rather than in `pytest`, because `pytest` has to pass on a plane.

The fan-out and the shift report:

- **`AGENT_CONCURRENCY = 4` is a global ceiling on Opus calls in flight.** `fan_out` builds one semaphore and hands the same object into every agent. Four agents each bounding themselves at four would be sixteen.
- **An agent failing is not the tick failing**, at both levels: a packet raising inside an agent, and the whole agent raising inside the fan-out. Either way every incident it carried comes back as a `no_answer` order.
- **`synthesize` runs on every tick and spends on almost none.** It calls a model only when `spend` is true *and* `FUSION_THRESHOLD = 2` or more sensing worlds opened incidents. Below that it assembles the page in code for free, and the same `assemble_shift_report` is both the calm-tick answer and the failure fallback, deliberately. A rejected report is **replaced**, carrying its violations, never retried.
- Two rails on the report, both blocking: `invented_incident` (`linked` against the keys the page carried) and `all_clear` (priorities and headline, never the `situation` prose).

Two live `--once` runs on 2026-09-10, against a ledger that had just been refilled, so both are unusually expensive:

| | tick A | tick B |
| --- | --- | --- |
| newly-opened, so calls | 15 across 3 worlds | 14 across 3 worlds |
| tokens (in + out) | 104,500 + 15,308 | 91,468 + 15,545 |
| wall clock | 85 s | 96 s |
| rail failures | 0 of 16 | 0 of 15 |

**29 work orders, 29 shipped, 0 rejected, 0 no-answer.** Per-call cost is unchanged from M2, so fan-out multiplies calls and not price. The supervisor is about 13% of the bill for one call against fourteen. **A calm tick is $0.00 exactly.** Do not read either tick as a steady-state budget; M4 measures that. Full numbers and the assumed rate: `docs/model-routing.md`.

**`herd_health` was handed nothing on both ticks and logged nothing about it.** Decision 5 working. A warning per tick per idle agent trains everyone to ignore the log.

**The rails cannot detect a missing brief, and that is measured rather than suspected.** `docs/no-brief-transcript.md` and `docs/with-brief-transcript.md` are the same model on the same packet with `COMPLIANCE_MANDATE` removed, and the unbriefed answer **passes every rail with zero violations** while naming no neighbour at all. Every rail asks whether an answer is defensible about its own incident; scope is not answerable from inside one work order. Both are pinned as fixtures. Do not turn any of it into a blocking rail: a rail that counts actions gets satisfied by padding.

---

## What M4 actually produces, so a new session does not re-derive it

`python main.py` is the loop. `python main.py --no-spend` is the loop with the two paid stages
off, which is how the mechanics are exercised for free. Exit codes: 0 clean drain, 1 forced or
broken, 2 config, 3 not built, **4 spend ceiling.** Knobs, all in `config.py` with the reasons:

| Knob | Default | What it does |
| --- | --- | --- |
| `TICK_INTERVAL_SECONDS` | 300 | start-to-start cadence. Chaos TTLs are in ticks and multiply by this at injection, so the two rescale together |
| `SPEND_CEILING_USD` | 10.00 | per-run ceiling, summed from `cost_usd` on the tick lines, checked after every tick, **halts with exit 4.** Must be positive; no unlimited value. Not applied to `--once` |
| `BACKOFF_BASE_SECONDS` / `BACKOFF_CAP_SECONDS` | 60 / 900 | per-upstream exponential window (`mcp`, `sensor`, `sw_ops`, `evidence`, `model`). The loop itself never sleeps past one cadence |
| `LOG_ROTATE_BYTES` | 10 MB | size cap on `tick.jsonl` and `agent.jsonl`; set small to force a rotation inside a verification run |

**What the live runs measured, 2026-09-10.** The free run: 30 ticks in 30 minutes at 60s, a
healthy free tick is **3.0 to 3.6s**, and a tick against a dead Sensor API fails in 18s with
`all 160 reads failed`. Backoff went 60s then 120s, ticks 12, 14, and 15 were skipped and each
wrote a line, tick 16 recovered on its own. One `chaos_event_missed`, real: a 4-tick stream fault
injected seconds before the outage healed during it. Two rotations at 8KB, one `run_id` across
three files. `CTRL_BREAK_EVENT` during the cadence sleep stopped it in the same millisecond, exit
0. The paid run, prod ledger, chaos off, 120s: tick 1 opened 12 and cost **$2.16** in 80s, tick 2
opened 14 and cost **$2.54** in 90s, 26/26 shipped, both reports fused, then **exit 4 at $4.70
against a $3.00 ceiling.**

**The finding that matters most, and what was done about it the same day: on this ranch every tick was a storm tick.**
The prediction in `docs/model-routing.md` was that a steady-state loop opens a handful of
incidents per tick. It opened 10 to 24 on every one of 30 free ticks and 12 then 14 on the paid
ones, because the deployed Sensor API synthesizes a fresh, unanchored reading on every call and
healthy sensors draw extremes a fraction of the time. At 300s that is roughly **$28 an hour.** The
levers are a debounce (open only on two consecutive bad sweeps, which the seven permanently-bad
sensors pass and a one-draw extreme does not) or M7's cascade. **The debounce shipped the same
day** as `INCIDENT_CONFIRM_SWEEPS = 2` and migration `0003` (decision 6); the live check is in
`docs/JOURNEY.md` under the M4 addendum. M7 is next.

**The held set and the miss check are in-process.** Both live for the run and die with it. A held
incident survives a restart only as `ongoing`; a fault injected by a previous run is never this
run's miss to report. Both durable forms are columns, and columns are a Supabase migration.

**Nothing in the tick reads the Care API.** Chaos's animal events are real writes, and the loop
fires them when armed and permitted, but no stage produces an animal finding, so `herd_health`
is handed nothing and the coyote verification cannot run in any tick yet. Its own item.

---

## M5 is landed. What a session needs to know before touching chaos

**`CHAOS_ENABLED` defaults to `0` and should stay `0` unless you are driving a demo.** With it
off, `sweep()` costs one boolean and opens no connection, so a tick is byte-for-byte what it
was before M5. An overlay firing underneath a cost measurement turns the numbers into noise,
which is why it shipped off.

| Knob | Default | What it does |
| --- | --- | --- |
| `CHAOS_ENABLED` | `0` | the master switch. Off means the overlay is never read |
| `CHAOS_SEED` | `1` | same seed, same demo, forever. `plan()` is pure |
| `CHAOS_ALLOW_WRITES` | `0` | the only thing standing between a scenario and a real `PATCH` on the deployed Farm API |
| `CHAOS_ANIMAL_COHORT` | five `cow-090x` ids | every animal mutation is confined to this set. An empty cohort means no animal event can fire at all |
| `CHAOS_MAX_ACTIVE` | `6` | the ceiling. A correlated group that would cross it is deferred **whole**, never half-fired |

Two injection paths, and the asymmetry is deliberate: **sensor faults are a lie this repo
tells over a truthful upstream**, applied in `sweep()` and nowhere else; **animal events are
real writes** through two different APIs, because `herd_health` has to find them with its own
tools. Detail in `src/tools/CLAUDE.md`, including the two-API table that M5 got wrong first.

Driving a demo. **The store target is explicit on purpose**: `resolve_store` defaults to
prod, which is right for a tick and wrong for a copy-pasted experiment.

```bash
export SW_OPS_TARGET=test CHAOS_ENABLED=1        # drop SW_OPS_TARGET when you mean prod
python -m src.tools.chaos status                 # what is armed, and how far through its TTL
python -m src.tools.chaos plan --ticks 6         # what this seed will do, against the live catalog, touching nothing
python -m src.tools.chaos inject --tick 2        # arm tick 2's events, and heal anything expired
python -m src.tools.chaos expire                 # heal everything whose TTL is up
python -m src.tools.chaos restore                # put the cohort back to `active`. Needs CHAOS_ALLOW_WRITES=1
```

`restore` **exits 1 when it restored nothing**, which is what a guarded refusal looks like from
a shell: the command did not do its job, and a script chaining off it should stop. The other
four exit 0 whether or not they had anything to do, because "nothing was due" is a successful
answer. `plan` reads the live catalog and touches no database at all.

**A replay is not a re-run.** Event ids are derived from seed, tick, and index, so injecting
the same seed twice inserts nothing the second time. Re-driving a demo from the top means
truncating `chaos_events` first. `docs/cookbook.md` #19.

**Two sessions cannot run `pytest` at the same time.** `conftest.py` drops `sw_ops_test`
`CASCADE` and re-migrates from its own worktree's `alembic/`, and the local Postgres is
shared. This actually happened twice during M5's phase close and looked exactly like a
migration rolling itself back. `docs/cookbook.md` #18.

**There is one known orphan on the deployed Care API**: observation
`0328d7e2-d271-4410-907c-a84020c2c8c7` against the nonexistent animal `zz-does-not-exist-0000`,
created by a contract probe that returned 201 instead of the expected 422. Observations are
append-only upstream, so it cannot be deleted from this repo. It is invisible to every real
animal. Written down so it is a known artifact, not a mystery.

---

## Working preferences that cost context to rediscover

Short answers. No recap tables of things already agreed. Do not read whole files when a targeted search answers the question. Do not dump raw probe output into the transcript, summarize it. Batch independent reads and commands into one round.
