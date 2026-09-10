# STATE

The session-start briefing. **Read this one file, then the nested `CLAUDE.md` for the package you are about to touch, then stop and ask.** It exists so a new session does not have to read the doc set, probe the live ranch, or re-derive a decision that is already made.

Refreshed at every milestone boundary, same step as `JOURNEY.md`. If it disagrees with the code, the code wins and this file is stale, say so.

**Last refreshed:** 2026-09-10, at the **M5** boundary. M5 was built in a parallel session
while M3 was in flight, so **M3 may have landed after this line was written**; if the table
below and `git log` disagree, `git log` wins.

---

## Where the build stands

| | |
| --- | --- |
| Done | **M0**: tree, three log streams, `config.py`, `mcp_client.py`, live handshake. **M1**: the free pass, `catalog -> sweep -> triage -> reconcile -> route`, five stages and zero tokens, plus the `sw_ops` store and Alembic. **M2**: the first phase that spends. `evidence.py`, `llm_client.py`, `system_prompts.py`, `workers.py`, the water and feed SOPs, and one agent (`water_feed`) writing a real work order per newly-opened incident. **M3**: the supervisor, all five agents, the tool slices, and the remaining SOPs. Its code is on `master`; **its own phase close, including its `JOURNEY.md` entry, belongs to that session and is not reflected here.** **M5**: chaos. The seeded overlay in `sw_ops.chaos_events` applied inside `sweep()`, the guarded animal write path, migration `0002`, the `python -m src.tools.chaos` CLI, and 64 new rails. Built beside M3, not after it. |
| Gate | **279 tests on the tree M5 landed on** (M3 and M5 together; the count was still moving as the other session committed), `ruff check .` clean, `mypy` clean on 29 files, one alembic head. M5's live checks: the same live sweep read calm honestly and critical in three categories with the overlay on, and `--once` on the combined tree shipped **23/23 work orders on 161,851 tokens** against `sw_ops_test`. |
| Next | **M4, the tick loop.** Bare `python main.py` still exits 3 naming M4. |
| HEAD | The M5 boundary on `master`, rebased on top of M3's two commits. A hash here is stale by one commit by construction, so trust `git log` over this cell and the milestone over both. |
| Owed | Two verifications are **deferred to the M3 boundary and deliberately unrun**: that a storm front fuses into one work order, and that `herd_health` discovers the coyote kill through its own tools. Both need a supervisor that now exists, so both are now doable. Neither was faked or weakened. Also owed: three lines wiring `inject_for_tick` into `run_tick`, and `chaos_fired` on the tick line. **And one defect M5's boundary run found in M3's code**: the supervisor's shift report truncated at `max_tokens=1024` and fell back to `shift_report=code`. `logging.md` calls that exact signature a config bug rather than a weak model. Left for the M3 session, whose file it is. |

Milestone list and the shape: `docs/architecture.md`. The layout, the milestone order, and which leaf lands when: `docs/Plan.md`, which is the authority the tree matches. What happened and what diverged: `docs/JOURNEY.md`. Phase-close ritual: root `CLAUDE.md`.

---

## Where the code lives. Two modules were renamed at the M1 boundary.

`docs/Plan.md`'s tree is the authoritative layout, and the code was conformed to it rather than the reverse. There is **no `tick.py` and no `src/agent/routing.py`**; a search for either finds nothing, and that is the current state, not a missing file.

