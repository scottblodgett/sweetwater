# Model routing: local by default, Opus where it earns it

**This is a ledger, not a plan.** The tier table below is the design; the measurement
log at the bottom is the truth. A job moves down a tier only after a row is added
saying what proved it was safe.

## The rule this all rests on

> **Turning thinking off is free exactly when the model is not the one classifying.**

Measured in a previous life of this project, not assumed. With severity decided in code
and the model only writing prose a human reads, reasoning off cost nothing. With the
model handed the verdict and asked to justify it, reasoning off took the same setup from
**94/97 correct to 2/100**, fabricating justifications for labels it had already been
given.

The structural good news: **`triage.py` owns severity here already.** No sub-agent
classifies, so the expensive-model requirement mostly evaporates. What remains needing a
strong actor is not what you would guess.

## The job that breaks local models is navigating, not writing

| Setup | Model | Result |
| --- | --- | --- |
| open-ended tool loop, full context | 9b local | **16 `list_sensors` calls, zero `read_sensor` calls, never answered** |
| handed the ranch map | 9b local | read 8 of 28 water sensors, **confident false all-clear**; Opus found 4 tanks below the floor |
| handed an assembled evidence set, asked to write | 4b-class local | 5 sweeps, 160 sensors, 72 incidents, **0 templates, 0 retries, 0 check failures**, ~11s/call, free |

**Local models write well and navigate badly.** A false all-clear is the single worst
output a ranch monitor can produce, and open-ended tool loops are how you get one.

## So the cheap path is a cheaper ARCHITECTURE, not just a cheaper model

The free pass already knows exactly which sensor is bad and holds the ranch map. So
instead of telling a sub-agent "go find out what is wrong," **`tools/evidence.py`
assembles the packet in code**: the incident, that sensor's recent history, its sibling
sensors at the same location, the animals in that pasture, and the relevant SOP. The
model gets one call and no tool loop. It is not navigating, it is judging a page.

Cheaper, faster, more reliable, **and** the shape a local model is demonstrably good at.
Three wins from one decision, which is usually the sign a decision is right.

## The two tiers

| Tier | Model | Jobs | Why it is safe |
| --- | --- | --- | --- |
| **1, default** | local, `gemma4:e4b` via Ollama | judge an assembled evidence packet; write the work order; chaos observation prose; shift-report assembly | Nothing here classifies. Code already ranked severity. A human reads and judges the output. |
| **2, escalation** | Opus | genuine tool-driving investigation; cross-domain fusion when 2+ sensing worlds are hit in one tick; anything proposing a write | These decide what is *true*, or they mutate a real ranch. Both are classification in the sense that matters. |

## The escalation predicate

Fires when **any** holds. The reason is logged in `tick.jsonl` as `escalation_reasons`.

1. the Tier-1 judge returned `insufficient_information`
2. `triage.py` marked the incident **critical**
3. two or more sensing worlds opened incidents in the same tick
4. a `Finding` proposes a write

Condition 3 is the storm front, and it is the whole reason a supervisor exists rather
than four independent scripts.

**Cost shape that falls out:** a calm ranch costs approximately nothing and a storm costs
real money. That is "cost scales with change, not wall-clock" arriving as a side effect
of the architecture rather than as a tuning exercise later.

## The rail that makes Tier 1 safe

**A Tier-1 model may never produce an all-clear.** `triage.py` already flagged the
incident in code, so "nothing is wrong here" from the cheap judge is a contradiction, not
a finding. `validate.py` rejects it and escalates rather than trusting it.

That turns the measured false-all-clear failure mode into a check instead of a footnote.
**The rail goes in before the first job moves down, not after.** Do not relax it to make
a test pass.

## Build order

Tier 2 only at M2. Log `model`, `tokens`, `latency_ms`, and `finish_reason` on every
call. Then move **one job at a time** down to local, each with a rail that catches the
regression, each with a row below. Measure before you fix applies to its own cost lever.

**M2 built no cascade, no local fallback, and no retry, on purpose.** `llm_client.py` has
one function, `call_tier2`, and Tier 1 is absent rather than stubbed. A fallback added
before there is a baseline to fall back *from* is a cost optimization nobody measured, and
it hides the exact signal M7 needs: which jobs Tier 2 was actually good at.

## The Tier 2 baseline, measured at M2

Structured output is a **forced tool call** (`tool_choice={"type":"tool","name":"write_work_order"}`),
not a "reply in JSON" instruction. Two reasons, and the second is the one that matters:
the schema is enforced by the API rather than by a parser, and it keeps `finish_reason`
honest. `tool_use` means a real answer; `max_tokens` means a config bug. With free-form
JSON both arrive as text and the distinction is gone.

Thinking is **off** for this job, per the rule at the top: `triage.py` classified, the
model is writing prose a human reads. `reasoning_effort` is still an explicit per-call
argument, so turning it on for one job later does not require touching the call site of any
other.

