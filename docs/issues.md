# Issues

Open items observed across M0 through M5, written at the M4 boundary on 2026-09-10 for Scott to
come back to after M7. Nothing here is a failing gate. These are deferred verifications, owed
pieces, decisions waiting on a person, and known artifacts. Each one says where it came from and
what it would take, so it can be picked up cold.

Plain-language rule for reading this: **"owed" means we said we would prove it and have not;
"needs a yes" means the code is ready but the change touches Supabase or the plan's order;
"decision" means there is a fork and nobody has picked.**

---

## Owed verifications

### 1. `herd_health` discovering the coyote kill through its own tools

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

**Where it came from.** M5 deferred it to M3. M3 verified fusion live, but on natural churn across
three sensing worlds, not on the chaos `storm_front` group. The specific claim that four correlated
faults sharing one `group_id` come out as one fused report, with `linked` naming them together, has
not been run end to end.

**What it would take.** One paid `--once` (or a short loop) with `CHAOS_ENABLED=1` on the test
ledger, the seed advanced to the tick that fires `storm_front`, and a read of `linked` on the
resulting shift report. About $3. Cheap, and worth doing before M7 changes which model fuses.

---

## Needs a yes: Supabase migrations that are designed but not run

### 3. Make the held set survive a restart

**What it is.** At M4 an incident whose agent raised, or whose model call died in transport, is
*held* and re-routed on the next tick. The held set lives in the loop's memory. Restart the process
and those incidents come back as plain `ongoing`, which means they are never re-routed and never
get a work order.

**What it would take.** One nullable column on `incidents` (a `held_reason` text, or a boolean),
written where `run_tick` computes `state.held`, read where the next tick builds its re-route list.
Migration `0004`. Small. Wants a rail that a held incident is still held after a fresh process
starts against the same ledger.

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
key that does not expire. Nothing to build, one value to set. Also decide whether the assumed rate
in `llm_client.ASSUMED_RATE_USD_PER_M` matches whatever contract the key is on.

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

Listed at the bottom of `docs/cookbook.md` since M2: the `num_ctx` shim trap (M7 territory) and why
a gate must outlive its process (M6 territory). Both will get their entry when the phase that pays
for them lands.

---

## By design, not open. Easy to mistake for gaps

- `herd_health` gets no sensor incidents (decision 5). `chaos` has zero tools and no brief.
- `GATE_LANDED` is False and no write tool reaches a model until M6.
- Tier 1 does not exist until M7; every model job is Opus.
- A calm tick costs exactly $0.00, and `synthesize` assembles in code below two worlds.
- The loop's held set and miss check being in-process is a scoped choice (issues 3 and 4), not an
  oversight.
