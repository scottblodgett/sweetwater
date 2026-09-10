# STATE

The session-start briefing. **Read this one file, then the nested `CLAUDE.md` for the package you are about to touch, then stop and ask.** It exists so a new session does not have to read the doc set, probe the live ranch, or re-derive a decision that is already made.

Refreshed at every milestone boundary, same step as `JOURNEY.md`. If it disagrees with the code, the code wins and this file is stale, say so.

**Last refreshed:** 2026-09-10, at the M1 boundary.

---

## Where the build stands

| | |
| --- | --- |
| Done | **M0**: tree, three log streams, `config.py`, `mcp_client.py`, live handshake. **M1**: the free pass, `catalog -> sweep -> triage -> reconcile -> route`, five stages and zero tokens, plus the `sw_ops` store and Alembic. |
| Gate | 132 tests, `ruff check .` clean, `mypy --strict` clean on 28 files, `--once` twice against the live ranch showing `ongoing` on the second run. |
| Next | **M2, one agent, Opus only.** Not started. `ANTHROPIC_API_KEY` is a hard blocker, see below. |
| HEAD | The M1 boundary on `master`: `ac8822d` is the last code commit, followed by the doc commit that carries this file. Working tree clean apart from `.env` (gitignored). A hash here is stale by one commit by construction, so trust `git log` over this cell and the milestone over both. |

Milestone list and the shape: `docs/architecture.md`. The layout, the milestone order, and which leaf lands when: `docs/Plan.md`, which is the authority the tree matches. What happened and what diverged: `docs/JOURNEY.md`. Phase-close ritual: root `CLAUDE.md`.

---

## Where the code lives. Two modules were renamed at the M1 boundary.

`docs/Plan.md`'s tree is the authoritative layout, and the code was conformed to it rather than the reverse. There is **no `tick.py` and no `src/agent/routing.py`**; a search for either finds nothing, and that is the current state, not a missing file.

| Module | Holds | Grows |
| --- | --- | --- |
| `src/agent/executor.py` | `run_tick`, the five free stages, the one `tick.jsonl` line | cadence, backoff, graceful shutdown at M4 |
| `src/agent/agent.py` | `ROUTES`, all 18 categories to an owner | the LangGraph supervisor and the five worker factories at M3 |
| `src/agent/state.py` | `RanchState`, `Finding`, `Incident` | |
| `src/agent/memory.py` | `sw_ops` only: reconcile, the engine allowlist | chaos events at M5, the checkpointer at M6 |
| `src/models/routing.py` | nothing yet | job to tier to model at M7. **A different question than `agent.py`'s routing**, which is why the two do not share a name |
| `tests/` | three files: `test_agent.py`, `test_tools.py`, `test_api.py` | |

Eleven other leaves are docstring-only placeholders naming the milestone that fills them. A stub never claims to be implemented, and the marker column in `docs/Plan.md`'s tree is how you tell unbuilt from missing.

---

## Decisions already made. Do not re-litigate these.

1. **`sw_ops` exists and is migrated.** Schema plus an `incidents` table, SQLAlchemy async, asyncpg, `alembic_version` living inside `sw_ops` rather than `public`. One resolver picks the target for both `alembic` and `main.py`, because a migration applied to one database and a tick written to another presents as an empty ledger rather than as an error. **Any further Supabase migration is run only after asking Scott explicitly.**
2. **The sweep goes direct to `SENSOR_API` over httpx**, bounded by `SWEEP_CONCURRENCY`, because 160 reads per tick through a tool wrapper is pure overhead. The catalog comes from the `ranch://sensors/map` MCP resource, with `GET /sensors?limit=500` as fallback and second source of truth.
3. **Triage thresholds are derived from the ranch mission in `docs/sweetwater-ranch.md` and the observed distributions below.** Never lifted from the upstream service's source. See the boundary rule.
4. **Triage writes the human sentence in code.** Deterministic prose per finding, per the voice rules in `src/tools/CLAUDE.md`. A model never authors severity and never authors an all-clear.
5. **No sensor incident routes to `herd_health`**, because by design it cannot read a sensor. It stays idle until chaos writes real animal events at M5. That is correct, not a gap.
6. **A sensor that did not answer resolves nothing.** `reconcile` takes the set of sensors that actually replied. "No finding" and "no reading" are different facts, and conflating them lets one upstream outage close every incident and report an all-clear.
7. **An empty catalog is a failed tick, not a calm one.** Nothing is swept and nothing is reconciled, because resolving every incident on the strength of a map we could not read is the worst available outcome.
8. **`ruff format` is deliberately not in the gate.** `E501` is ignored on purpose so a long line may stay long; the formatter hard-wraps at 140 with no escape hatch, so the two contradict. `ruff check` is the lint gate. Do not add the formatter back.

## The boundary rule, sharpened

`scott-jasper/mcp-farm` is the frozen upstream. A local clone exists at `C:\temp\MCP-Farm`. **Do not read it.** The 19 tools, the `ranch://sensors/map` resource, and the four REST APIs are the contract; that repo's source is not. Constants copied out of its internals are values nothing here can verify and that fail silently when they drift. Everything needed is reachable over the wire or already written down in this repo.

Learned the hard way on 2026-09-10, in this repo, by doing exactly that and retracting it. Full entry: `docs/cookbook.md` #5.

---

## Environment, verified 2026-09-10

- **Interpreter is `.venv/Scripts/python.exe`.** System `python` is 3.11.9 with none of the dependencies installed. Every command in the docs assumes the venv.
- **`.env` is filled in and working.** All five upstreams, both database URLs, chaos cohort.
- **`ANTHROPIC_API_KEY` is still empty.** It cost nothing through M1. It is a **hard blocker on M2**, which is Opus-only by design, so fill it before starting that phase.
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
- `GET /sensors/:id/readings?limit=N` returns invented history, newest first, one every ten minutes.
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

## M2 scope, one agent, Opus only

The first phase that spends money. Deliberately before any fan-out, and deliberately **Tier 2 only**, so there is a known-good baseline to measure local models against at M7.

| Piece | Job |
| --- | --- |
| `src/models/llm_client.py` | provider registry, `reasoning_effort` as an explicit per-call argument, usage receipts. Four Ollama traps are already written down in the file's docstring and in `src/models/CLAUDE.md`; read them before writing a line |
| `src/tools/evidence.py` | assemble the packet in **code**: the incident, that sensor's recent history, its siblings at the same location, the animals in that pasture, the relevant SOP. The model judges one page and drives no tool loop |
| `src/prompts/system_prompts.py` | the three inherited rules. Write them against a real assembled packet, not an imagined one |
| `data/knowledge_base/` | the SOPs, one file per sensing world |
| one agent | reads its SOP, returns a Pydantic `Finding`: severity **echoed, never authored**, work order, citations. Invoked only on newly-opened incidents |
| `logs/agent.jsonl` | its first real lines. `finish_reason` written on return, **before** validation runs |

**M2 verification:** token cost flat across three sweeps while incident count grows; every work order names a real sensor and quotes its real reading; `finish_reason` on every call; one narrow test grades the **reason text** for grounding facts, not just the severity label.

Then the phase-close ritual in root `CLAUDE.md`, in order, no exceptions. M1 skipped step 2 and it is what made this file lie to the next session for a commit.

---

## Working preferences that cost context to rediscover

Short answers. No recap tables of things already agreed. Do not read whole files when a targeted search answers the question. Do not dump raw probe output into the transcript, summarize it. Batch independent reads and commands into one round.