| Module | Holds | Grows |
| --- | --- | --- |
| `src/agent/executor.py` | `run_tick`, the five free stages, the two spend stages, the `spend` flag, the one `tick.jsonl` line | cadence, backoff, graceful shutdown at M4 |
| `src/agent/agent.py` | `ROUTES`, all 18 categories to an owner | the LangGraph supervisor and the five worker factories at M3 |
| `src/agent/workers.py` | `water_feed`: the rails (`check`), `to_work_order`, `judge_packet`, `run_water_feed` | the other four agents at M3 |
| `src/agent/state.py` | `RanchState`, `Finding`, `Incident`, `WorkOrder` | |
| `src/agent/memory.py` | `sw_ops` only: reconcile, the engine allowlist, and the chaos-event store (`insert_chaos_events`, `active_chaos_events`, `expire_chaos_events`, `chaos_counts_by_status`) | the checkpointer at M6 |
| `src/tools/chaos.py` | the scenario catalog, the pure seeded `plan()`, `inject_for_tick`, `apply_overlay`, the guarded animal write path, and the CLI | more scenarios; nothing structural |
| `src/tools/evidence.py` | `assemble`, `EvidencePacket.render`, `SOP_FOR_CATEGORY` | more SOP files as M3 adds sensing worlds |
| `src/models/llm_client.py` | `resolve_provider`, `build_client`, `call_tier2`, `ModelResponse`, `THINKING_BUDGET` | Tier 1 at M7, and **not before** |
| `src/prompts/system_prompts.py` | `INHERITED_RULES`, `MANDATES`, `WORK_ORDER_SCHEMA`, `system_prompt()` | four more mandates at M3 |
| `src/models/routing.py` | nothing yet | job to tier to model at M7. **A different question than `agent.py`'s routing**, which is why the two do not share a name |
| `tests/` | three files: `test_agent.py`, `test_tools.py`, `test_api.py` | |

Seven other leaves are docstring-only placeholders naming the milestone that fills them. A stub never claims to be implemented, and the marker column in `docs/Plan.md`'s tree is how you tell unbuilt from missing.

`data/knowledge_base/` holds `water.md` (`WATER-01` to `WATER-06`) and `feed.md` (`FEED-01` to `FEED-04`). **Both are derived from `docs/sweetwater-ranch.md`, one file per sensing world, and nothing else in this repo may source them.** A rule id is citable only if it is a heading in the file the packet carried, so an SOP file is the definition of what a work order is allowed to cite.

---

## Decisions already made. Do not re-litigate these.

1. **`sw_ops` exists and is migrated.** Schema plus an `incidents` table, SQLAlchemy async, asyncpg, `alembic_version` living inside `sw_ops` rather than `public`. One resolver picks the target for both `alembic` and `main.py`, because a migration applied to one database and a tick written to another presents as an empty ledger rather than as an error. **Any further Supabase migration is run only after asking Scott explicitly.**
2. **The sweep goes direct to `SENSOR_API` over httpx**, bounded by `SWEEP_CONCURRENCY`, because 160 reads per tick through a tool wrapper is pure overhead. The catalog comes from the `ranch://sensors/map` MCP resource, with `GET /sensors?limit=500` as fallback and second source of truth.
3. **Triage thresholds are derived from the ranch mission in `docs/sweetwater-ranch.md` and the observed distributions below.** Never lifted from the upstream service's source. See the boundary rule.
4. **Triage writes the human sentence in code.** Deterministic prose per finding, per the voice rules in `src/tools/CLAUDE.md`. A model never authors severity and never authors an all-clear.
5. **No sensor incident routes to `herd_health`**, because by design it cannot read a sensor. It stays idle until chaos writes real animal events at M5. That is correct, not a gap.
6. **A sensor that did not answer resolves nothing.** `reconcile` takes the set of sensors that actually replied. "No finding" and "no reading" are different facts, and conflating them lets one upstream outage close every incident and report an all-clear.
7. **An empty catalog is a failed tick, not a calm one.** Nothing is swept and nothing is reconciled, because resolving every incident on the strength of a map we could not read is the worst available outcome.
8. **Structured output is a forced tool call, not a "reply in JSON" instruction.** The schema is enforced by the API, and - the reason that actually matters - `finish_reason` stays honest: `tool_use` is a real answer, `max_tokens` is a config bug. With free-form JSON both arrive as text and the distinction is gone.
9. **Thinking is off for the work-order job, and `reasoning_effort` is an explicit per-call argument.** Turning thinking off is free exactly when the model is not the one classifying, and `triage.py` classified. Per-call rather than ambient so turning it on for one job later does not touch any other call site.
10. **A work order is never dropped.** A model that never answered, timed out, or got truncated still produces a `WorkOrder` with `status="no_answer"` and the reason in `assessment`. A tick that silently loses an incident is indistinguishable from a ranch with nothing wrong.
11. **`ruff format` is deliberately not in the gate.** `E501` is ignored on purpose so a long line may stay long; the formatter hard-wraps at 140 with no escape hatch, so the two contradict. `ruff check` is the lint gate. Do not add the formatter back.

