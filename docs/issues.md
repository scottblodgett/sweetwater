# Issues

Open items observed across M0 through M9, first written at the M4 boundary on 2026-09-10 and
extended at each boundary since, the last time at the M9 boundary on 2026-09-11, the close of the M phases. Nothing here is a failing gate. These are deferred verifications, owed
pieces, decisions waiting on a person, and known artifacts. Each one says where it came from and
what it would take, so it can be picked up cold.

Plain-language rule for reading this: **"owed" means we said we would prove it and have not;
"needs a yes" means the code is ready but the change touches Supabase or the plan's order;
"decision" means there is a fork and nobody has picked.**

---

## Owed verifications

### 1. `herd_health` discovering the coyote kill through its own tools. **Closed at M7A, 2026-09-11.**

**What closed it.** `tools/herd.py`, the second free sweep. `chaos inject --tick 32` PATCHed cow-0905 to
`deceased` on the real Farm API; the next tick held `cow-0905:deceased` pending, the one after opened it
and routed it to `herd_health` alone, and the order named her by id and tag, quoted the coyote note
verbatim, cited `HERD-01`, and escalated to the GM on the predator note. The pause is #11's audit id.
The design changed twice on the wire before it worked: see `docs/JOURNEY.md` M7A and cookbook #39, #40.

**Where it came from.** M5 built the `coyote_kill` scenario and the guarded animal write path, and
deferred the check to "the first phase with a supervisor." M3 built the supervisor. M4 was the
first phase that could run chaos and the supervisor in one tick, and found the check cannot run
in any tick yet.

**What is actually wrong.** Nothing in the tick reads the Care API. The free pass is a sensor
sweep, triage is a sensor truth table, and `herd_health` by design cannot read a sensor. So no
stage ever produces an *animal* finding, `herd_health` is handed nothing, and a dead cow written
into the ranch by chaos is invisible to the monitor. This is not three lines. It is a new free
stage (an animal sweep: recent observations and status changes off the Care and Farm APIs), a
triage category or two for animals, routing to `herd_health`, and an evidence packet shape for a
cow rather than a sensor.

**What it would take.** A scoped phase of its own, or a deliberate addition to M6 or M8. Decide
where it lives before building it. Until then `docs/STATE.md` decision 5 stands: `herd_health`
idle is correct, not a gap.

### 2. A chaos storm front fusing into one shift report. **Closed at M7A, 2026-09-11.**

**What closed it.** Tick H on the test ledger (run `06177b7095cf`): `storm_front` and the coyote kill
opened together, 18 orders across three worlds, and the fused report's `linked` named eleven keys
including `met-tower-wind:high_wind`, `antelope-ridge-fence:fence_down`, and `home-place-temp:freeze_risk`
together, and fused `cow-0777:care_overdue` into the Red Canyon water run. Zero violations. The report
is pinned in `logs/transcripts/06177b7095cf/`.

**Where it came from.** M5 deferred it to M3. M3 verified fusion live, but on natural churn across
three sensing worlds, not on the chaos `storm_front` group. The specific claim that four correlated
faults sharing one `group_id` come out as one fused report, with `linked` naming them together, has
not been run end to end.

**What it would take.** One paid `--once` (or a short loop) with `CHAOS_ENABLED=1` on the test
ledger, the seed advanced to the tick that fires `storm_front`, and a read of `linked` on the
resulting shift report. About $3. Cheap, and worth doing before M7 changes which model fuses.

---

## Needs a yes: Supabase migrations that are designed but not run

### 3. Make the held set survive a restart. **Closed at M6, 2026-09-11.**

**What it was.** At M4 an incident whose agent raised, or whose model call died in transport, was
*held* and re-routed on the next tick, and the held set lived in the loop's memory, so a restart
turned those incidents back into plain `ongoing` rows nobody re-asked about.

**What closed it.** Migration `0005`, `incidents.held_reason`, a nullable text: `run_tick` writes
the reason on every key it holds and nulls it on every key it released, `run_loop` reads the live
held keys before its first tick, and the rail is `test_the_held_set_survives_a_restart`. It went
in as `0005` rather than `0004` because the gate's checkpointer tables took `0004`.

### 10. Two processes append to `audit.jsonl`, and daily rotation could collide. **Closed at M8, 2026-09-11.**

