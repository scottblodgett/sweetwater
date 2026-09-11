# src/agent - the graph, the loop, the state

## The tick contract

A tick is the unit of work. Everything about the design follows from one rule:

> **Every tick writes exactly one line to `logs/tick.jsonl`, including when it fails.**

A tick that produces no line is indistinguishable from a dead loop, and at 2am that
distinction is the entire product. On failure the line carries `error` and
`failed_stage`, and `failed_stage` is set **before** each stage is attempted, never
after. A line reading `failed_stage: null` next to an error tells you a tick died
without telling you where, which is the one question the field exists to answer.

A tick also survives one sub-agent raising, at both levels: one packet failing inside an agent,
and a whole agent failing inside the fan-out. Either way the incidents it was carrying come back
as `no_answer` orders rather than disappearing, because an incident nobody was told about is
indistinguishable from a ranch with nothing wrong. One agent's bad afternoon is not an outage.

**`AGENT_CONCURRENCY = 4` is a ceiling on Opus calls in flight, not a per-agent budget.**
`fan_out` builds one semaphore and passes it into every `gather_bounded`; four agents each
bounding themselves at four is sixteen concurrent calls, which is a different and much more
expensive statement.

## The order inside a tick, and which parts are free

```
catalog                free   the MCP map, with GET /sensors as fallback
chaos maybe-fires      free   after the catalog, because plan() picks targets from the topology
sweep ~160 sensors     free   bounded by SWEEP_CONCURRENCY
herd                   free   M7A: the Farm list in waves of 6, the care record for the changed set. ~18s. Never fails the tick
triage                 free   code owns severity, for sensors and for animals
reconcile into sw_ops  free   pending / opened / ongoing / resolved / dismissed
route new incidents    free
  -> evidence          SPEND  HTTP, not tokens: ~4 extra calls per newly-opened incident
  -> fan out           SPEND  only newly-opened incidents reach a model
gate on writes         free   a proposed write pauses for a human; the tick does not. M6, see below
synthesize             maybe  one call, or zero. Runs on every tick, see below
```

Six stages cost zero tokens. That is not an optimization, it is the architecture: 160
sensors and 1,195 head are far too many to hand a model every tick, and a model asked to find the problem
will burn its budget navigating. The free pass narrows the ranch to what is actually wrong,
and only then does anything expensive happen.

**`run_tick(spend=False)` skips `evidence` and `fan_out`, then still synthesizes.** The whole
pipeline runs for free, which is how every rail above the model layer exercises it. The flag
defaults to `True` because a default that quietly does nothing is a default that ships; the
matching guard is `conftest.no_model_calls`, so a forgotten flag fails loudly instead of billing.

**`synthesize` is outside the spend guard on purpose, and it is the one stage that decides for
itself.** It runs last on every tick and calls a model only when `spend` is true *and*
`FUSION_THRESHOLD` (2) or more sensing worlds opened incidents. Below that there is nothing to
fuse - one world is a concatenation of length one - so it assembles the page in code for free,
which is most ticks. That means every tick ends with a shift report, including a free-pass tick
and including a tick where the model failed, because the person coming on shift needs a page and
"no page" is indistinguishable from "no tick." The same `assemble_shift_report` is both the
calm-tick answer and the failure fallback, deliberately: a fallback exercised only after
something has already gone wrong is a fallback nobody has read.

A rejected report is **replaced** by the code-assembled one, carrying the violations, never
retried. A retry loop turns a rail into a sampler.

**`evidence` is a separate `failed_stage` from `water_feed`.** They fail for unrelated
reasons - one is the ranch not answering, the other is the model not answering - and a
single marker over both would say a tick died at "the expensive part."

**A persisting fault is `ongoing`, never re-alarmed.** Duplicate alerts train the
client to ignore the service, which is worse than no service at all.

