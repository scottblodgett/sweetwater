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
| **1, first** | local, `gemma4:e4b` via Ollama | **the per-incident work order** at every severity (M10), **the fused shift report** when two or more worlds opened incidents (M10), and **the investigator**, a bounded tool loop that fires only when the local judge said `insufficient_information` (M10, ships off) | Nothing here classifies. Code already ranked severity, code assembled the page, code checks the answer (the rails, and from M10 the `linked` enum), and a human reads it. |
| **2, escalation** | Opus | the rewrite, from the identical page, of any work order or report Tier 1 got **rejected** on, said **`insufficient_information`** on (after the investigator, when on), **proposed a write** in, or gave **no answer** to; and any page too long for the local window (**`page_too_long`**, the one pre-call reason) | These are the answers the cheap judge could not give, or would mutate a real ranch, or could not be read whole locally. |

**Corrected at M7, before anything moved.** The table used to list four Tier-1 jobs. Two of them
do not exist: *chaos observation prose* (M5 made chaos pure code, no model anywhere in it) and
*shift-report assembly* (`assemble_shift_report` is the code path a calm tick already runs for
free). Packet judging and the work-order write were listed as two jobs and are one call, and have
been since M2. **Inverted at M10.** Until then the table sent critical incidents and the fused
report to Opus before asking; M10 asks Tier 1 first for both, on Scott's call that severity is
code's and the rails hold at every severity, and added the third job. There are exactly **three
model jobs in this repo**: the per-incident work order, the fused shift report, and the
investigator.

## The escalation predicate

Per incident. Fires when **any** holds, and the reason is logged on the work order and summed
into `tick.jsonl` as `escalation_reasons`.