| | |
| --- | --- |
| Model | `us.anthropic.claude-opus-5`, Bedrock (`us-east-1`); `AsyncAnthropic` when a key is set |
| Input per packet | **~4,800 to 5,600 tokens**, of which the SOP file is the majority |
| Output per work order | **~1,100 tokens**, capped at `max_tokens=2048` |
| Latency | **14 to 16s** per call, 4 in flight (`AGENT_CONCURRENCY`) |
| Reliability | **19 of 19 shipped, 0 rejected, 0 no-answer**, every call `finish_reason=tool_use` |

**The input number was a 4x miss on the estimate**, sized at ~1,400 before a packet was
printed. `MAX_OUTPUT_TOKENS` had been set to 1,536 against a guessed ~450 output, which the
first real answer would have clipped. Print the page before sizing the budget.

**The cost lever is the SOP, not the evidence.** Trimming the assembled facts saves a few
hundred tokens; the SOP file is thousands. Worth knowing before M7 optimizes the wrong half.

## What M3 added to the bill, measured on two live ticks

Fan-out does not change the per-call cost, it changes how many calls a tick makes. Two live
`--once` runs on a ranch whose ledger already held 8 to 11 `ongoing` incidents:

| | tick A | tick B |
| --- | --- | --- |
| newly-opened, so calls | 15 across 3 worlds | 14 across 3 worlds |
| responder input / output | 90,165 / 14,284 | 77,375 / 13,899 |
| supervisor input / output | 14,335 / **1,024, truncated** | 14,093 / 1,646 |
| tick total | 104,500 / 15,308 | 91,468 / 15,545 |
| wall clock | 85 s | 96 s |
| calls that failed a rail | 0 of 16 | 0 of 15 |

**Tokens are the measurement here; dollars are arithmetic on top of it.** The pricing table lands
at M7 and `cost_usd` joins the tick line with it (`docs/logging.md`), so until then any figure below
is a rate times a token count and the rate is an assumption, stated so it can be corrected in one
place. At **$15/M in and $75/M out**, tick A is **$2.72** and tick B **$2.54**.

**The supervisor is 14% of the input, 11% of the output, and about 13% of the bill** - one call
against fourteen. Per unit of value it is the cheapest thing in the tick, because it is the only
call that reads across worlds.

**A calm tick is $0.00, and that is exact rather than approximate.** Zero newly-opened incidents
means zero responder calls, and `synthesize` assembles in code below `FUSION_THRESHOLD`, so nothing
bills. That is M2's central claim with a fan-out on top of it, unchanged: **cost tracks
newly-opened incidents, not open ones, and not sensor count.**

**Do not read either tick as a steady-state budget.** Both were unusually expensive: the ledger had
just been refilled after M2's rows resolved, so nearly everything wrong on the ranch presented as
new. Naively, 288 ticks a day at tick B's cost is roughly $730/day, and that number is fiction. A
steady-state loop opens a handful of incidents per tick, not fourteen, which is a materially
different bill. M4 runs for 30 minutes unattended and measures it rather than either of us guessing.

## What M4 measured, and the prediction it overturned

`cost_usd` is on the tick line from M4 at the rate above (`llm_client.ASSUMED_RATE_USD_PER_M`),
and the loop halts at `SPEND_CEILING_USD`. The short paid run, prod ledger, chaos off, 120s cadence:

| | tick 1 | tick 2 |
| --- | --- | --- |
| newly-opened, so calls | 12 across 3 worlds | 14 across 2 worlds |
| responder input / output | 72,692 / 10,523 | 82,086 / 13,060 |
| shift report | fused by the model | fused by the model |
| `cost_usd` | **$2.16** | **$2.54** |
| wall clock | 80 s | 90 s |

26/26 shipped, 0 rejected, 0 no-answer. The run halted itself at **$4.70 against a $3.00
ceiling**, exit 4, one tick of overshoot as designed.

**The "handful of new incidents per tick" prediction above was wrong for this ranch.** The
prod ledger was not freshly refilled; it had 25 live incidents from M3 and had churned for
hours. Tick 1 still opened 12 and tick 2 opened 14, and the 30-minute free run at a 60s
cadence opened **10 to 24 per tick, every tick, for 30 ticks.** The reason is in
`docs/STATE.md`: the deployed Sensor API synthesizes a fresh reading on every call, unanchored
to the last one, so healthy sensors draw extreme values a fraction of the time and the ledger
churns 10 to 20 opened and 10 to 20 resolved on every tick regardless of cadence. On this ranch
**every tick is a storm tick.** At 300s that is roughly $2.30 x 12 = **$28 an hour, $670 a
day**, which is the "fiction" number above arriving as a measurement.

That is a property of the simulator, and it points at two levers. The first, a debounce in
reconcile (an incident opens only when a sensor is bad on two consecutive sweeps, which the seven
permanently-bad sensors pass and a one-draw extreme does not), **shipped the same day** as
`INCIDENT_CONFIRM_SWEEPS = 2` and migration `0003`; the row below has the first live tick under
it. The second is M7's cascade. Both are written here so M7 optimizes against the measured bill
and not the predicted one.