**Where it came from.** M6. The loop writes `proposed` and the CLI writes `decided`, each through
its own `TimedRotatingFileHandler` on the same file. A one-line append is fine in practice and both
halves read back intact on the first live run. A rotation at midnight fired by the long-lived loop
while a CLI decision is mid-write, or the reverse, is the unlikely case nobody has exercised.

**What it would have taken, and what was done.** The M6 note assumed `/ops/gate` would run in the
loop's process and fix this for free. It does not: the API is its own process (`python main.py --api`),
so at M8 it became the second writer this issue is about, and a third once the CLI is counted. So the
receipt moved to a table. Migration `0007` adds `sw_ops.audit_receipts` with primary key
`(audit_id, phase)`; `gate.propose` writes the `proposed` row before the file line and `gate._execute`
writes the `decided` row before its line, both through the checkpointer's own connection
(`ThreadedPostgresSaver.record_receipt`), so the process holding the pause is the process holding the
row. The primary key is the "exactly twice" rail as a constraint: a third receipt for one id cannot be
inserted, and a rail proves it. **`logs/audit.jsonl` is a projection from M8.** A midnight rotation
colliding with a decision loses at worst a projected line, never the receipt. Verified live with a pause
planted by one process and rejected over HTTP by another: both rows in the table, both lines in the file.

**What stays file-only, on purpose.** The chaos guard's `blocked` and `auto_allowed` pairs are written
by `src/tools/chaos.py` through the logger with no ledger connection in hand, so they have no row. They
are one writer (the loop) and one process, which is not the collision this issue was about. See #20.

### 11. The gate's interrupt is verified on `restock_feed`, not `create_observation`. **Closed at M7A, 2026-09-11.**
### 11. The gate's interrupt is verified on `restock_feed`, not `create_observation`. **Closed at M7A, 2026-09-11.**

**What closed it.** `write_paused` on `cow-0905:deceased`, `create_observation`, **audit_id
`968e7f64fb7540dc9adeed547d15e802`**, the note built from the page under `HERD-07` and `observedAt` the
Farm API's own update stamp. Approved at the CLI by scooter after 49s; the note landed on the deployed
Care API as observation `2dd60c7d-b9a2-413d-b17c-3cae46563d64`. Both halves in `logs/audit.jsonl`.

**Where it came from.** The plan's M6 verification says "let a tick pause on a `create_observation`."
That needs `herd_health` to propose one, and `herd_health` is handed nothing because no stage reads
the Care API: issue 1, the coyote gap. M6 did not absorb it. The mechanics were verified with a
planted `restock_feed` on `sw_ops_test` (kill mid-pause, restart, reject, approve) and the
proposal channel was verified live on whatever `water_feed` actually proposed. The care write's
three checks are covered by the planted suite, not by a live pause. When issue 1 closes, run the
`create_observation` pause live and strike this item.

### 4. Make the chaos miss check work across runs

**What it is.** `chaos_event_missed` fires when a fault this run injected heals without its sensor
ever being read. "This run" is the limit: a fault armed by a previous run, or by the CLI, is never
this run's miss to report, because the set of observed event ids lives in memory.

**What it would take.** A `last_observed_at` or `observed_ticks` column on `chaos_events`, written
by the sweep when the overlay applies, read by `expire`. Small. Only matters for demos that span a
restart, so it can wait.

---

## Decisions waiting

### 5. Where the loop runs

**Where it came from.** `docs/Plan.md` says decide after M4, once a tick's real duration is known.
It is known now: a free tick is 3 seconds, a paid tick is 80 to 90 seconds, and the cadence is 300.

**The fork.** A small always-on box (honest answer for "constantly running", trivially matches the
loop as written, exit codes and SIGTERM already mean the right thing) versus a Lambda container on
an EventBridge schedule (cheaper when calm, but the loop's whole design is a long-lived process with
per-upstream backoff and a held set in memory; issues 3 and 4 would have to be done first). I would
take the box. Dockerizing is FUTURE-1 and deferred; a box runs the venv until then.

### 6. A real `ANTHROPIC_API_KEY`