1. the prompt does not fit the local window: **`page_too_long`** (known before the call and code's: `routing.fits_tier1` at a conservative 3.0 chars per token against `num_ctx` less the answer budget and a margin; Ollama truncates a long prompt from the front and the model cites what it never read). Until M10 this slot was **critical**, and it is not any more
2. the Tier-1 answer failed a **blocking rail** (`all_clear`, `severity_mismatch`, `invented_rule`, `no_payload`, `schema_invalid`; for the report, `invented_incident`, `all_clear`, `schema_invalid`)
3. the Tier-1 judge returned **`insufficient_information`** (a field it may set on purpose; a model allowed to say "I do not know" says it instead of inventing). From M10, when the investigator is on, this reason runs the loop first and only a page still thin after one re-judge reaches Opus
4. the Tier-1 answer **proposed a write** (only a Tier-2 proposal may reach the gate)
5. the Tier-1 call **never answered** (transport, truncation)

Conditions 2 to 5 are known only after the cheap call, so escalation is a **rewrite**: Opus gets
the identical page and writes its own work order or report; the Tier-1 answer is kept in `agent.jsonl`
as the receipt and the Tier-2 order is the one stored. Nothing is retried at the same tier.

**"Two or more sensing worlds opened incidents" is not a per-incident trigger.** The plan had it as
one, and it was dropped at M7 on Scott's reasoning: the world count says nothing about what one
packet contains, and applying it per incident would send every work order on every storm tick to
Opus, which is precisely the bill M7 exists to cut. What the world count actually decides is whether
the tick needs someone reading *across* the ranch, and that is the fused shift report, gated at
`FUSION_THRESHOLD = 2` and, from M10, a Tier-1 call first like everything else. One constant, one meaning.

**Cost shape that falls out:** a calm ranch costs approximately nothing and a storm costs
real money. That is "cost scales with change, not wall-clock" arriving as a side effect
of the architecture rather than as a tuning exercise later.

## The rail that makes Tier 1 safe

**A Tier-1 model may never produce an all-clear.** `triage.py` already flagged the
incident in code, so "nothing is wrong here" from the cheap judge is a contradiction, not
a finding. `workers.check` rejects it (the `all_clear` rail reads the actions list, not the prose,
and it has existed since M2), and from M7 a Tier-1 rejection **escalates** the same packet to Tier 2
with the reason logged. A Tier-2 rejection is stored as rejected and never retried.

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
at M7 and `cost_usd` joins the tick line with it (`docs/plan.md`), so until then any figure below
is a rate times a token count and the rate is an assumption, stated so it can be corrected in one
place. At **$15/M in and $75/M out**, tick A is **$2.72** and tick B **$2.54**.

**The assumption was wrong by 3x, found at M7.** Claude Opus 5 lists at **$5/M in and $25/M out**
on the first-party API. Every dollar figure from M2 through M6 in this file, `state.md`, and
`journey.md` is three times the real bill: the "$0.16 per work order" is about **$0.05**, and the
"$28 an hour" storm was about **$9**. The rows are left as written because the tokens in them are
the measurement and the rate was one line. From M7 `cost_usd` is computed from
`llm_client.PRICE_TABLE` per model; Bedrock's public pricing page did not render an Opus 5 row, so
the Bedrock entry is set at first-party parity and marked unverified in the table.

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
`docs/state.md`: the deployed Sensor API synthesizes a fresh reading on every call, unanchored
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

Code-level, in `src/models/CLAUDE.md`: `ChatOllama` not the OpenAI shim, `num_ctx` explicit, `reasoning_effort`
a per-call argument, every actor built in `llm_client.py`, `finish_reason` logged against the healthy set.

## Measurement log

One row per job that moved tiers. No row, no move.

| Date | Job | From | To | Evidence | Verdict |
| --- | --- | --- | --- | --- | --- |
| 2026-09-10 | evidence-packet judging **plus** work-order write, `water_feed` | - | Tier 2 | 3 live ticks, 19 work orders. 58,339 then 38,328 then 24,161 tokens as the ledger grew 25 to 65 rows. 19/19 shipped, 0 rejected, all `tool_use`. Rule citations discriminate: `WATER-05` on 12 of 19, but `east-allotment-water` cited only `WATER-02` | **baseline set.** The number to beat, not a decision |
| 2026-09-10 | the same job across **four** responders, fanned out | - | Tier 2 | 2 live ticks, 29 work orders, 3 worlds each. 29/29 shipped, 0 rejected, 0 no-answer. Per-call cost unchanged from the row above, so fan-out multiplies calls and not price. `herd_health` was handed nothing on both ticks and logged nothing about it, which is `docs/state.md` decision 5 working | **baseline widened.** Still no move |
| 2026-09-10 | the free pass with the debounce (`INCIDENT_CONFIRM_SWEEPS = 2`, migration `0003`) | - | none | 3 free ticks on the prod ledger right after the migration: would-have-opened 14 / 17 / 21, actually opened **0 / 2 / 1**, dismissed 0 / 12 / 16. Nothing a model was asked about changed; what changed is how many times it is asked | **not a tier move, a count move.** Roughly $2.30 a tick to roughly $0.20 to $0.40 at the same per-call price. M7 now optimizes the price of a real incident, not the price of a dice roll |
| 2026-09-10 | the whole tick, in the loop, unattended | - | Tier 2 | 2 paid ticks on the prod ledger at 120s cadence: 12 then 14 newly-opened, $2.16 then $2.54, 26/26 shipped, fused both times, halted at the $3 ceiling with exit 4. 30 free ticks at 60s cadence opened 10 to 24 each. Steady state on this ranch is a storm every tick, not a handful | **no move.** The bill is now measured; the lever is a debounce or M7, not a cheaper tick |
| 2026-09-10 | shift-report synthesis, the supervisor | - | Tier 2 | 1 call per tick, gated at `FUSION_THRESHOLD = 2` worlds, so most ticks make none. 14,093 in / 1,646 out on a 14-order page, `tool_use`, 0 violations. The design table above puts "shift-report assembly" in Tier 1; that is still the intent, and it is not this job. Assembly in code is what a calm tick already does for free | **not moved, and the table's Tier-1 row is about `assemble_shift_report`, not about fusion** |
| 2026-09-11 | the work-order write **with the `proposed_write` field** (M6) | Tier 2 | Tier 2 | 2 paid ticks on the prod ledger after the debounce: 1 then 2 newly-opened, 3/3 shipped, 0 violations, $0.17 then $0.32, so roughly **$0.16 per work order**, unchanged from M3 with the field and the brief paragraph added. `water_feed` proposed nothing on two feed-low packets | **no move.** The gate added a field, not a tier. This is M7's row to beat |
| 2026-09-11 | **the per-incident work order** (packet judging plus the write, one call), `gemma4:e4b` on Ollama for every incident the predicate does not claim | Tier 2 | Tier 1, **measured and not adopted** | 3 ticks on `SW_OPS_TARGET=test` after a warm-up, cascade on, every local order shadowed by Opus on the identical page (`docs/transcripts/m7-compare-transcript.md`). 12 opened, 12/12 shipped, 0 rejected, **0 all-clears, 0 invented rules**, sensor named and reading quoted 12/12. 6 were critical and went straight to Opus. Of the 6 Tier-1 candidates, **5 escalated on `insufficient_information`** and 1 stayed local, so the cascade saved 1 call in 12. On the 6 pairs, local named the flagged neighbour on 1 of 3 pages that had one (Opus 3 of 3), the head count on 1 of 4 (Opus 4 of 4), cited the more specific rule less often (SENSOR-01 alone where Opus added SENSOR-05; SENSOR-05 where Opus led with SENSOR-02), and padded actions with echoes of its own brief on 3 of 6 ("In the work order, name Calving Pasture and 111 head"). 3,024 to 4,207 tokens in against `num_ctx` 16,384; 5.6 to 13.3s a call warm, 22s cold; $0.74 for the three ticks at Opus's real rate, shadows excluded | **no move. `TIER1_ENABLED` ships off.** Not because the local model was unsafe (the rails never fired on it) but because it was thin about exactly the two things on the page that matter, and because it escalated 5 of 6 on its own, which leaves nothing to save. The row is the bar for the next attempt |
| 2026-09-11 | **the fused shift report** | Tier 2 | Tier 2 | not measured at Tier 1 on purpose: it is the one call that decides what is true across worlds, and the design table's Tier-1 note was about `assemble_shift_report`, which is code. Fused twice in the three ticks, 0 violations | **stays.** By decision rather than by measurement |
| 2026-09-11 | **the herd order** (M7A: `deceased`, `care_overdue`, the same per-incident job on a cow's page), `herd_health` | - | Tier 2 | 10 herd orders across four paid ticks on the test ledger, 9 shipped, 1 rejected (`severity_mismatch` plus `write_shape_invalid`, tick C). 6,800 to 7,000 tokens in per packet, the SOP the majority as everywhere. Every shipped order named the animal by id and tag, quoted the observation verbatim, cited `HERD-01` or `HERD-04` plus `HERD-05`; the deceased orders escalated to the GM on the predator note. `proposed_write` null on 7 of 7 until `HERD-07` gave the model a legitimate note to record, then 1 of 1 proposed and paused. About $0.06 per herd order | **Tier 2, by the predicate.** `deceased` is critical and goes to Opus before the call; `care_overdue` is a warning and would be a Tier-1 candidate when the cascade is next tried. Not measured at Tier 1: the cascade ships off and the herd page is new |
| 2026-09-12 | **the feed page's missing fact** (#12): the weather and the yard fuel on the feed page, from the same sweep, zero HTTP | - | none (a page change) | Offline replay on `gemma4:e4b`, $0: the real `feed-bin-03` packet built live, judged 3 times with the block stripped and 3 times with it. **`insufficient_information` 2 of 3 before, 0 of 3 after**; weather or fuel reasoned about in the prose 2 of 3 before, 3 of 3 after; +165 tokens a page. The remaining unknown was the Feed Room's head count, which is honest: it is a site, not a pasture. Live, the two feed orders that stood locally in run B named the snow and the cold (`-24.7 F and 7.6 in snow`) | **#12 closed.** The page was what was wrong, as the M7 row said; a fact the SOP asks for now sits on the page and the local judge stops saying it cannot answer |
| 2026-09-12 | **the investigator**, a bounded LangGraph tool loop on `gemma4:e4b` through `langchain-mcp-adapters`, fired by `insufficient_information` at Tier 1 | - | Tier 1, **measured and shipped off** | 28 live loops across the two M10 runs (15 in run A, 13 in run B), every one logged turn by turn. **Cured 0 of 28**: no enriched page came back without `insufficient_information`. Outcomes: `no_tool_calls` 19, `answered` 7, `error` 1 (an MCP `ConnectTimeout` at session open). `no_tool_calls` is the model writing its tool calls as text (`get_animal{animalId:<|"|>cow-0777<|"|>}`), which Ollama did not parse into `tool_calls`; `answered` loops reached for `list_sensors` whole (36k characters, cut to 4k by the interceptor) on 6 of 7 before touching anything else, then read the incident's own sensor, which the brief forbade. 0 write attempts, 0 step ceilings, 0 deadlines; 20 to 40 s a loop, 3,000 to 30,000 tokens in. And the unknowns the judge listed were mostly not on any endpoint: how long a sensor has been dark (`get_sensor_readings` on an offline sensor returns an empty series), the power source behind a fence, a site's head count | **Ships off** (`INVESTIGATOR_ENABLED=0`). Not because it was unsafe (the belt was never tested by a write attempt, the ceilings never hit) but because it did nothing for 20 to 40 s a page. Two things would change the next attempt: a local model whose tool calls Ollama parses (`qwen3.5:9b` is pulled and untried), and the honest finding that `insufficient_information` on this ranch is mostly about facts the ranch does not record, which no loop fetches |
| 2026-09-12 | **the tiers inverted**: the work order at every severity and the fused report, Tier 1 first, Opus by escalation | Tier 2 for critical and the report | Tier 1 first, **recommendation below** | Run A, 9 ticks with `TIER_COMPARE=1`: 31 opened, 31 shipped, **17 written locally (55%)**, 14 escalated (13 `insufficient_information`, 1 `proposed_write`), 4 fused reports all rewritten by Opus on `invented_incident` (prose in `linked`). Run B, 5 ticks, compare off, after the `linked` enum: 22 opened, 22 shipped, **9 local (41%)**, 13 escalated, all `insufficient_information`; **2 of 2 fused reports written locally and rail-clean**. Across both runs 26 stored local orders: 0 all-clears, 0 invented rules, 0 severity mismatches, 0 unnamed sensors, 26 shipped; 17 of 26 had a write proposal stripped (`write_tool_not_allowed` / shape: the local model names tools its slice does not have) against 0 of 27 Opus orders. Run A's 27 pairs on identical pages: subject named 27/27 both, reading quoted 21/21 both, the flagged neighbour named **11 of 17 vs 17 of 17**, the head count **12 of 21 vs 21 of 21**, rules cited 38 vs 55. Calls: 71% local in A, 78% in B. Dollars: run B **$0.80 for 22 orders and 2 reports** against about $1.48 all-Opus at the measured per-call rates, so **about half**; run A's lines read $1.11 and its shadows cost another $1.01. The first rail-clean local report (run B tick 2, 16 orders) had a paragraph for a headline, bold labels on the priorities, one mislabelled fact (a temperature incident called a tank level), and linked the right two keys. `unverified_number` fired once in 6 model reports, on **Opus**, for `-20 F` where the page said `-20.3 F` | **Inverted and measured; `TIER1_ENABLED` still ships off pending Scott's call at the M10 check-in, recommendation on.** The bar M7 set had two parts. "It escalates so much there is nothing to save" is cleared: 41% to 55% of orders and the fused report stand locally and the bill halves. "Thin about the neighbour and the herd" is not: a third of the pages with a flagged neighbour and half with a head count lose them, and no rail can see that (the M3 finding). The flip is one line; the row is what it costs |

The first three rows are what the M7 rows are measured against, which is why they exist at all
in a table that says "one row per job that moved tiers." Nothing moved; a floor was
established, and at M7 the floor held.

## What M10 measured, and the verdict

The build was inverted from its intent until M10: the aggregation job was hardcoded to Opus, the local
model got the warning-severity orders and escalated most of them, and the adapter that hands a model its
tools was pinned and unimported. M10 did three things in order and measured each: put the weather and the
yard fuel on the feed page (#12), built the investigator, and inverted the tiers. Two live runs on the
test ledger, seed 1, chaos on, the same ranch both times.

| | run A (`TIER_COMPARE=1`) | run B (compare off, after the `linked` enum) |
| --- | --- | --- |
| ticks / opened / shipped | 9 / 31 / 31 | 5 / 22 / 22 |
| written locally | 17 (55%) | 9 (41%) |
| escalated, and why | 14: `insufficient_information` 13, `proposed_write` 1 | 13: `insufficient_information` 13 |
| fused reports: local and clean / rewritten by Opus | 0 / 4 (`invented_incident`, prose in `linked`) | **2 / 0** |
| investigations: answered / no tool calls / error, cured | 5 / 9 / 1, **0** | 2 / 11 / 0, **0** |
| model calls, local share | 119, 71% | 58, 78% |
| dollars on the tick lines / shadows / total | $1.11 / $1.01 / $2.12 | $0.80 / none / $0.80 |
| wall clock, storm tick | 185 s | 157 s |

**What held.** Every rail. 26 local orders stored across both runs and none was an all-clear, none
invented a rule, none disagreed with triage, every one named its sensor or its animal and quoted the
reading. The `linked` enum turned a 0-for-4 local supervisor into 2-for-2 on the next run, and the rail
behind it is still there. The step ceiling, the deadline, and the write belt were never reached by a
model that never got that far, and each has a planted test.

**What did not.** The investigator cured nothing in 28 tries. The local model wrote its tool calls as
prose on 19 of them and Ollama did not parse them; where it did call tools it fetched the whole sensor
catalogue first; and most of what the judge said it lacked (how long a sensor has been dark, what powers
a fence, how many head stand at a feed room) is not on any endpoint the ranch has. **A loop cannot fetch
what nobody records.** `INVESTIGATOR_ENABLED` ships off and the row says what the next attempt changes.

**What is true about the cascade now.** With the tiers inverted the local model writes 41% to 55% of
the stored orders and, with the enum, the fused report, and the bill is about half of all-Opus at the
same incident count. The loss is the one M7 named and no rail can see: on the pages that carried a flagged
neighbour or a head count, the local order left them out a third to half of the time and Opus never did;
17 of 26 local orders also named a write tool their slice does not have, stripped in code, against 0 of 27
for Opus. `insufficient_information` at 13 of 22 is the other half of the story: the local judge says it
where the schema's own description says not to, and the investigator was meant to be the answer to that
and was not. **`TIER1_ENABLED` still ships off, pending Scott's call at the M10 check-in.** The
recommendation is on: half the bill, every safety rail holding, and a known, written-down quality loss on
the fields a rancher reads the order for. The flip is one line in `config.py` and this paragraph is what it
buys and what it costs.

**The bill for the phase.** About $3.50 on Opus against an estimate of $1.00 to $1.25: run A ran nine
ticks instead of four because its loop survived a kill that reported success (`docs/cookbook.md` #49),
and the first attempt at run B shared the ledger with it for one tick before that was found. The runaway
ticks are in the ledger as data; the contaminated tick's logs were discarded.

## What M7 measured, and why the cascade shipped off at M7

Everything in the design above is built: `routing.tier_for`, the predicate as a post-call rewrite,
the price table, `call_tier1` on `ChatOllama`, `TIER_COMPARE` for the side-by-side, and the tick
line's `tier` / `escalations` / `escalation_reasons`. Three measured ticks with the cascade on, on
the test ledger, so nothing a demo reads from was touched:

| | tick A | tick B | tick C |
| --- | --- | --- | --- |
| opened (critical) | 9 (4) | 1 (0) | 2 (2) |
| Tier-1 candidates -> stayed local | 5 -> 1 | 1 -> 0 | 0 -> 0 |
| `escalation_reasons` | critical x4, insufficient_information x4 | insufficient_information | critical x2 |
| shipped / rejected | 9 / 0 | 1 / 0 | 2 / 0 |
| shift report | fused | code | fused |
| wall clock | 77s | 23s | 36s |
| `cost_usd` | $0.52 | $0.05 | $0.16 |

**Three things the run settled.**

1. **The safety rails held on the local model.** Zero all-clears, zero invented rules (after the
   citation trim below), zero severity disagreements, every order named its sensor and quoted its
   reading. The escalation-on-rejection path never fired live because nothing was rejected; its
   proof is the planted test, which stands.
2. **`insufficient_information` was the whole story.** The local model set it on 5 of 6 candidates.
   Read charitably, it was right every time: FEED-02 asks for the forecast and the page has none;
   SENSOR-01 asks how long the sensor has been dark and the history is empty. Opus, on the same
   pages, wrote around the gap and listed it under `unknowns`, which is what the field's description
   asks for. One tightening of the wording moved the water packet from 5/5 to 2/5 and the feed
   packet from 5/5 to 4/5. The field is doing what it was designed to do; the model reaches for it
   where the SOP names a fact the packet does not carry, and on this ranch that is most packets.
3. **Where the local prose was thinner, it was thinner about the neighbour and the herd.** Both
   are on the page, both are what a rancher reads the order for, and the rails cannot see either
   (`docs/state.md`, the no-brief finding). Opus named the degraded tank probe beside the open gate,
   the empty bin beside the low one, and 111 head on every page that had them. The local model
   named the bin and not the other two, and the head count once in four.

**The move that would have earned the row** is the one Scott named in the design conversation:
put the forecast on the feed page (the ranch has wind, temperature, and snow-depth sensors and the
packet does not carry them), so FEED-02 can be answered from the page and `insufficient_information`
stops being the honest answer. That is `evidence.py`'s change, its own item (`docs/open-issues.md` #12),
and the next attempt at this row runs after it.

**The close-out tick agreed.** Re-running the documented `TIER1_ENABLED=1 TIER_COMPARE=1` command at
the phase close opened 9 more (5 critical): 4 Tier-1 candidates, 2 stayed local, 2 escalated on
`insufficient_information`, 9/9 shipped, $0.48. Across all four measured ticks: 10 candidates, 3
stayed local, so the cascade as built saves about a quarter of the non-critical calls and none of
the critical ones. Same verdict.

**The bill, restated at the real rate.** Tick A above, 9 orders and a fused report, was $0.52. The
M6 row's "$0.16 per work order" is about $0.05. At the debounced steady state of one to three new
incidents a tick, the loop costs roughly **$0.05 to $0.20 a tick, $0.60 to $2.40 an hour**, all of
it Opus, and the ceiling still halts it. The falling token count is the architecture's central claim landing: **cost
tracks newly-opened incidents, not open ones.** Tick 3 cost 41% of tick 1 while watching
more of the ranch.
