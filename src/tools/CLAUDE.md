# src/tools - the hands

## The upstream is frozen

The MCP server and the four REST APIs are deployed and owned by another repo. **19
flat tool names**, no namespaces. Consequences that are easy to get wrong:

- An allowlist is an **explicit set of literal names**, never a prefix match.
- `ranch://sensors/map` is a **resource, not a tool**. LangChain's adapter surfaces
  tools only, so the map must be read explicitly with `read_resource`. An agent that
  "should have the map" and does not is almost always this.
- `GET /sensors` takes **pagination only** - there is no `type` filter. So the catalog
  is pulled once at `?limit=500` and filtered in Python. This is not a workaround; the
  map-not-paging pattern is the right shape anyway and arrives here by necessity.
- `GET /sensors/:id` **synthesizes a fresh value on every call**, unanchored to the
  previous one. Never cache it, never prefetch it, and never expect two reads a second
  apart to agree.
- `GET /sensors/:id/readings` is synthesized the same way and **does not contain the value
  the sweep read**, even when its newest point carries a later timestamp. M2 printed a
  packet where triage judged 3.4 gal and the newest history point said 0.8 gal at a later
  time. Both are honest; they are two independent draws. The packet therefore labels the
  series as shape and trend only, and says in one line that it is not the current reading,
  because a model handed two contradictory numbers with no note will pick one.
- `/animals` and `/pastures` are on the **Farm API**, not the Care API. Easy to get
  backwards, and the wrong base URL 404s rather than erroring in a way that names itself.
- A Lambda Function URL routes **every path** to the same handler, so appending a
  bogus path to `MCP_URL` does not produce a failure. Test failure paths with an
  unreachable host instead.

## allowlists.py, and the gate that does not exist yet

The counts are the spec: **7 / 6 / 5 / 5 / 0**, and a test asserts each one exactly, so a
slice cannot grow by one tool without a deliberate edit to a number a human reads. If a
number here needs changing, change the number, not the test.

**Three agents share the three sensor read tools, and that is not carved up.** The isolation
that matters is the brief, the SOP set, and which sensor types reach each agent; tool-name
exclusivity would mean editing a frozen server. `herd_health` has no sensor reads at all,
which is design and not omission: it cannot see a sensor, so no sensor incident can route to
it (`src/agent/agent.py`).

**No write tool reaches a model before M6.** Three functions, and none of them is redundant:

- `tools_for(agent)` is the **declaration**. It includes the writes, so the counts the tests
  assert are the real counts. A declared-but-withheld tool is a documented seam.
- `bound_tools_for(agent)` is what a model may be handed, and it subtracts `WRITE_TOOLS`
  while `GATE_LANDED` is False. It logs `write_tools_withheld` when it removes something.
- `assert_callable(tool)` is the belt behind that filter, raising `WriteGateError` from inside
  `mcp_client.call_tool`. **This one covers us, not the model.** The next person to write a
  helper that calls `consume_feed` directly is not a model.

`WRITE_TOOLS` names all **eight** deployed writes, not the four that appear in a slice. The
four placement tools are in no slice and belong in none: moving an animal between places is a
crew decision, not an inference from a sensor. They are named anyway, because the unassigned
ones are exactly what somebody reaches for later while chasing one read out of the same API.

**`GATE_LANDED` is a boolean in one module, and M6 is the only thing that may flip it.** Not a
config value: an env var is something somebody sets on a laptop at 11pm to make a demo work.
The gate is `interrupt()` plus the LangGraph Postgres checkpointer, so a pause outlives the
process.

`DEPLOYED_TOOLS` is all 19, **read off the wire and never copied out of the upstream's
source.** `--handshake` compares the live list against it and fails on drift in either
direction. That check lives in the handshake rather than in `pytest` because `pytest` has to
pass on a plane. Agent names here are literals rather than imported from `src/agent/agent.py`,
because `mcp_client` imports this module and the cycle back is real; a test asserts `SLICES`
covers exactly `agent.AGENTS`, so the rail is the sync mechanism.

## Severity belongs to triage.py

`triage.py` decides severity, in code, from per-type thresholds. No model, at any
tier, ever assigns it. A model handed a verdict and asked to justify it fabricates the
justification, measured at 94/97 correct down to 2/100 in a previous life of this
project. A sub-agent may **echo** severity and must never author it.

Every critical-capable type gets a **warning tier** between nominal and critical, and
an unrecognized sensor type trips a `warn_once` rather than falling through a default
branch. A new sensor type silently reading as nominal is the failure mode here.

## evidence.py buys HTTP to avoid tokens, and the trade is measured

