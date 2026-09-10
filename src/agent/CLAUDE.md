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
sweep ~160 sensors     free   bounded by SWEEP_CONCURRENCY
triage                 free   code owns severity
reconcile into sw_ops  free   opened / ongoing / resolved
route new incidents    free
  -> fan out           SPEND  only newly-opened incidents reach a model
  -> synthesize        SPEND
gate on writes         pause
```

Four of the seven stages cost zero tokens. That is not an optimization, it is the
architecture: 160 sensors are far too many to hand a model every tick, and a model
asked to find the problem will burn its budget navigating. The free pass narrows the
ranch to what is actually wrong, and only then does anything expensive happen.

**A persisting fault is `ongoing`, never re-alarmed.** Duplicate alerts train the
client to ignore the service, which is worse than no service at all.

## Escalation (see also `src/models/CLAUDE.md`)

Tier 1 is local and default. Escalate to Tier 2 when **any** of these holds, and log
which one fired:

- the Tier-1 judge returned `insufficient_information`
- `triage.py` marked the incident **critical**
- two or more sensing worlds opened incidents in the same tick (the storm front, which
  is the whole reason a supervisor exists rather than four independent scripts)
- a `Finding` proposes a write

**The rail: a Tier-1 model may never produce an all-clear.** Code already flagged the
incident, so "nothing is wrong here" from the cheap judge is a contradiction, not a
finding. Reject it and escalate. Do not relax this to make a test pass.

## State

`state.py` holds `RanchState` (the LangGraph channel), `Finding`, and `Incident`.
`Finding` is the **handoff contract** between a sub-agent and the supervisor: a
sub-agent inherits nothing, so anything the supervisor needs must be in the `Finding`
rather than assumed to be in shared context.

`memory.py` owns `sw_ops` and nothing else. Incidents are keyed on **sensor plus
category**, so the same tank going dry twice in one afternoon is one incident.

## Gates outlive the process

`interrupt()` plus the LangGraph Postgres checkpointer, so a pause survives a restart.
A human gate that evaporates when the process dies is not a gate, it is a delay.