**Both ticks were `reasoning_effort="none"`, including the supervisor.** Fusion is the one job in
this repo where that looks arguable, since deciding two incidents are one event is closer to
classification than to writing. It stays off because the fusion claim is *checked* rather than
trusted: `linked` is a list of keys `check_shift_report` verifies against the page, so a wrong
claim is caught in code instead of paid for in tokens. Turning it on is a one-argument change with
a ledger row, if a bad fused report ever survives that rail.

**One config bug found by the first live tick, and it is the M2 lesson word for word.**
`SHIFT_REPORT_MAX_TOKENS` was 1,024, reasoned about rather than measured, and tick A truncated
mid-priorities. Worse, the truncation *mislabelled itself*: a tool call cut off at `max_tokens`
still arrives carrying a partially filled `input` dict, so a `payload is None` check let the
half-answer through the rails, where it failed `all_clear`. The log said the supervisor wrote an
all-clear about a ranch with ten criticals on it. Both `synthesize` and `to_work_order` now treat
truncated as no answer. **Print the page before sizing the budget, and then check that a budget
failure still says it was a budget failure.**

## The Ollama traps

See `src/models/CLAUDE.md` for the code-level versions. In short: `ChatOllama` not the
OpenAI shim (the shim silently ignores `num_ctx`, which produced an entire investigation
that looked like "small models are too weak" and was a 4,096-token default);
`num_ctx` always explicit; `reasoning_effort` an explicit per-call argument, never an
ambient env var; every actor built in `llm_client.py` and nowhere else.

## Measurement log

One row per job that moved tiers. No row, no move.

| Date | Job | From | To | Evidence | Verdict |
| --- | --- | --- | --- | --- | --- |
| 2026-09-10 | evidence-packet judging **plus** work-order write, `water_feed` | - | Tier 2 | 3 live ticks, 19 work orders. 58,339 then 38,328 then 24,161 tokens as the ledger grew 25 to 65 rows. 19/19 shipped, 0 rejected, all `tool_use`. Rule citations discriminate: `WATER-05` on 12 of 19, but `east-allotment-water` cited only `WATER-02` | **baseline set.** The number to beat, not a decision |
| 2026-09-10 | the same job across **four** responders, fanned out | - | Tier 2 | 2 live ticks, 29 work orders, 3 worlds each. 29/29 shipped, 0 rejected, 0 no-answer. Per-call cost unchanged from the row above, so fan-out multiplies calls and not price. `herd_health` was handed nothing on both ticks and logged nothing about it, which is `docs/STATE.md` decision 5 working | **baseline widened.** Still no move |
| 2026-09-10 | the free pass with the debounce (`INCIDENT_CONFIRM_SWEEPS = 2`, migration `0003`) | - | none | 3 free ticks on the prod ledger right after the migration: would-have-opened 14 / 17 / 21, actually opened **0 / 2 / 1**, dismissed 0 / 12 / 16. Nothing a model was asked about changed; what changed is how many times it is asked | **not a tier move, a count move.** Roughly $2.30 a tick to roughly $0.20 to $0.40 at the same per-call price. M7 now optimizes the price of a real incident, not the price of a dice roll |
| 2026-09-10 | the whole tick, in the loop, unattended | - | Tier 2 | 2 paid ticks on the prod ledger at 120s cadence: 12 then 14 newly-opened, $2.16 then $2.54, 26/26 shipped, fused both times, halted at the $3 ceiling with exit 4. 30 free ticks at 60s cadence opened 10 to 24 each. Steady state on this ranch is a storm every tick, not a handful | **no move.** The bill is now measured; the lever is a debounce or M7, not a cheaper tick |
| 2026-09-10 | shift-report synthesis, the supervisor | - | Tier 2 | 1 call per tick, gated at `FUSION_THRESHOLD = 2` worlds, so most ticks make none. 14,093 in / 1,646 out on a 14-order page, `tool_use`, 0 violations. The design table above puts "shift-report assembly" in Tier 1; that is still the intent, and it is not this job. Assembly in code is what a calm tick already does for free | **not moved, and the table's Tier-1 row is about `assemble_shift_report`, not about fusion** |
| _(M7)_ | chaos observation prose | Tier 2 | Tier 1 | pending | pending |
| _(M7)_ | work-order write | Tier 2 | Tier 1 | pending | pending |
| _(M7)_ | evidence-packet judging | Tier 2 | Tier 1 | pending | pending |

The first three rows are what the M7 rows are measured against, which is why they exist at all
in a table that says "one row per job that moved tiers." Nothing moved; a floor was
established. The falling token count is the architecture's central claim landing: **cost
tracks newly-opened incidents, not open ones.** Tick 3 cost 41% of tick 1 while watching
more of the ranch.