## The boundary rule, sharpened

`scott-jasper/mcp-farm` is the frozen upstream. A local clone exists at `C:\temp\MCP-Farm`. **Do not read it.** The 19 tools, the `ranch://sensors/map` resource, and the four REST APIs are the contract; that repo's source is not. Constants copied out of its internals are values nothing here can verify and that fail silently when they drift. Everything needed is reachable over the wire or already written down in this repo.

Learned the hard way on 2026-09-10, in this repo, by doing exactly that and retracting it. Full entry: `docs/cookbook.md` #5.

---

## Environment, verified 2026-09-10

- **Interpreter is `.venv/Scripts/python.exe`.** System `python` is 3.11.9 with none of the dependencies installed. Every command in the docs assumes the venv.
- **`.env` is filled in and working.** All five upstreams, both database URLs, chaos cohort.
- **`ANTHROPIC_API_KEY` is still empty, and M2 shipped anyway.** It was called a hard blocker here and was not one: this machine authenticates to **AWS Bedrock** (`CLAUDE_CODE_USE_BEDROCK=1`, `us-east-1`, session-scoped temporary credentials), and `resolve_provider()` in `llm_client.py` picks first-party Anthropic when a key exists and Bedrock otherwise. One code path, two credentials. **This is right for a supervised phase and wrong for M4:** session credentials expire, so the continuous loop needs a real key. Expiry surfaces as `ExpiredTokenException` in `ModelResponse.error` and a `no_answer` work order, not as a crash.
- **`anthropic[bedrock]` is a first-order dependency now.** It was absent from `requirements.txt` and arrived transitively through `langchain-anthropic` without the extra, so the first live call failed on `No module named 'botocore'`. `llm_client.py` calls the raw SDK, not `ChatAnthropic`, because `langchain-anthropic` has no Bedrock path and M2 has no tool loop.
- **Ollama is up** on `localhost:11434`. Not needed until M7.
- **Supabase**: PostgreSQL 17.6, connects as `postgres`, can create schemas. **`sw_ops` now exists**, migrated to `0001`, with `alembic_version` inside it. The connection also has write access to `farm`, `feed`, `animal_care`, and `sensor`, and must never use it. The engine pins `search_path` to the target schema, and a test fails if any SQL in this repo names a ranch schema.
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

## M3 scope, the other four agents and the supervisor

| Piece | Job |
| --- | --- |
| `src/agent/agent.py` | the LangGraph supervisor and the five worker factories. The routing table is already there |
| `src/agent/workers.py` | the other four agents. `run_water_feed` is the shape; `MANDATES` is where each brief goes |
| `src/prompts/agent_prompts.py` | the five briefs. **A sub-agent inherits nothing**, so anything it needs is in its brief or its packet |
| `src/tools/allowlists.py` | the five tool slices, enforced in **code**. An explicit set of literal names, never a prefix match |
| `data/knowledge_base/` | the remaining SOPs, one file per sensing world, derived from `docs/sweetwater-ranch.md` and nothing else |

Two things already known that M3 will trip over: **`system_prompt()` logs `no_mandate_for_agent` for the four agents with no brief yet**, which is deliberate and is the signal that one is missing. And **no sensor incident routes to `herd_health`** by design, so it stays idle until chaos writes real animal events at M5. That is correct, not a gap.

Then the phase-close ritual in root `CLAUDE.md`, in order, no exceptions. M1 skipped step 2 and it is what made this file lie to the next session for a commit; M2's step 2 is what found a documented `jq` command that had been wrong in three files for two milestones.

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