This machine authenticates to Bedrock with session credentials that expire. From M4 an expiry
mid-run is survivable (the incident is held, the model backs off) but a multi-day run still wants a
key that does not expire. Nothing to build, one value to set. From M7 the rate is `routing.PRICE_TABLE`
per model; the first-party row is the list price and the Bedrock row is assumed equal (#14).

### 7. `CHAOS_ENABLED=1` in `.env`

It was at 1 on this machine through M4 and caused `docs/cookbook.md` #26 (the test suite reading
Supabase). The tests now disarm it themselves and the paid run set it to 0 explicitly, but the
value in `.env` is still 1. Set it to 0 when a demo is not being driven, and treat "what is armed
on this machine" as part of the pre-run checklist.

### 8. Whether `--once` should honour the debounce

Since migration `0003` a single `--once` against a fresh ledger opens nothing: everything it sees
is pending until a second run. That is correct for the loop and surprising at a keyboard. The knob
exists (`INCIDENT_CONFIRM_SWEEPS=1` for a demo), so this is a documentation and expectation
question, not a code one. Decide whether `--once` should default to first sight, and if so say it
in one place.

---

## Owed from M7

### 12. The forecast on the feed page, so `insufficient_information` stops being the honest answer

**Where it came from.** The M7 measurement (`docs/model-routing.md`, the M7 row). FEED-02 says
weather is what turns a bin at reserve from routine into urgent, and asks the model to say which.
The feed packet carries the bins and nothing about weather. The local model set
`insufficient_information` on 4 of 5 feed calls and 5 of 6 Tier-1 candidates across the three ticks,
and Opus on the same pages wrote "no forecast on this page, treat as scheduled and confirm before
you commit the truck." Both were right. The page is what is wrong.

**What it would take.** `evidence.assemble` already reads the whole sweep. The ranch has
`wind-speed`, `temperature`, and `snow-depth` sensors; the packet for a `feed_low` or `deep_snow`
incident could carry the current readings from the weather station (or the nearest of each type)
under a "weather now" heading, at no extra HTTP. There is no forecast upstream, so it is conditions,
not a forecast; the SOP wording may want to say "current conditions" once that is true. Then re-run
the M7 measurement: three ticks, the same columns. This is the change that would earn the cascade
its row, and it is `evidence.py`'s, not the model's.

### 13. `THINKING_BUDGET` sends `budget_tokens`, which Claude Opus 5 rejects

Found while loading the pricing at M7. `call_tier2` maps `reasoning_effort` low/medium/high to a
`thinking: {type: "enabled", budget_tokens: N}` block, and the current API returns 400 for
`budget_tokens` on Opus 5; adaptive thinking (`{type: "adaptive"}` plus `output_config.effort`) is
the replacement. Nothing calls anything but `"none"`, and `"none"` omits the block, so every
measured call was unaffected (64 real calls, zero thinking blocks returned). One function to change
when a job first wants thinking on; not touched at M7 because no job did.

### 14. The Bedrock price row is assumed, not read

`routing.PRICE_TABLE` prices `us.anthropic.claude-opus-5` at the first-party list rate because the
public Bedrock pricing page did not render an Opus 5 row when checked. Verify against the AWS bill
for the M7 run (about $0.75 of Opus on 2026-09-11) and correct the one row if it differs.

### 15. The M6 live pause is still owed. **Closed at M7A, 2026-09-11**, with #11.

Same pause, same audit id. Worth keeping the finding beside it: Opus proposed **nothing** on seven herd
orders while the SOP forbade fabricating an observation, exactly as it proposed nothing on M6's and
M7's feed pages. The prompt was not steered; `HERD-07` names a legitimate write and the model took it
on the first tick it was offered. The gate has never paused on a proposal the SOP did not sanction.

Carried from M6. Three more paid ticks at M7 and the local model proposed writes readily
(`restock_feed` with `quantity: "unknown"`, tools outside its slice), every one stripped by the
shape and tool checks or escalated before the gate; Opus proposed nothing. The first real
`write_paused` remains the verification, and `create_observation` still waits on #1.

---

## Owed from M7A

### 16. A `high` observation on an `active` animal with no care task is invisible

**Where it came from.** The herd sweep reads observations only for the changed set (non-active status,
pending care task, live incident), because observations list per animal only and 1,195 reads a tick is
344k Care requests a day. So a `high` `injury` note written against a healthy-status cow with no task
opens nothing until something else puts her in the changed set. The coyote kill is caught through the
status; a "cow down, still active" note is not.

