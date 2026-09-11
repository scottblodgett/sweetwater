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
triage                 free   code owns severity
reconcile into sw_ops  free   opened / ongoing / resolved
route new incidents    free
  -> evidence          SPEND  HTTP, not tokens: ~4 extra calls per newly-opened incident
  -> fan out           SPEND  only newly-opened incidents reach a model
synthesize             maybe  one call, or zero. Runs on every tick, see below
gate on writes         pause  M6
```

Five stages cost zero tokens. That is not an optimization, it is the architecture: 160
sensors are far too many to hand a model every tick, and a model asked to find the problem
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

**Every read failing is the Sensor API being down, and it fails the sweep.** One dark sensor is
a per-sensor `SweepError` and the sweep carries on; all of them dark is an outage, and without
the raise the tick is green with zero readings and the loop reads 160 connection errors every
tick at cadence forever, which is the opposite of backing off.

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
In-process only for now; the durable version is a column on `incidents`, which is a Supabase
migration and Scott's explicit yes.

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
the owed `herd_health` verification and its own scoped item.

## Escalation (see also `src/models/CLAUDE.md`)

**Not built, and deliberately so. Through M6 every job is Tier 2.** There is no cascade,
no local fallback, and no retry that hides a bad response, because M7 moves jobs down one
at a time with a rail and a ledger row in `docs/model-routing.md`. Anything earlier is a
cost optimization nobody measured. What exists now is `escalate` / `escalate_reason` on the
`WorkOrder`: the model may say a human is needed, which is a different question from which
tier answered.

The design below is what M7 builds against. Escalate to Tier 2 when **any** of these
holds, and log which one fired:

- the Tier-1 judge returned `insufficient_information`
- `triage.py` marked the incident **critical**
- two or more sensing worlds opened incidents in the same tick (the storm front, which
  is the whole reason a supervisor exists rather than four independent scripts). M3 already
  reuses this exact condition as `FUSION_THRESHOLD`, the spend gate on the shift report, which
  is not a coincidence: it is the same claim about when cross-domain reasoning is worth paying
  for, and M7 should read one constant rather than agree with itself twice
- a `Finding` proposes a write

**The rail: a Tier-1 model may never produce an all-clear.** Code already flagged the
incident, so "nothing is wrong here" from the cheap judge is a contradiction, not a
finding. Reject it and escalate. Do not relax this to make a test pass.

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

`memory.py` owns `sw_ops` and nothing else. Incidents are keyed on **sensor plus
category**, so the same tank going dry twice in one afternoon is one incident.

## Gates outlive the process

`interrupt()` plus the LangGraph Postgres checkpointer, so a pause survives a restart.
A human gate that evaporates when the process dies is not a gate, it is a delay.
