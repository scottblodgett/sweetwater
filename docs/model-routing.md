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
| _(M7)_ | chaos observation prose | Tier 2 | Tier 1 | pending | pending |
| _(M7)_ | work-order write | Tier 2 | Tier 1 | pending | pending |
| _(M7)_ | evidence-packet judging | Tier 2 | Tier 1 | pending | pending |

The first row is what the three M7 rows are measured against, which is why it exists at all
in a table that says "one row per job that moved tiers." Nothing moved; a floor was
established. The falling token count is the architecture's central claim landing: **cost
tracks newly-opened incidents, not open ones.** Tick 3 cost 41% of tick 1 while watching
more of the ranch.
