# src/agent - the graph, the loop, the state

## The tick contract

A tick is the unit of work. Everything about the design follows from one rule:

> **Every tick writes exactly one line to `logs/tick.jsonl`, including when it fails.**

A tick that produces no line is indistinguishable from a dead loop, and at 2am that
distinction is the entire product. On failure the line carries `error` and
`failed_stage`, and `failed_stage` is set **before** each stage is attempted, never
after. A line reading `failed_stage: null` next to an error tells you a tick died
without telling you where, which is the one question the field exists to answer.

A tick also survives one sub-agent raising. One agent's bad afternoon is not an
outage.

## The order inside a tick, and which parts are free

```
chaos maybe-fires      free
catalog                free   the MCP map, with GET /sensors as fallback
sweep ~160 sensors     free   bounded by SWEEP_CONCURRENCY
triage                 free   code owns severity
reconcile into sw_ops  free   opened / ongoing / resolved
route new incidents    free
  -> evidence          SPEND  HTTP, not tokens: ~4 extra calls per newly-opened incident
  -> fan out           SPEND  only newly-opened incidents reach a model
  -> synthesize        SPEND  M3
gate on writes         pause  M6
```

Five stages cost zero tokens. That is not an optimization, it is the architecture: 160
sensors are far too many to hand a model every tick, and a model asked to find the problem
will burn its budget navigating. The free pass narrows the ranch to what is actually wrong,
and only then does anything expensive happen.

**`run_tick(spend=False)` stops at the end of `route`.** The whole pipeline then runs for
free, which is how every rail above the model layer exercises it. The flag defaults to
`True` because a default that quietly does nothing is a default that ships; the matching
guard is `conftest.no_model_calls`, so a forgotten flag fails loudly instead of billing.

**`evidence` is a separate `failed_stage` from `water_feed`.** They fail for unrelated
reasons - one is the ranch not answering, the other is the model not answering - and a
single marker over both would say a tick died at "the expensive part."

**A persisting fault is `ongoing`, never re-alarmed.** Duplicate alerts train the
client to ignore the service, which is worse than no service at all.

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
  is the whole reason a supervisor exists rather than four independent scripts)
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

**The all-clear rail reads the actions list, not the prose, and that is load-bearing.**
"The second tank at 29.7 gal is fine, so this is the float and not the pasture" is a
correct, useful sentence and the actual diagnosis. A prose matcher rejects it. A rail that
punishes accurate writing gets switched off inside a week, so the rail asks the only
question that cannot be argued with: does this order tell somebody to do something.

## State

`state.py` holds `RanchState` (the LangGraph channel), `Finding`, `Incident`, and
`WorkOrder`. `Finding` is the **handoff contract** between a sub-agent and the supervisor:
a sub-agent inherits nothing, so anything the supervisor needs must be in the `Finding`
rather than assumed to be in shared context.

`WorkOrder` is split down the middle on purpose: everything above `status` is the model's
prose, everything from `status` down is code's verdict on it, and `severity` is triage's
rather than the model's echo. The echo is kept in its own field so a disagreement is
recorded rather than smoothed over.

`memory.py` owns `sw_ops` and nothing else. Incidents are keyed on **sensor plus
category**, so the same tank going dry twice in one afternoon is one incident.

## Gates outlive the process

`interrupt()` plus the LangGraph Postgres checkpointer, so a pause survives a restart.
A human gate that evaporates when the process dies is not a gate, it is a delay.