**One bad read is `pending`, not an incident.** The deployed Sensor API invents a fresh
reading on every call, so a healthy tank reads empty one sweep in a while and fine on the next.
`INCIDENT_CONFIRM_SWEEPS` (default 2) is how many consecutive sweeps have to flag a sensor
before it opens; until then the row is `pending`: live in the ledger, covered by the unique
index and the unread guard, but never routed, paged, or billed. A pending row that reads clean is
`dismissed`, never `resolved`, because nothing was alarmed. Unread is not read-clean, for pending
exactly as for opened. The seven permanently-bad sensors and every chaos scenario (minimum TTL
two ticks) still open on their second sweep. Migration `0003`. M4 measured the reason: 10 to 24
opened per tick, almost all of them dice.

**Every read failing is the Sensor API being down, and it fails the sweep.** One dark sensor is
a per-sensor `SweepError` and the sweep carries on; all of them dark is an outage, and without
the raise the tick is green with zero readings and the loop reads 160 connection errors every
tick at cadence forever, which is the opposite of backing off.

**The herd stage is the other way round, on purpose: it never fails the tick.** A Farm or Care
outage is `herd_error` on the line and an empty `answered` set from `sweep_herd`, so no animal
incident resolves, the findings that did come back still open, and the tanks are still watched.
It reads the ledger's live animal subjects first (`memory.live_animal_subjects`) so an animal that
has left every roster (the Farm API nulls a dead cow's pasture) is still in the changed set and
still vouched for by the list. `reconcile` takes `read_subject_ids`: the sensors read plus
`herd.answered`. The stage is not in `STAGE_UPSTREAM`, so it has no backoff; 20 requests against a
sick Farm API every 300s is a cost the heartbeat can carry.

## The loop (M4). `run_loop` in `executor.py`, and the rules it enforces

**The spend ceiling halts.** `SPEND_CEILING_USD` is summed from `cost_usd` on the tick lines
and checked after every tick; reaching it writes `loop_halted` and returns exit **4**. Not a
skipped tick, not a warning. Overshoot is bounded at one tick because a fan-out in flight is
already paid for and cancelling it wastes the tokens. There is no unlimited value: a
non-positive ceiling refuses to start a spending loop. `--once` is exempt, a human is there.

**Cadence is start to start.** The next tick is scheduled from when this one began, so a
90-second storm tick does not push every later tick late; a tick longer than the interval
starts the next at once and logs `tick_overran`. Chaos TTLs are in ticks and are multiplied by
the cadence at injection, so cadence and TTL are one decision and rescale together.

**Backoff is per upstream, never global.** Five keys: `mcp`, `sensor`, `sw_ops`, `evidence`
(Farm, Feed, and Care behind one stage), `model`. Exponential, deterministic (one caller, no
herd to jitter against), 60s base and 900s cap. The loop itself never sleeps past one cadence.
A free-pass upstream inside its window skips the whole free pass **and the tick still writes
its line** naming who is sick; a spend upstream inside its window runs the free pass and holds
the new incidents. The model never raises out of `fan_out`, so its outage is read off the
orders: every order dying in transport marks the model, one real answer clears it.

**A crashed agent resolves nothing, and its incidents are held.** Reconcile runs before any
agent and reads triage's findings, so an agent raising cannot close an incident. What it does
is leave a `no_answer` order on an incident that is `ongoing` next tick and would never be
re-routed. `held` is the fix: keys whose order carried `agent_raised`, `worker_raised`, or
`transport_error` (or whose spend stage was skipped) come out of the tick, go onto the line,
and are re-routed on the next tick until answered or resolved. **A rail rejection is never
held**, and neither is `max_tokens`: one is a sampler, the other buys the same truncation twice.
**Durable from M6** (migration `0005`, `incidents.held_reason`): `run_tick` writes the reason on
every key it holds and nulls it on every key it released, and `run_loop` reads the live held keys
before its first tick, so a restart re-routes what the previous run could not get answered. A
ledger that cannot be read at start begins with an empty set and says so, rather than refusing
to start; the first tick will hit the same ledger and back off properly if it is really down.

**Shutdown drains, on Windows.** `loop.add_signal_handler` raises `NotImplementedError` there
and SIGTERM never arrives, so neither is relied on. Python 3.11's `asyncio.Runner` turns the
first Ctrl+C into a cancel of the main task and the second into `KeyboardInterrupt`. The
in-flight tick runs behind `asyncio.shield`, the cancel lands in the loop, the tick finishes,
the line is written, exit 0. Second Ctrl+C escapes and `main.py` returns 1. SIGTERM and
SIGBREAK go through `signal.signal` and set the stop event, which also wakes the cadence sleep.

**A chaos fault that heals unseen is reported, never silent.** `SweepResult.overlay_observed`
is the event ids whose sensor the sweep read; at heal time an event this run injected that was
never in that set logs `chaos_event_missed` and lands in `chaos_missed` on the line. Animal
events are excluded because nothing observes them yet: no stage reads the Care API, which is
the owed `herd_health` verification and its own scoped item, **M7A**, which adds the herd sweep and puts animal events into the observed set.

## Escalation (see also `src/models/CLAUDE.md`)

**Not built, and deliberately so. Through M6 every job is Tier 2.** There is no cascade,
no local fallback, and no retry that hides a bad response, because M7 moves jobs down one
at a time with a rail and a ledger row in `docs/model-routing.md`. Anything earlier is a
cost optimization nobody measured. What exists now is `escalate` / `escalate_reason` on the
`WorkOrder`: the model may say a human is needed, which is a different question from which
tier answered.

Built at M7 as `routing.tier_for` (before the call) and `routing.escalation_reason` (after it),
inside `workers.judge_packet`. Per incident, escalate to Tier 2 when **any** holds, and the reason
lands on the order as `escalation` and on the tick line in `escalation_reasons`:

- `triage.py` marked the incident **critical** (the one pre-call condition; Tier 1 is never asked)
- the Tier-1 order failed a **blocking rail** (`rejected`)
- the Tier-1 judge returned **`insufficient_information`**, a schema field it may set on purpose
- the Tier-1 order **proposed a write** (only a Tier-2 proposal may reach the gate)
- Tier 1 **never answered** (`no_answer`: transport or truncation; Opus is standing right there)

Escalation is a **rewrite from the identical page**, never a review and never a retry at the same
tier. Opus's order is stored with the Tier-1 attempt on it as `tier1_*`; a Tier-2 rejection is
stored as rejected, as it always was.

**"Two or more sensing worlds" is not on this list, and the plan had it there.** It says nothing
about what one packet contains, and per incident it would send every order on every storm tick to
Opus. What it decides is whether the tick needs someone reading across the ranch, which is
`synthesize` at `FUSION_THRESHOLD`, already Tier 2. One constant, one meaning
(`docs/STATE.md` decision 28).

**The rail: a Tier-1 model may never produce an all-clear.** Code already flagged the
incident, so "nothing is wrong here" from the cheap judge is a contradiction, not a
finding. `all_clear` rejects it and the rejection escalates. Do not relax this to make a test pass.
The cascade ships **off** (`TIER1_ENABLED=0`) after the M7 measurement; the predicate is live code
either way and every path has a planted test.

## The five rails on a work order, and what each one reads

`workers.py::check` runs them all before anything is stored. Blocking ones make the order
`rejected`; the other two are recorded and still ship.

| Rail | Blocks | Reads |
| --- | --- | --- |
| `severity_mismatch` | yes | the echo against `incident.severity`. Triage's value is what gets stored either way |
| `all_clear` | yes | the **actions list**, plus the headline |
| `invented_rule` | yes | `rules_cited` against the rule ids actually in the packet's SOP text |
| `no_payload` / `schema_invalid` | yes | the response never arrived, or arrived unparseable |
| `no_rule_cited` | no | quality, not safety. A truck going to the right tank without a citation is still going to the right tank |
| `sensor_not_named` | no | prose that never names the sensor behind the number |

Three more on `proposed_write`, M6, in `workers.py::check_write_proposal`. **None of them blocks
the work order**: the prose is still a defensible answer about the incident, so it ships, and the
one part of it that would have changed the ranch is stripped and its code recorded. A proposal
that failed a check never reaches the gate. They are the plan's shape / key / grounding, fired one
at a time in that order, so the planted suite can assert which one:

| Rail | Reads |
| --- | --- |
| `write_shape_invalid` | not `{tool, args}`; an argument the tool does not take or a required one missing; a value outside an enum; a timestamp that is not ISO 8601 (`allowlists.WRITE_TOOL_ARGS`, read off the wire) |
| `write_tool_not_allowed` | the tool against `proposable_tools_for(agent)`: this agent's slice, restricted to `WRITE_TOOLS`, and empty while `GATE_LANDED` is False |
| `ungrounded_write_arg` | every `id` and `number` argument against the rendered page. Number tokens compared as numbers, so `16.7` grounds `16.7` and not `6.7`; free-text `note` and `reason` are not graded because they cannot be on the page verbatim |

The key itself is code's: a proposal is born inside the work order for one packet and never names
an incident, so the model has no index to get wrong. `executor._gate_step` re-checks the
code-attached key against the routed set anyway, as `write_key_unknown`, because "cannot happen"
is what a future path that builds a work order by hand will say too.

Two more on the shift report, in `agent.py::check_shift_report`. Both block, and blocking here
means the code-assembled page ships instead:

| Rail | Reads |
| --- | --- |
| `invented_incident` | `linked` against the incident keys the page actually carried. `linked` is the report's causal claim, and code knows exactly what was handed over, so the claim is checkable rather than prose. A report linking an incident that does not exist sends somebody looking for it |
| `all_clear` | the priorities list and the headline, never the `situation` prose, for the same reason the work-order rail reads actions |

**Neither of those is `_ALL_CLEAR` from `workers.py`, and the two regexes are deliberately not
shared.** One is about a sensor ("no problem here"), the other about a whole ranch ("quiet
night"), and a shared pattern means widening one widens the other by accident.

**The all-clear rail reads the actions list, not the prose, and that is load-bearing.**
"The second tank at 29.7 gal is fine, so this is the float and not the pasture" is a
correct, useful sentence and the actual diagnosis. A prose matcher rejects it. A rail that
punishes accurate writing gets switched off inside a week, so the rail asks the only
question that cannot be argued with: does this order tell somebody to do something.

## State

`state.py` holds `RanchState` (the LangGraph channel), `Finding`, `Incident`, `WorkOrder`, and
`ShiftReport`. `Finding` is triage's output, code-owned end to end. **`WorkOrder` is the handoff
contract between a sub-agent and the supervisor:** a sub-agent inherits nothing and the supervisor
inherits nothing back, so anything the supervisor needs must be written in the order rather than
assumed to be in shared context. `render_shift_page` is the whole of what it gets.

That claim is not asserted, it is measured. `docs/no-brief-transcript.md` and
`docs/with-brief-transcript.md` are the same model on the same packet with one brief removed, and
the unbriefed answer passes every rail while naming no neighbour at all.

`WorkOrder` is split down the middle on purpose: everything above `status` is the model's
prose, everything from `status` down is code's verdict on it, and `severity` is triage's
rather than the model's echo. The echo is kept in its own field so a disagreement is
recorded rather than smoothed over.

`memory.py` owns `sw_ops` and nothing else. Incidents are keyed on **subject plus
category** (`subject_id` / `subject_type` since migration `0006`; `subject_type` is the sensor type
or the literal `animal`), so the same tank going dry twice in one afternoon is one incident, and so
is the same cow reading deceased on every sweep until somebody restores her.

## The gate (M6): a proposed write pauses for a human, and the pause outlives the process

A human gate that evaporates when the process dies is not a gate, it is a delay. `gate.py`:

    propose  ->  [ ask: interrupt() ]  ...hours, a restart...  ->  [ execute ]  ->  END

**One tiny LangGraph per proposal, checkpointed in Postgres.** `ask` calls `interrupt()` with the
proposal and stops; the LangGraph Postgres checkpointer writes the paused graph into `sw_ops`
(migration `0004`, the library's own four tables); the tick moves on. A human runs
`python -m src.agent.gate list | approve | reject`, which resumes that one graph with
`Command(resume=...)`, and `execute` performs the write or records the refusal and writes the
`decided` audit line. The whole tick is **not** a graph and does not pause: one waiting proposal
must not stop the ranch being watched, and `interrupt()` blocks the graph it is in, which is why
the graph is per proposal and the loop stays hand-wired.

**Code owns everything but the pause.** The three checks run before a proposal gets near the gate,
the incident key is code's, the audit lines are code's, the duplicate suppression is code's, and
the decision is a person's. LangGraph holds the pause and decides nothing.

Rules enforced in `gate.py` and `executor._gate_step`, each because a spike or a rail found the
other way wrong:

- **Nothing with a side effect runs before `interrupt()`.** A node restarts from its first line on
  resume; the first spike measured `ask` running twice. The `proposed` line is written by
  `propose()` outside the graph.
- **The same write for the same incident is asked once.** A held incident is re-judged every tick
  until answered, so each judgment could propose the same restock again. `propose()` looks for a
  pause on the same `incident_key` and `tool` first and suppresses the duplicate, with no audit
  line, because it is not a new side effect. The tick line counts it as `writes_duplicate`.
- **The gate never fails the tick.** A checkpointer that cannot be opened counts each proposal as
  `writes_failed`, holds the incident with reason `gate_unavailable` so the next tick re-proposes
  it, and the shift page still ships. A proposal whose pause could not be written after its
  `proposed` line was logged is completed with `decision="dropped"`, `decided_by="gate"`, so a
  dangling `proposed` keeps its one meaning: a pause nobody has answered.
- **A write is performed by exactly one path.** `perform_write` checks `assert_callable` with the
  `Approval` before it opens an MCP session, because `ranch_session` wraps anything raised inside
  it as an outage and a refused write is not an outage. The result is written whatever the wire
  did, and never retried.
- **A second answer is refused, not absorbed.** A resume on a finished thread is a silent no-op in
  LangGraph that looks exactly like a decision, so `decide()` checks the thread is actually sitting
  on an interrupt first and raises `GateError` naming the earlier decision.
- **The checkpointer is the sync saver in a worker thread**, `memory.ThreadedPostgresSaver`,
  because psycopg's async connection refuses Windows' default event loop and the sync saver's own
  async methods raise `NotImplementedError`. One lock, because a psycopg connection is not safe for
  concurrent use. Its tables are created by alembic from the library's own `MIGRATIONS` list, never
  by `setup()`, and `memory.checkpointer()` refuses a database that is behind the installed library.

The audit rail: every `audit_id` in `audit.jsonl` appears exactly twice, **or once while its pause
is still open**, and `gate.unpaired_audit_ids` minus `gate.pending` must be empty. From M8 the same
pair is a row in `sw_ops.audit_receipts` first, written through the checkpointer's connection
(`gate.record_receipt`), and the primary key `(audit_id, phase)` makes the rail a constraint; the file
is the projection (`docs/issues.md` #10). A console line about a receipt must not carry both `audit_id`
and `phase`, or the rail counts it as one: `audit_receipt_failed` says `receipt_phase` for that reason.

From M8 the tick's last act is `_record_tick`: the tick line as a row in `sw_ops.ticks` and the shift
report as a row in `sw_ops.shift_reports`, so the read API in another process can show them. Best-effort,
after the log line, never failing the tick: a ledger that cannot take the row is `tick_row_failed` on the
console and the line in `logs/tick.jsonl` remains the heartbeat.
