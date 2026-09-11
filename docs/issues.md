# Issues

Open items observed across M0 through M7, first written at the M4 boundary on 2026-09-10 and
extended at each boundary since. Nothing here is a failing gate. These are deferred verifications, owed
pieces, decisions waiting on a person, and known artifacts. Each one says where it came from and
what it would take, so it can be picked up cold.

Plain-language rule for reading this: **"owed" means we said we would prove it and have not;
"needs a yes" means the code is ready but the change touches Supabase or the plan's order;
"decision" means there is a fork and nobody has picked.**

---

## Owed verifications

### 1. `herd_health` discovering the coyote kill through its own tools

**Scheduled: M7A**, inserted before M8 at the M7 boundary. Design is the M7A paragraph in `docs/Plan.md`. Closes with #2, #11, and #15 in one paid run.

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

### 2. A chaos storm front fusing into one shift report

**Scheduled: M7A.** "Cattle through the gap" is an animal event, so the storm front has both halves only once the herd sweep exists.

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

### 10. Two processes append to `audit.jsonl`, and daily rotation could collide

**Where it came from.** M6. The loop writes `proposed` and the CLI writes `decided`, each through
its own `TimedRotatingFileHandler` on the same file. A one-line append is fine in practice and both
halves read back intact on the first live run. A rotation at midnight fired by the long-lived loop
while a CLI decision is mid-write, or the reverse, is the unlikely case nobody has exercised.

**What it would take.** Either the CLI writes its `decided` line through the loop (M8's `/ops/gate`
gets there for free, since the API is the loop's process), or the receipt moves to a table and the
file becomes a projection of it. Decide at M8, when the second writer becomes the API.

### 11. The gate's interrupt is verified on `restock_feed`, not `create_observation`

**Scheduled: M7A.** The live `create_observation` pause is M7A's headline verification.

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
take the box. M10 dockerizes either way.

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

### 15. The M6 live pause is still owed

**Scheduled: M7A**, with #11.

Carried from M6. Three more paid ticks at M7 and the local model proposed writes readily
(`restock_feed` with `quantity: "unknown"`, tools outside its slice), every one stripped by the
shape and tool checks or escalated before the gate; Opus proposed nothing. The first real
`write_paused` remains the verification, and `create_observation` still waits on #1.

---

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

- `herd_health` gets no sensor incidents (decision 5). `chaos` has zero tools and no brief.
- `GATE_LANDED` is True from M6, and that made writes **proposable**, not callable. A model never
  calls a write tool; a human performs an approved proposal through the gate CLI.
- Tier 1 exists from M7 and ships **off** (`TIER1_ENABLED=0`): every model job is Opus until a ledger row says otherwise. That is the measured result, not a stub.
- A calm tick costs exactly $0.00, and `synthesize` assembles in code below two worlds.
- The loop's held set and miss check being in-process is a scoped choice (issues 3 and 4), not an
  oversight.