**What it would take.** A rotating slice (read 1/N of the herd's observations per tick, so every animal
is read every N ticks), or a `high` note on the Care API becoming a care task upstream, which is a
conversation with the frozen repo. Neither is V1.

### 17. A deceased PATCH nulls the animal's pasture, and restore does not put it back

**Where it came from.** The first live kill. The Farm API clears `pastureId` when an animal is patched
to `deceased`, so the dead cow's packet says "pasture unrecorded" and her herd-mates are unknown to it;
the deceased order had to ask the GM where she was found. `chaos restore` PATCHes the status back and
leaves her pastureless, and the chaos kill leaves a `high` `injury` note inside the 24-hour window, so a
restored cohort animal reads as `observation_high` critical for a day (`cow-0905:observation_high` went
pending on the tick after restore, correctly).

**What it would take.** The kill event could record the pasture and `restore` could `assign_to_pasture`
it back: one more real write through the guard, in `chaos.py`. The observation is append-only upstream
and stays; the window handles it after a day. Decide whether the demo wants the cohort to survive a kill
intact before building it.

### 18. `sensor_not_named` grades the animal id on a herd order

The rail is right and the code is wrong for a cow. Kept because docs, transcripts, and the ledger name it.
Rename to `subject_not_named` when the M8 API decides what it shows a human, so it is renamed once.

### 19. Care task titles carry em dashes, from the upstream

"Recheck pinkeye eye — remove patch" is the Farm's seed data, so a triage summary quoting the title
carries the dash and so does the herd page. Data, not our prose; the em-dash rail is on the SOPs and
the briefs and stays there.

## Owed from M8

### 20. The chaos guard's audit receipts are file-only

