# STATE

The session-start briefing. **Read this one file, then the nested `CLAUDE.md` for the package you are about to touch, then stop and ask.** It exists so a new session does not have to read the doc set, probe the live ranch, or re-derive a decision that is already made.

Refreshed at every milestone boundary, same step as `JOURNEY.md`. If it disagrees with the code, the code wins and this file is stale, say so.

**Last refreshed:** 2026-09-10, end of the M1 planning session.

---

## Where the build stands

| | |
| --- | --- |
| Done | **M0**: tree, three log streams, `config.py`, `mcp_client.py`, live handshake. Gate green: 17 tests, `ruff check` clean, `mypy --strict` clean, `--handshake` exit 0. |
| Next | **M1, the free pass.** Not started. No code written yet. |
| HEAD | `ab4d2ff` on `master`. Working tree clean apart from `.env` (gitignored). |

Milestone list and the shape: `docs/architecture.md`. What happened and what diverged: `docs/JOURNEY.md`. Phase-close ritual: root `CLAUDE.md`.

---

## Decisions already made. Do not re-litigate these.

1. **M1 carries the real `sw_ops` store.** Alembic migration creating the schema plus an `incidents` table, SQLAlchemy async, asyncpg. Built against local Postgres first. The Supabase migration is run only after asking Scott explicitly.
2. **The sweep goes direct to `SENSOR_API` over httpx**, bounded by `SWEEP_CONCURRENCY`, because 160 reads per tick through a tool wrapper is pure overhead. The catalog comes from the `ranch://sensors/map` MCP resource, with `GET /sensors?limit=500` as fallback and second source of truth.
3. **Triage thresholds are derived from the ranch mission in `docs/sweetwater-ranch.md` and the observed distributions below.** Never lifted from the upstream service's source. See the boundary rule.
4. **Triage writes the human sentence in code.** Deterministic prose per finding, per the voice rules in `src/tools/CLAUDE.md`. A model never authors severity and never authors an all-clear.
5. In M1 **no sensor incident routes to `herd_health`**, because by design it cannot read a sensor. It stays idle until chaos writes real animal events at M5. That is correct, not a gap.

## The boundary rule, sharpened

`scott-jasper/mcp-farm` is the frozen upstream. A local clone exists at `C:\temp\MCP-Farm`. **Do not read it.** The 19 tools, the `ranch://sensors/map` resource, and the four REST APIs are the contract; that repo's source is not. Constants copied out of its internals are values nothing here can verify and that fail silently when they drift. Everything needed is reachable over the wire or already written down in this repo.

Learned the hard way on 2026-09-10, in this repo, by doing exactly that and retracting it. Full entry lands in `docs/cookbook.md` at the M1 boundary.

---

## Environment, verified 2026-09-10

- **Interpreter is `.venv/Scripts/python.exe`.** System `python` is 3.11.9 with none of the dependencies installed. Every command in the docs assumes the venv.
- **`.env` is filled in and working.** All five upstreams, both database URLs, chaos cohort.
- **`ANTHROPIC_API_KEY` is empty.** Fine through M1, which spends nothing. Hard blocker at M2.
- **Ollama is up** on `localhost:11434`. Not needed until M7.
- **Supabase**: PostgreSQL 17.6, connects as `postgres`, can create schemas. `sw_ops` does **not** exist yet. The connection also has write access to `farm`, `feed`, `animal_care`, and `sensor`, and must never use it. M1 pins the engine `search_path` to `sw_ops` and adds a test that fails if any SQL in the repo names a ranch schema.
- **Local Postgres**: 16.4, database **`farm_systems_test`**, connects as `postgres` with no password. It already holds the upstream project's `farm`, `feed`, `animal_care`, and `sensor` schemas, so **`sw_ops_test` is the only schema this repo may create or drop.** M1 adds a rail that refuses to run or drop anything else. This is the exact accident that cost three answer keys in a previous life of the project.
- Both database URLs in `.env` use the bare `postgresql://` scheme. `config.py` needs a validator upgrading it to `postgresql+asyncpg://`, since SQLAlchemy otherwise reaches for psycopg2, which is not installed.

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

The seven sensors that misbehave on purpose are listed at the bottom of `docs/sweetwater-ranch.md`. They read badly on every call, which makes them the only findings guaranteed to persist across ticks. Healthy sensors also draw extreme values a fraction of the time, so expect roughly **10 to 20 findings per tick with chaos off**, some opening and resolving tick to tick. That churn is the feed working, not a bug to suppress.

---

## M1 scope, the free pass

Four stages, zero tokens. Nothing here calls a model.

| Piece | Job |
| --- | --- |
| `src/utils/config.py` | add the `postgresql://` to `+asyncpg` validator |
| `src/tools/sensors.py` | catalog once, bounded live sweep, upstream errors returned as data and never raised, no retry inside the tool layer |
| `src/tools/triage.py` | severity in code from per-type thresholds. Every type decided explicitly, a warning tier beneath every critical, `warn_once` on an unrecognized type, incident key is `sensor:category` |
| `src/agent/state.py` | `RanchState`, `Finding`, `Incident` |
| `src/agent/memory.py` | reconcile into `sw_ops` as opened / ongoing / resolved, keyed on sensor plus category, so a persisting fault is never re-alarmed |
| routing | pure function mapping category to the owning agent. No fan-out, no model |
| `main.py --once` | wire it end to end, one real `tick.jsonl` line, exit 0 |
| Alembic | migration 0001 creating `sw_ops` plus `incidents` |
| tests | triage truth table, unknown type trips `warn_once`, sweep concurrency ceiling, the three reconciliation buckets, the schema-name guard, the ranch-schema grep |

**M1 live verification:** `--once` twice in a row. The second run has to show `ongoing`, which is the whole point of `memory.py` and the reason the store is in this phase rather than M4.

Then the phase-close ritual in root `CLAUDE.md`, in order, no exceptions.

---

## Working preferences that cost context to rediscover

Short answers. No recap tables of things already agreed. Do not read whole files when a targeted search answers the question. Do not dump raw probe output into the transcript, summarize it. Batch independent reads and commands into one round.