One packet is the incident, that sensor's recent history, its siblings at the same
location, the animals in that pasture, and the whole relevant SOP. That is roughly **four
extra HTTP calls per newly-opened incident**, which on a first tick against a quiet ledger
means 9 to 11 packets and ~40 extra requests, all bounded by `SWEEP_CONCURRENCY` and lost
in the noise of a 160-sensor sweep.

What it buys: the model judges **one page** and drives no tool loop. Measured on the Alkali
Flat tank, **5,555 input tokens per packet, of which the SOP is the majority** and the
evidence itself is a few hundred. So the lever on cost is the SOP file, not the evidence,
which is the opposite of what it looks like before you print one.

**Match a pasture by id, never by display name.** The sensor map says
`"Alkali Flat (alkali-flat)"` where REST says `"Alkali Flat"`, and the Farm API's pasture
is `East BLM Allotment` where the sensor's location is `East Allotment`. A name match
returns zero refs and reads exactly like a pasture with no animals in it.

**An absence is written as a sentence, never as a zero or an omission.** "No animal roster
returned for this pasture" and "0 head" are different facts, and only one of them is true.

## Bounded fan-out, always

160 unbounded requests against API Gateway is a wall of Lambda cold starts and reads
to the other side as a load test. Use `gather_bounded(..., limit=SWEEP_CONCURRENCY)`.
Backoff is jittered because on a shared outage 160 unjittered retries rebuild the exact
thundering herd the backoff was added to prevent.

## Errors are data, not exceptions

`call_tool` returns the upstream's structured error envelope (`category`, `retriable`,
`retryAfterMs`) **as a value**. Do not raise it, and do not retry inside the tool
layer. Classification belongs to whoever saw the status line; **retry policy belongs
to the caller**, because attempts, budget, and deadline are the caller's to spend.
Collapsing the two produces the canonical bug: a helper that retries a 422 forever and
cannot be told a budget by the only process that has one.

One hole is known and deliberate: an upstream fault that returns **HTTP 200 with a
structurally perfect empty envelope** is undetectable by any retry layer. The defense
is a plausibility check on content plus a second source of truth (the map), not a
better classifier.

## chaos.py: two injection paths, and the asymmetry is forced

**Sensor faults are an overlay in `sw_ops.chaos_events`,** applied by `sensors.py` on
top of the honest live read before triage sees it. The deployed Sensor API is stateless
and DB-free, and its own fault injector deliberately refuses to arm when
`AWS_LAMBDA_FUNCTION_NAME` is set so deployed prod can never be faulted. That guard is
correct and stays. **The deployed API stays truthful; this repo owns the lie, in one
place, under test.**

**Animal events are written for real** via `PATCH /animals/:id` and
`POST /animals/:id/observations`, so `herd_health` finds them through its real tools
with no overlay at all. Two guards, both load-bearing: `CHAOS_ALLOW_WRITES` (off by
default) and `CHAOS_ANIMAL_COHORT`, which confines every mutation to a named handful of
animals so the rest of the herd stays pristine for other demos.

**That write spans two APIs, and it is the trap at the top of this file again.** The
`PATCH` is on **`FARM_API`** and only the observation is on **`CARE_API`**. M5 shipped both
against `CARE_API` and every rail passed, because a respx mock answers whatever host it is
pointed at. Read off the deployed services on 2026-09-10:

| Call | Base | Body |
| --- | --- | --- |
| `PATCH /animals/:animalId` | `FARM_API` | `status`, one of `active` \| `inactive` \| `sold` \| `deceased` |
| `POST /animals/:animalId/observations` | `CARE_API` | `type` (`behavior` \| `appetite` \| `mobility` \| `appearance` \| `injury` \| `general`), `severity` (`low` \| `medium` \| `high`), `note`, `observedAt` - **all four required** |

The field is **`note`, not `notes`**, there is no `observedBy` (it is accepted and dropped),
and `healthy` is not a status, so a reset goes to `active`. All three enums are validated in
`parse_catalog` at load, so a typo is a load-time error rather than a 422 nobody is watching
for mid-demo. **Learn a shape like this by sending a deliberately invalid body to a
nonexistent id and reading the 422** - it names the enum, mutates nothing, and does not
require reading the frozen upstream's source.

**Chaos is seeded and it heals.** A `random.Random(CHAOS_SEED)` picks scenario,
targets, and timing, so a seed replays a demo. Chaos that cannot be reproduced is a
flake, not a fixture. Every event carries a TTL, and expiry is what produces `resolved`
incidents; without healing everything is broken an hour in and the feed goes quiet.

The PRNG picks **what breaks**. A model may only author the observation prose a human
reads. Same ownership rule as severity.