**Where it came from.** M8 moved the gate's receipts into `sw_ops.audit_receipts` and made `audit.jsonl`
a projection (#10). Chaos's `blocked` / `auto_allowed` pairs (`docs/logging.md`) still go only to the
file: `fire_animal_events` has no ledger session in hand and the writer is `logger.py`, which knows no
database. So the table holds every receipt the **gate** wrote and none the chaos guard wrote, and a
reader of the table would not see that a scenario's animal write was refused.

**What it would take.** `chaos.fire_animal_events` takes the session `run_tick` already has open, and
writes the pair through a small `memory.insert_receipt(session, row)` beside its two log lines. One
afternoon. M9's window shows the gate's receipts (the decided envelope) and not the chaos guard's, so
this did not block it; still worth doing before a reader of the table is told it holds every receipt.

## Owed from M9

### 21. The ranch map has no source. **Needs a yes: migration `0008`, a catalog snapshot in `sw_ops`.**

**Where it came from.** `docs/Plan.md`'s M9 paragraph said the map needs no new backend work because
sensor coordinates are already in the catalog. They are, and the catalog is `ranch://sensors/map`,
which is the ranch. The read API never calls the ranch and neither does a browser, so the window has
no source for a coordinate. M9 ships the map panel as a placeholder that says exactly this, rather
than a panel that quietly calls the Sensor API from a browser. One thing talks to the ranch.

**What it would take.** Three small pieces, one migration, one yes.

1. The migration. Proposed DDL, to be applied to Supabase only with Scott's explicit yes:

```sql
CREATE TABLE sw_ops.catalog_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT        NOT NULL,
    tick          INTEGER     NOT NULL,
    at            TIMESTAMPTZ NOT NULL,
    source        TEXT        NOT NULL,          -- 'mcp_resource' or 'sensor_api', the same catalog_source the tick line carries
    sensor_count  INTEGER     NOT NULL,
    digest        TEXT        NOT NULL,          -- sha256 of the canonical JSON, so an unchanged catalog writes no row
    sensors       JSONB       NOT NULL           -- [{sensor_id, sensor_type, location, unit, lat, lon}], the catalog as read
);
CREATE UNIQUE INDEX uq_catalog_snapshots_digest ON sw_ops.catalog_snapshots (digest);
CREATE INDEX ix_catalog_snapshots_at ON sw_ops.catalog_snapshots (at DESC);
```

2. The loop. `run_tick` already reads the catalog every tick (`catalog_source` on the line). After
`log_tick`, best-effort like `ticks` and `shift_reports`: hash it, `INSERT ... ON CONFLICT (digest) DO
NOTHING`. A ranch that does not change writes one row per process lifetime, not one per tick.
3. The API. `GET /ops/catalog`, the latest snapshot in the envelope, 404 `CATALOG_NOT_FOUND` on an empty
ledger. A seventh route, asked for here the way the sixth was asked for at M8, and `src/api/CLAUDE.md`
edited first. Then the window draws from it and the placeholder comes down.

About a day, plus the yes. The coordinates' field names in `sensors` are whatever the catalog resource
carries and are confirmed on the wire when the snapshot is first written, not copied from the other
repo's source.

### 22. A tool the ranch does not have is reported as an outage

**Where it came from.** M9's first browser approve was on a planted pause naming `add_care_note`, a
tool that is not one of the 19, planted on purpose so an approve could not write. The ranch answered
`McpError: Tool add_care_note not found`, and the receipt says `transport_McpUnavailableError`, because
`ranch_session` wraps everything raised inside it as an MCP outage. M6 hit the same wrapping one layer
up (`WriteGateError` came back as an outage) and moved the belt check ahead of the session; this is the
next layer down. A missing tool is a refusal, not an outage, and a `transport_*` result reads as "try
again later" when the honest reading is "never".

**What it would take.** In `gate.perform_write`, before the session opens: `assert tool in
DEPLOYED_TOOLS` and return a `refused_unknown_tool` result, the way `assert_callable` already refuses a
tool outside the allowlist. Ten lines and a rail. Not done at M9 because the Python gate was frozen for
the phase by design; the first thing to do when it is next open.

### 23. The Vercel deploy, and "three ticks land without a refresh at the Vercel URL"

**Where it came from.** `docs/Plan.md`'s M9 verification item 7. Vercel cannot reach `127.0.0.1`, and
where the loop and the API run is #5, a decision and not this phase's. The window is built and verified
against `next dev` on this machine with the API local. Not faked with a tunnel.

**What it would take.** #5 landing on a host Vercel can reach, `OPS_API_URL` and `OPS_API_TOKEN` set
server-side in the Vercel project, `API_CORS_ORIGINS` untouched (same-origin proxy, no CORS), and one
browser tab left open for three cadences. `web/README.md`, "Deploying".

## Known artifacts, not fixable from here

### 9. One orphan observation on the deployed Care API

Observation `0328d7e2-d271-4410-907c-a84020c2c8c7` against the nonexistent animal
`zz-does-not-exist-0000`, created at M5 by a contract probe that returned 201 where 422 was
expected. Observations are append-only upstream and that repo is frozen, so it stays. Invisible to
every real animal. Written down so it is a known artifact, not a mystery.

### 10. The ranch's dice

The deployed Sensor API invents a fresh, unanchored reading on every call, and its history endpoint
is synthesized independently of its latest-reading endpoint. This is the root of the storm-tick
bill (fixed on our side by the debounce) and of the evidence packet labelling history as "shape and
trend only." It is the upstream's nature, not a bug to file, but every future measurement has to be
read knowing it.

---

## Cookbook candidates never paid for

Both paid for now: why a gate must outlive its process (M6) and the `num_ctx` trap (M7, #35, paid
for by measuring rather than by tripping). The list at the bottom of `docs/cookbook.md` is empty.

---

## By design, not open. Easy to mistake for gaps

- `herd_health` owns animals and nothing else (decision 5, rewritten at M7A): the four animal categories and never a sensor incident. `chaos` has zero tools and no brief.
- The herd stage never fails the tick. `herd_error` on the line and zero animals answered is the failure mode, by design.
- `GATE_LANDED` is True from M6, and that made writes **proposable**, not callable. A model never
  calls a write tool; a human performs an approved proposal through the gate CLI.
- Tier 1 exists from M7 and ships **off** (`TIER1_ENABLED=0`): every model job is Opus until a ledger row says otherwise. That is the measured result, not a stub.
- A calm tick costs exactly $0.00, and `synthesize` assembles in code below two worlds.
- The loop's held set and miss check being in-process is a scoped choice (issues 3 and 4), not an
  oversight.
- The window's browser never calls the API directly and never sees `OPS_API_TOKEN`; every call goes
  through its own route handlers. Same origin, so `API_CORS_ORIGINS` staying empty is correct, not a gap.
- An approve in the window performs the write on the deployed ranch. That is the gate doing its job,
  not a missing belt; the window asks once before sending it. A demo that wants to exercise approve
  plants a tool the ranch does not have (cookbook #47).
