# JOURNEY

What actually happened, and where it diverged from the plan. Written as we go rather
than reconstructed at the end, because a reconstruction only records the decisions that
worked.

File names are as they were at the time. In the post-M9 review (2026-09-11) `docs/architecture.md` and
`docs/logging.md` were folded into `docs/plan.md`, `docs/issues.md` became `docs/open-issues.md` (issue numbers
unchanged), the three transcripts moved to `docs/transcripts/`, and the per-phase defect lists below were trimmed
to one line each where `docs/cookbook.md` holds the full entry.

---

## M0 - Skeleton, logging, and a live handshake

**2026-09-10. Done.**

The tree, the nested `CLAUDE.md` files, the docs skeleton, `config.py`, `logger.py` with
all three streams, and `mcp_client.py` speaking Streamable HTTP to the deployed Function
URL.

**M0 gate, all green:**

| Check | Result |
| --- | --- |
| `python main.py --handshake` | **19 tools, 160 sensors, 32 locations, 13 types**, ~2-4s, exit 0. Protocol negotiated to `2025-11-25`. |
| handshake writes `tick.jsonl` | one well-formed JSON line carrying `run_id` and `tick` |
| **failed** handshake writes `tick.jsonl` | yes, with `error` and `failed_stage`, exit 1 |
| `pytest` | 17 passed |
| `ruff check .` | clean |
| `mypy src main.py` (strict) | clean, 12 files |

### Divergence: Python 3.11, not 3.12

3.12 is not installed on this machine (3.11.9 and 3.14.3 are). Took **3.11.9**: langgraph,
asyncpg, and greenlet all have mature wheels there, and 3.14 is where native builds break.
No plan content depends on 3.12.

### Three defects M0 caught in itself

The first two are the reason logging lands first rather than last; the third is the reason `mypy --strict`
is in the gate rather than aspirational. Each is a cookbook entry.

1. **`failed_stage: null` on a connect failure.** The marker was assigned inside the block it described. Set before each stage now. Cookbook #2.
2. **`unhandled errors in a TaskGroup (1 sub-exception)`** for every transport failure. `flatten_exception()` walks the group to its leaves and deduplicates; the catch widened to `BaseException`. Cookbook #1.
3. **`getattr(c, "text", None)` type-checked by accident** on a five-way union and would have read a non-text block as "the tool returned nothing." `isinstance` now. The code worked and the tests passed; only the type checker knew. Cookbook #4.

### A found fact worth writing down

**A Lambda Function URL routes every path to the same handler**, so a bogus path on `MCP_URL` is a passing
handshake. Failure paths are tested against an unreachable host. Cookbook #3.

### Doc audit after the gate closed, and a fourth defect

Scott asked whether `CLAUDE.md` was current. It was not, in three places:

| Claim | Reality |
| --- | --- |
| "No Docker until M9" (also in `README.md`) | M9 is the window, **M10** is dockerize. `docker-compose.yml` had it right; the two docs that a reader hits first had it wrong. |
| `ruff format --check .` listed as a command | **It fails on 5 files, and always will.** See below. |
| `--once`, `--api`, and the bare loop listed as commands | All three are stubs that exit `3`. Nothing said so. |

**The formatter defect is the interesting one, because the config guarantees it.**
`[tool.ruff.lint] ignore = ["E501"]` deliberately permits a long line, and the convention
says not to fragment a line that reads fine horizontally. But `ruff format` hard-wraps at
`line-length = 140` with no per-line escape hatch, so the linter's permission and the
formatter's mandate are in direct contradiction. A documented command that the repo's own
settings can never satisfy is worse than an absent one, so **`ruff format` is out of the
gate and out of the docs.** `ruff check` is the lint gate.

The general shape: **a doc defect and a code defect have the same cause, and only the code
one gets caught by a test.** M0's gate was genuinely green while the file describing how to
run the gate was wrong. Worth a habit rather than a one-time fix, so from M1 the doc step at
each boundary re-runs every command the docs claim works.

---

## M1 - The free pass

**2026-09-10. Done.**

`catalog -> sweep -> triage -> reconcile -> route`. Five stages, zero model calls, 160
sensors narrowed to roughly twenty findings before anything expensive can happen. Plus the
real `sw_ops` store, Alembic, and the two rails that keep this repo out of the ranch's
schemas.

**M1 gate, all green:**

| Check | Result |
| --- | --- |
| `pytest` | 132 passed |
| `ruff check .` | clean |
| `mypy src main.py` (strict) | clean, 28 files |
| migration against Supabase | `sw_ops` created at `0001`, `alembic_version` **inside** `sw_ops`, not `public` |
| `--once` against `sw_ops_test` and prod | 160 read, 0 failed, exit 0, exactly one `tick.jsonl` line per run |
| `--once` twice in a row | run 2 reports `ongoing`, which is the whole reason the store is in this phase and not M4 |

### The `-500` fault, and why the fix is a window rather than a case

One temperature sensor reads `-500` with `status: "online"`. `triage.py` carries a per-type physical-plausibility
window rather than a special case for one sentinel, so the next sentinel the upstream picks is caught too. Cookbook #6.

### An M0 defect that only M1 could find

**Foreign stdlib records skipped the structlog processor chain.** `alembic` and `httpx` lines came out with no
`run_id`, so they were beside the stream rather than in it. M0's gate was green because M0 logged through nothing
else; the defect needed a second library in the process to exist. Cookbook #7.

### Reaching into the frozen upstream, and retracting it

Writing the triage thresholds, the fastest route looked like reading the deployed Sensor API's generator in
`C:\temp\MCP-Farm`. Did it, then retracted it: a constant lifted out of another service is a value nothing here
can verify. Thresholds derive from `docs/sweetwater-ranch.md` and the observed distributions in `docs/state.md`.
Cookbook #5, and the rule is sharpened in root `CLAUDE.md` and `docs/state.md`.

### The defect this phase caught in itself: the tree stopped matching the plan

Found at the boundary, when Scott asked whether `docs/plan.md` still described the repo. It did not, in four
ways, none of which any test could see:

| Plan says | M1 shipped |
| --- | --- |
| `src/agent/executor.py` holds the loop | `src/agent/tick.py` |
| `src/agent/agent.py` holds the supervisor **and** routing | routing in its own `src/agent/routing.py` |
| three test modules | seven |
| eleven named leaves exist | absent, including the whole of `src/prompts/` |

The `routing.py` one was the live grenade: `docs/plan.md` also names `src/models/routing.py` for a different
question (which model **tier**, not which **agent**), and two modules with one name doing unrelated work is a
mis-import that type-checks. Fixed both ways: `git mv` to conform the code (the seven-into-two test merge
verified by diffing the sorted set of collected test names, 89 before and 89 after), eleven docstring-only
placeholders naming the milestone that fills each, and a "lands at" marker column on every line of the plan's
tree. Cookbook #9.

### And the process defect underneath it

**M1's first commit did step 1 of the phase-close ritual and skipped step 2 entirely.** No
`state.md`, no `journey.md`, no `cookbook.md`. So for one commit the session-start briefing
told the next session that HEAD was `ab4d2ff`, that M1 was "not started," and that a
cookbook entry it had promised was still coming.

That is the same shape as M0's fourth defect one layer up: **a doc defect and a code defect
have the same cause, and only the code one gets caught by a test.** M0 concluded from that
"re-run every command the docs claim works." M1 extends it, because the drift here was not
in a command, it was in a *diagram*, and no command would have caught it either. The
structural fix is the marker column plus the placeholder files: they put the plan's claim
about the tree where a reader trips over it, since a test never will.

---

## M2 - One agent, Opus only

**2026-09-10. Done.**

The first phase that spends money. `evidence.py` assembles the page, `llm_client.py` calls
Opus once, `system_prompts.py` carries the brief, `workers.py` runs the rails, and
`water_feed` writes a work order for every newly-opened incident it owns. Tier 2 only: no
cascade, no local fallback, no retry.

Built in the mandated order, and the order was the point. **The packet was printed before
any model existed**, which is what caught defects 1, 2, and 3 below; the brief was written
last, against a page that had actually been read. A brief drafted against an imagined packet
reads fine and grounds nothing.

**M2 gate, all green:**

| Check | Result |
| --- | --- |
| `pytest` | **168 passed** (132 before) |
| `ruff check .` | clean |
| `mypy src main.py` (strict) | clean, 29 files |
| `--once` three times live | **19 work orders, 19 shipped, 0 rejected, 0 no-answer**, every call `finish_reason=tool_use` |
| token cost flat as incidents grow | **58,339 then 38,328 then 24,161** while the ledger went 25 to 65 rows |
| every order names a real sensor and quotes its real reading | yes, graded not asserted |
| `finish_reason` on every call | yes, and written before validation |
| every command the docs claim works | `--handshake` exit 0 (19 tools, 160 sensors, 32 locations), `--once` exit 0 (**10/10 shipped, 63,446 tokens**), bare loop exit 3 `arrives_in=M4`, `--api` exit 3 `arrives_in=M8`, and all three documented `jq` queries, one of which was wrong. See below |

The token curve is the phase's actual claim. **Cost tracks newly-opened incidents, not open
ones**, so tick 3 cost 41% of tick 1 while watching more of the ranch. That is the free pass
paying for itself, and it is a measurement rather than an argument.

### The first work order, because "it reads well" is the deliverable

Alkali Flat, tank at 1.9 gal against a 2 gal floor, 111 head on 2,400 acres. Opus quoted
1.9 gal as current and used the 0.8-3.9 gal history **only as a range and a trend**, used the
second tank on the same ground at 16.7 gal to argue float-or-supply rather than a
pasture-wide outage, noted the battery at 82.5% to rule out a dead solar site, cited
`WATER-01` and `WATER-05`, and pushed six facts it did not have - starting with tank capacity
- into `unknowns` instead of inventing them.

Citations discriminate rather than pattern-match: `WATER-05` appears in 12 of the 19 orders,
but `east-allotment-water` cited only `WATER-02`. Worth watching, not a defect.

### Divergence: the credential is Bedrock, not first-party Anthropic

`ANTHROPIC_API_KEY` was empty and `docs/state.md` called it a hard M2 blocker. It was not
one: this session authenticates to Bedrock (`us-east-1`, `us.anthropic.claude-opus-5`), so
`resolve_provider()` picks first-party when a key exists and Bedrock otherwise, scoping the
model id on the Bedrock path only so `TIER2_MODEL` stays one setting for both.
`AsyncAnthropic` and `AsyncAnthropicBedrock` expose an identical `messages.create`, so this
cost a constructor and nothing downstream changed shape.

**It is right for a supervised M2 and wrong for M4.** Session credentials expire, and a
continuous loop cannot depend on one. When they do expire the failure is data - an
`ExpiredTokenException` in `ModelResponse.error` and a `no_answer` work order - and there is
a rail for exactly that.

Also: the raw SDK rather than `ChatAnthropic`, because `langchain-anthropic` has no Bedrock
path and M2 has no tool loop for LangChain to run. `anthropic[bedrock]` was missing from
`requirements.txt` entirely, arriving transitively through `langchain-anthropic` without the
extra, so the first call failed on `No module named 'botocore'`. Now named as the first-order
dependency it is.

### Divergence: `src/agent/workers.py` is new to the plan's tree

`docs/plan.md` had `agent.py` carrying the routing table **and** the worker factories. M2 split
the second half into `workers.py`, because the two answer different questions: `agent.py` says
which agent owns a category, `workers.py` says what an agent's answer must satisfy. Folding the
rails, the checker, and the fan-out into a 113-line routing table reproduces the exact confusion
the M1 `routing.py` rename existed to remove.

The plan's tree is amended with the reason written into it, which is the M1 lesson applied
rather than repeated: the defect there was **silent** drift, and no test reads a diagram.

### Five defects M2 caught in itself

Full entries as cookbook #10 through #15. The first three are all consequences of printing the packet first.

1. **A name join across two services returned an empty set, not an error.** Zero siblings, no pasture; the map and REST spell a location differently. Slugified to the id. Cookbook #15.
2. **Two honest numbers on one page with no note about precedence.** Triage judged 3.4 gal, the newest history point said 0.8 gal later. `render()` says the series is shape and trend only. Cookbook #12.
3. **The token estimate was off 4x.** `MAX_OUTPUT_TOKENS = 1536` against a guess; measured 5,555 in / 1,137 out, the SOP the majority. Raised to 2,048 with the numbers in the comment. **The cost lever is the SOP, not the evidence.** Cookbook #13.
4. **The grader's own failure case caught the grader.** `"40"` was a substring of `13:40:00`. Number tokens on both sides now. Cookbook #11.
5. **Ten test rails were about to start billing.** `spend=False` at every call site and an autouse `no_model_calls` fixture. Cookbook #14.

### The doc defect, and this time step 2 is what found it

M0 concluded "re-run every command the docs claim works." M2 is the first phase where one of those commands had
real data to run against, and it failed immediately: the documented `jq` that selects `finish_reason != "stop"`
returned **all 19 lines, every one healthy**, because Anthropic never emits `stop`. It shipped in four files and
survived two milestones. The first fix was also broken, in a way that produced no output and looked healthy;
a negative control caught it. Cookbook #10 has the whole story. The same audit found `tokens_in` / `tokens_out`
printed where the code writes `input_tokens` / `output_tokens`, and two docs still calling `--once` a stub.

The general lesson is M0's fourth defect one more layer out: **a doc defect and a code defect have the same cause,
and only the code one gets caught by a test.** The habit works. It just cannot fire until the command has
something to say.

### Two decisions worth not re-litigating

**Structured output is a forced tool call**, not a "reply in JSON" instruction. The schema is
enforced by the API rather than by a parser, and - the reason that actually matters - it keeps
`finish_reason` honest: `tool_use` is a real answer and `max_tokens` is a config bug. With
free-form JSON both arrive as text and that distinction is gone.

**The all-clear rail reads the actions list, not the prose.** "The second tank at 16.7 gal is
fine, so this is the float and not the pasture" is a correct, useful sentence and the actual
diagnosis; a prose matcher rejects it. A rail that punishes accurate writing gets switched off
inside a week, so the rail asks the only question that cannot be argued with: does this order
tell somebody to do something.

### Work not asked for, and why each one is here

Disclosed at the boundary rather than merged quietly:

| Added | Why it was not optional |
| --- | --- |
| `anthropic[bedrock]` in `requirements.txt` | the phase does not run without it |
| `conftest.no_model_calls` | defect 5; a flag alone is not a rule |
| `spend` on `run_tick` | the only way rails above the model layer stay free |
| `AGENT_CONCURRENCY = 4` | four 15s calls in flight, same reason `SWEEP_CONCURRENCY` exists |
| `maxItems` on `actions` and `unknowns` | the first live answer returned 7 actions and 6 unknowns. A work order nobody reads to the end is not a work order |
| `WorkOrder` in `state.py` | `Finding` is the sub-agent-to-supervisor contract; this is a different object with a code-owned half |
| the grader tightening | defect 4 |

---

## M3 - The four responders

**2026-09-10. Done.**

M2 proved one agent end to end. M3 turns it into four and gives them a supervisor.
`allowlists.py` carves the 19 deployed tools into five slices, `agent_prompts.py` carries four
briefs plus the supervisor's, `workers.fan_out` runs the responders under one ceiling, and
`agent.synthesize` fuses their work orders into one shift report when two or more sensing worlds
opened incidents in the same tick. Still Tier 2 for everything; the cascade is still M7's.

Built in the mandated order, rails first: `allowlists.py` with its counting tests before any agent
existed, then the briefs, then the fan-out, then the shift report. **Rails before the agents they
constrain**, because a slice is easy to widen quietly once something is already failing.

### The write-tool question, answered before the slices were built

Between M3 and M6 there is nothing between a model and a PATCH against the live Care API. The
instruction was to declare the write tools so the asserted counts are real counts, and withhold
them from what the model is handed until the gate lands. That is what shipped, with two additions
the question did not ask for and both of which turned out to matter.

**Three functions, not one filtered list.** `tools_for` is the declaration, `bound_tools_for`
subtracts `WRITE_TOOLS` while `GATE_LANDED` is False, and `assert_callable` raises `WriteGateError`
from `mcp_client.call_tool`. They look redundant and are not: withholding a tool from the model
covers the model, and the runtime guard covers **us**. The next person to write a helper that calls
`consume_feed` directly is not a model.

**There are eight write tools on the deployed surface, not four.** `docs/architecture.md` marked
four with a `*`; the wire also has `assign_to_pasture`, `remove_from_pasture`, `assign_to_shelter`,
and `remove_from_shelter`. They are in no slice and belong in none - moving an animal between
places is a crew decision, not an inference from a sensor - but they are named in `WRITE_TOOLS`
anyway, because the ones nobody assigned are exactly the ones somebody adds later while chasing one
read out of the same API. Naming them means that edit trips the guard.

**`DEPLOYED_TOOLS` had no home in this repo until now**, which is why the rail "no agent names a
tool outside its set" had nothing to be checked against: the doc set names only the 15 that appear
in a slice. All 19 are written down, read off the wire rather than out of the frozen upstream's
source, and `--handshake` now fails on drift in **either** direction. That check belongs in the
handshake and not in `pytest`, because `pytest` has to pass on a plane.

### The number that came before `asyncio.gather`

Asked for and delivered before the fan-out was wired, then re-measured live at the boundary: two `--once` runs
against a freshly refilled ledger, 15 and 14 newly-opened across three worlds each, 104,500 and 91,468 tokens in,
85 and 96 seconds, zero rail failures. The tables and the arithmetic are the M3 rows in `docs/model-routing.md`.
Two things worth saying here: **a calm tick is $0.00 exactly**, and the supervisor is about 13% of the bill for one
call against fourteen, the cheapest thing in the tick per unit of value because it is the only call that reads
across worlds. Neither tick is a steady-state budget; M4 measures that.

### The experiment disagreed with its own hypothesis, which is why it was worth running

The one thing to prove rather than port: run a sub-agent with no brief, capture it flailing, pass the brief,
capture it working. `docs/transcripts/no-brief-transcript.md` and `docs/transcripts/with-brief-transcript.md`
are that pair, one variable.

**It did not flail.** The unbriefed answer **passed every rail with zero violations** and would have shipped.
What it did instead was invisible to code: one action instead of five, the neighbours' tanks handed to nobody by
name, the suspect gauge buried in `unknowns`, a headline promising an escalation its actions never contained.
The finding is the honest limit of the rail suite: **every rail asks whether an answer is defensible about its own
incident, and scope is not answerable from inside one work order.** Both answers are pinned in `test_agent.py`
and nothing became a blocking rail. The SOP did not rescue it either: standing orders describe the domain, the
brief says which part is yours. Cookbook #22.

### Four defects M3 caught in itself

1. **`SHIFT_REPORT_MAX_TOKENS = 1_024`, reasoned about instead of measured**, cut the first live fused report mid-object. Now 3,072, sized off a measured 1,646. Cookbook #13, second time.
2. **The truncation mislabelled itself as an all-clear.** A tool call cut at `max_tokens` still carries a partial `input`, so `payload is None` let it through to the rails. Both `synthesize` and `to_work_order` treat truncated as no answer. Cookbook #23.
3. **The grounding grader was asymmetric.** Timestamps were stripped from the page and not the prose; `compliance` quotes the date it was handed. Both sides now. Cookbook #11, reopened.
4. **`AGENT_CONCURRENCY = 4` would have meant sixteen** with four agents each bounding themselves. One semaphore, shared, and a rail that measures peak in-flight rather than asserting the constant. Cookbook #24.

### Divergence from the plan

**`agent_prompts.py` is a new module, and `MANDATES` moved out of `system_prompts.py`.** M2 put the
one brief it needed beside the schemas. Four briefs plus a supervisor's is a different thing from a
schema, and `system_prompts.py` keeps what a machine consumes while `agent_prompts.py` keeps what a
model reads. `docs/state.md` said the mandates lived in `system_prompts.py`; that line is now
corrected rather than left to be discovered.

**Four briefs, not five.** Chaos's brief arrives with chaos. It is not a responder, it is never a
route target, and writing its brief early would have been writing against a job that did not exist.
`RESPONDERS` and `AGENTS` are two names in `agent.py` for exactly this reason.

**`WorkOrder`, not `Finding`, is the sub-agent handoff contract.** The plan said `Finding`, which is
triage's code-owned output and reaches the supervisor with no model in between. What the supervisor
actually reads is `render_shift_page` over the work orders. `src/agent/CLAUDE.md` is corrected. This
matters more than a name: a sub-agent inherits nothing and the supervisor inherits nothing back, so
anything the supervisor needs has to be *in* the order rather than assumed to be in shared context.

**`herd_health` was handed nothing on both live ticks and logged nothing about it.** It cannot read
a sensor, so no sensor incident can route to it, and it stays idle until chaos writes real animal
events. That is `docs/state.md` decision 5 working, so it is not warned about: a line per tick per
idle agent trains everyone to ignore the log.

### The gate

**281 tests**, `ruff check .` clean, `mypy` clean on 29 source files, one alembic head. Live
verification, in five parts:

| Check | Result |
| --- | --- |
| `--handshake` | 19 tools, 160 sensors, 32 locations, 13 types, and the deployed list matches `DEPLOYED_TOOLS` exactly |
| `--once`, tick A | 15 packets across 3 worlds, 16/16 shipped, and the truncated supervisor found |
| `--once`, tick B after both fixes | 14 packets, 15/15 shipped, `shift_report=model`, `shift_report_violations=[]` |
| the cross-domain fusion the phase was supposed to prove | 3 worlds in one tick, both runs, so the storm front arrived for free exactly as `docs/state.md` predicted and the supervisor fused it rather than concatenating two unrelated pages |
| bare `python main.py` and `--api` | exit 3, naming M4 and M8 |

The chaos CLI's read-only commands (`status`, `plan --ticks 3`) were re-run too, since they are
in `docs/state.md` and this repo's rule is that a documented command is a command that gets run.

### Step 2 found two doc defects, and one of them is the interesting kind

**`docs/logging.md` still printed the M2 tick line.** `worlds`, `shift_report`, and
`shift_report_violations` were being written and documented nowhere, in the one file whose whole
job is saying what a line contains. Worse, that file's own stated rule is "a missing field means
the stage had nothing to say," so three undocumented fields were actively lying by that rule.
Fixed, with the pair-reading explained: a two-world tick reading `shift_report=code` is either a
free pass or a rail that fired, and the violations list is what tells them apart.

**`src/tools/CLAUDE.md` had one line about allowlists and nothing about the write gate.** The
root doc table points at it for exactly that subject. `docs/architecture.md` carried the
three-function explanation and the nested file, which is what a session reads before touching
`src/tools/`, did not. That is the drift this ritual exists to catch: the detail was written down
in the place a reader arrives at second.

**And the documented `jq` query is what would have caught defect 2 unaided.** Query two in
`docs/logging.md` selects any `finish_reason` outside the healthy set, and running it at the
boundary returns exactly one line: the supervisor at `max_tokens` with `output_tokens: 1024` and
`max_tokens: 1024`. The instrument worked and nobody had asked it. Reading the logs really is
the test of whether the logging works.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| `synthesize` runs **outside** the `spend` guard | every tick has to end with a shift report, including a free-pass tick and a tick where the model failed. It decides for itself: `spend` and `FUSION_THRESHOLD` both have to hold before it calls anything, so a free tick still gets a page and still costs nothing |
| shift-report fields on the tick line | `shift_report` (`model` or `code`) and `shift_report_violations` are how defect 2 was found at all. A stage that can silently fall back needs the fallback in the line |
| `bound_tools_for` and `assert_callable` | the withheld list covers the model; the runtime guard covers us |
| `WRITE_TOOLS` naming all eight | four of them are in no slice, which is exactly why they are named |
| `DEPLOYED_TOOLS` plus handshake drift check | the isolation rail had no universe to be checked against |
| `FUSION_THRESHOLD = 2` | the same claim `src/agent/CLAUDE.md` already makes about when cross-domain reasoning is worth paying for. M7 should read one constant rather than agree with itself twice |
| `run_agent` generalized, `run_water_feed` kept as a wrapper | `water_feed` is the only agent whose output has been measured against a real ranch, and the calibration fixture is a recording of that call |

---

## M5 - Chaos, built beside M3 rather than after it

**M3 was in flight in another session while this phase was built.** Two worktrees, one
`master`, one shared local Postgres, one live ranch. The ownership split was declared up
front: `src/tools/chaos.py`, `src/tools/sensors.py`, `src/agent/memory.py`,
`data/examples.json`, `tests/test_tools.py`, `alembic/versions/0002_*`, and `CHAOS_*` in
`config.py` were this session's; `agent.py`, `allowlists.py`, `agent_prompts.py`, and
`test_agent.py` were not touched. **Nothing in the ownership list needed crossing**, which
is worth recording because the split was a guess made before either phase started.

### The shape: two injection paths, and the asymmetry is forced, not stylistic

**Sensor faults are an overlay** in `sw_ops.chaos_events`, applied inside `sensors.sweep()`
and nowhere else. The deployed Sensor API is stateless and synthesizes every reading in
code, and its own fault injector refuses to arm when `AWS_LAMBDA_FUNCTION_NAME` is set, so
deployed prod cannot be faulted from the outside. That guard is correct and stays. **The
deployed API stays truthful and this repo owns the lie, in one function, under test.**
Triage never learns the difference, which is the whole point: a faulted ranch and a broken
one have to be indistinguishable from the monitor's side or the exercise proves nothing.

Putting the overlay inside `sweep()` also meant **`executor.py` needed no change**, which
mattered more than it looks: `executor.py` was the file most likely to be moving under M3.

**Animal events are written for real** through `PATCH /animals/:animalId` and
`POST /animals/:animalId/observations`, with no overlay at all, because `herd_health` has
to discover them through its own tools. An overlay there would prove the overlay works and
nothing about the agent.

### The write guards, and the one instruction this phase did not follow

`CHAOS_ALLOW_WRITES` defaults to `0`. The cohort is confined to the five animal ids already
in `.env`. Twelve rails cover the write path, the first of which monkeypatches
`upstream_client` itself to raise, so it proves **no client is ever constructed** rather
than that no request was sent. Every block writes a paired `proposed` / `decided` audit
line, so the receipt that nothing was mutated exists even on the path where nothing
happened.

**The instruction was to write that test before the write path, and it was not followed.**
`chaos.py` was written in one pass, write path included, and the rails came after. No write
ever executed under those settings and the guards are real, but the ordering was the point
of the instruction and it was skipped. Recorded here rather than smoothed over, because the
two defects below are exactly what test-first would have caught before the code shipped.

### Determinism is the deliverable, so `plan()` is pure and `choices` is banned

`plan(seed, catalog, ticks, cohort)` is a pure function: same arguments, same list of
events, forever. It rolls an explicit cumulative weight and uses `randrange`, never
`random.choices`, `sample`, or `shuffle`, because those three are free to change their
internal draw pattern between CPython releases and a fixture that survives an interpreter
upgrade is worth more than the two lines they save. A 14-row golden plan sits in
`data/examples.json` and a rail asserts equality against it.

The impure half is `inject_for_tick`, which is where the clock, the database, and the live
catalog live. Splitting them is what lets the golden rail run with no database and no
network.

**Every event carries a TTL and expiry restores the sensor**, which is what produces
`resolved` incidents and exercises reconcile. Without healing, everything is broken an hour
in and the feed goes quiet.

### Five defects this phase caught in itself

1. **The `PATCH` was pointed at the wrong service, and every rail passed anyway.** `/animals` is on the Farm API; a `respx` mock answers whatever host it is pointed at. Two clients now, and the rail mounts each on its own host. Caught by a live probe, not by the suite. Cookbook #16.
2. **The observation body was wrong in three ways at once** (`notes` for `note`, `observedBy` invented, `severity` and `observedAt` missing), and `restore`'s default status was not in the enum. All three enums are validated at load. Cookbook #17.
3. **A rail that asserted nothing.** `... if False else events` compared a value to itself. Replaced with a real read back.
4. **Three rails failing for the rail's own fault**, a custom `sensor_id` against a default `target_id`. The matching logic was right.
5. **A documented `jq` query wrong in three ways**, caught by step 2 running it: a file that does not exist, `event` where the field is `msg`, and a stream that is not pure JSON. Cookbook #21.

### An unauthorized write that cannot be undone

The animal contract was learned by sending deliberately invalid bodies to a nonexistent animal id and reading
the 422s (cookbook #17). The fifth probe body was valid, returned **201**, and created observation
`0328d7e2-d271-4410-907c-a84020c2c8c7` against the ghost id `zz-does-not-exist-0000`. Nothing real was touched,
and it was still an unauthorized write to a deployed service, disclosed before it was asked about. Observations
are append-only upstream and the repo is frozen, so it stays: `docs/open-issues.md` #9.

### Two verifications are deferred to the M3 boundary, unrun and unweakened

The brief named two checks that need M3's supervisor, and both are **deferred rather than
approximated**:

1. **A storm front produces one fused work order rather than two unrelated ones.** The
   scenario exists, fires four correlated faults in one `group_id`, and was verified live to
   produce three simultaneous criticals from one seed. Whether the supervisor fuses them is
   not testable until there is a supervisor.
2. **`herd_health` discovers the coyote kill through its own tools.** The scenario exists
   and the write path is built and guarded. `herd_health` does not exist yet.

Neither was faked and neither was weakened into something that passes. There is no skipped
test standing in for them, because a skipped test is a claim that the check exists.

### Divergence from the plan

**The executor wiring was deferred to M3 by decision, not oversight.** Chaos injection per
tick belongs in `run_tick`, and `executor.py` was the file most likely to be moving under
the parallel session. The overlay is applied in `sweep()`, so a tick already sees a faulted
ranch; what M3 adds is the per-tick call to `inject_for_tick`, which is three lines in a
file that will be stable by then.

**`tests/conftest.py` was not touched, and the `chaos_store` fixture lives in
`tests/test_tools.py` instead.** `conftest.py` was on neither ownership list and the other
session was closing a phase inside it. A local fixture is slightly worse code and was
obviously the right trade for one afternoon.

### The boundary found one more thing, and it is a parallel-session hazard

Between the CLI verification and the cleanup step, `sw_ops_test` lost `chaos_events` and its alembic stamp
fell from `0002` to `0001`. The other session's `pytest` had dropped and re-migrated the shared schema from its
own worktree, which had no `0002`. Twenty minutes looking for a bug that was not there. Cookbook #18.

### A doc defect found by step 2 and deliberately not fixed

`main.py`'s `once()` docstring still says "the free pass end to end, **no model involved**."
That has been false since M2: `once()` calls `run_tick` with `spend` defaulting to `True`,
and the run below billed 69,264 tokens. Root `CLAUDE.md` gets it right. `main.py` is on
neither ownership list and a one-line docstring change is not worth a merge conflict in a
shared entry point during a parallel build, so it is **reported, not fixed.**

### The gate

232 tests on the M5 branch alone, `ruff check .` clean, `mypy` clean on 29 source files. The DB rails genuinely
execute rather than skip, confirmed by suite time rather than by trusting the absence of an
`s`.

Live verification, in four parts:

| Check | Result |
| --- | --- |
| `--handshake` | 19 tools, 160 sensors, 32 locations, 13 types |
| `plan --ticks 6 --seed 1` against the **live** catalog | 9 events, `storm_front` at tick 2 on four real sensor ids |
| the overlay over a genuinely live read | honest sweep calm, same sweep faulted to `fence_down` + `freeze_risk` + `high_wind`, all critical, and the battery drift ramped to `power_low` at TTL end |
| `--once` against `sw_ops_test`, chaos off | 160 read / 0 failed, 20 findings, 11/11 work orders shipped, 69,264 tokens, exit 0 |

The overlay verification is the one that matters: **the honest read was calm and the faulted
read was critical, from the same live values, in the same tick.** And re-planning the same
seed against the live catalog returned an identical list.

`--once` was pointed at `sw_ops_test` rather than prod on purpose. The other session was
measuring Opus cost per tick against the prod ledger, and twenty new incidents in it would
have turned those numbers into noise.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| load-time enum validation in `parse_catalog` | defect 2. A status typo that only surfaces as a mid-demo 422 is not caught by anything |
| `overlay_events` on `SweepResult` | a tick line has to be able to say the ranch was being lied to, or a faulted demo is a mystery |
| `restore` refusing an invalid status | defect 2 again. The cleanup path is the one nobody runs until they need it |
| the `chaos` CLI's five subcommands | injecting by hand is how a demo gets driven, and `status` is how you find out why nothing is broken |
| the partial unique index on active events | one fault per target at a time, without deleting the history that makes a replay auditable |
| migration `0002` applied to prod `sw_ops` | additive `CREATE TABLE` only. Without it the feature does not exist in the database the demo reads, and the repo's head would disagree with prod |

### The animal write path has never run against the real API, on purpose

The contract is verified: the `PATCH` route and its status enum were read off the deployed
Farm API's 422s, and the observation body is verified by the strongest possible evidence,
which is that the accidental probe returned **201** with exactly that shape. What has not
happened is a deliberate write with `CHAOS_ALLOW_WRITES=1` against real animals. Given that
this phase already produced one unauthorized write, arming the guard to admire it was not the
move. It is a real gap and it is named here rather than implied by a green gate: **the guards
are proven, the contract is proven, and the happy path is proven only by mock.**

### The rebase, which is the part a parallel build actually risks

M3 landed two commits while this phase was being built, so `m5-chaos` rebased onto them
before landing. **One conflict, in `tests/test_tools.py`, and both halves of it were the same
kind of collision:** two sessions appending imports to one block, and two sessions each
adding a new numbered section to the end of one file. Resolved as a union, with the chaos
section renumbered from 3 to 5 because both sides had claimed a number and one of them was
already taken by the evidence section.

Nothing else conflicted. `docs/` merged clean only because the other session had not written
its own boundary entry yet, which is luck rather than design. The ownership split held: no
file on the other session's list was touched, and no rename or signature change crossed the
line. **The one interface that did cross was `sweep()`'s new keyword argument**, and it
defaults to `None`, which is why M3's `executor.py` compiled against it without knowing it
existed.

One prediction in `docs/architecture.md` was corrected rather than fulfilled. It said the four
animal-placement write tools "belong to `chaos`" and would leave `UNASSIGNED_TOOLS` at M5. **They
did not, and should not.** Chaos writes over REST because it is a harness, not a responder: no
judgment, no tool loop, no model, and no reason to sit behind the M6 human gate. Its slice stays
at zero tools, which M3's own count rail already asserts. That sentence in `architecture.md` was
the last thing this boundary fixed, and it was found only because the other session committed it
twenty minutes before this one landed.

**The gate was then re-run on the combined tree, and that is the run that counts:** 279 tests,
`ruff` clean, `mypy` clean, one alembic head, and `--once` against `sw_ops_test` shipping
**23/23 work orders on 161,851 tokens** with 160 sensors read and none failed. A phase that
only proves itself on its own branch has proved nothing about `master`.

That run also found a defect in the other phase's code: the supervisor's shift report
**truncated at `max_tokens=1024`** and fell back to `shift_report=code`. `docs/logging.md`
describes that exact signature as a config bug rather than a weak model, which is why it took
one line to spot. It is in the other session's file, so it is reported and recorded in
`docs/state.md`'s owed row rather than fixed here. Fixing another session's file quietly is
how a parallel build turns into a merge nobody can review.

---

## M4 - The continuous loop

**2026-09-10. Done.** Closed after M5 and M3, in that order, on one `master`.

`run_loop` wraps `run_tick` in `executor.py`: a fixed start-to-start cadence, a hard per-run
spend ceiling that halts, per-upstream exponential backoff, a held set for incidents whose
agent never answered, chaos wired into the tick with a miss check, and a shutdown that drains
the in-flight tick on the platform this actually runs on. `main.py` lost the `not_yet` branch
for the loop and gained `--no-spend`. Built rails first, as asked: every rule below had a test
before the loop body existed.

### The ceiling, answered first

**`SPEND_CEILING_USD`, default $10.00 per run, checked after every tick, and it halts.** The
run's `cost_usd` is summed off the tick lines; reaching the ceiling writes `loop_halted` with
the reason, the spend, the tick count, and the run id, and the process exits **4**. Not 0,
because a restart policy that relaunches on a clean exit would relaunch and spend again. Not 1,
because nothing is broken and an on-call person reading 1 goes hunting for an outage. The
overshoot is bounded at one tick, because a fan-out in flight is already paid for and cancelling
it wastes the tokens. There is no unlimited value: a non-positive ceiling refuses to start a
spending loop. `--once` is exempt, since a human is at the keyboard for that one.

**The premise needed one correction.** The brief said `tick.jsonl` already carried `cost_usd`.
It did not; `docs/logging.md` and `docs/model-routing.md` both scheduled it for M7 with the
pricing table. The routing ledger did already state an assumed rate of $15/M in and $75/M out
"so it can be corrected in one place," so that place became code: `ASSUMED_RATE_USD_PER_M` in
`llm_client.py`, `cost_usd` on every tick line from M4, and M7's pricing table replaces the
constant rather than introducing the field. A ceiling in tokens would have needed somebody to
multiply at 2am, and the multiplication is the thing that gets done wrong.

### The four things, and what each turned out to be

**1. A crashed agent.** Reconcile runs before any agent and reads triage's findings, so an agent
raising cannot close an incident; the first rail asserts exactly that and it passed on the
existing code. The real hole was one layer down: an incident whose agent raised, or whose model
call died in transport, gets a `no_answer` order, is `ongoing` on the next tick, and is
therefore **never re-routed and never worked.** `held` is the fix. The tick emits the keys of
retriable no-answers (`agent_raised`, `worker_raised`, `transport_error`, or a skipped spend
stage), the loop carries them, and the next tick re-routes any that are still open. A rail
rejection is never held, because a retry loop turns a rail into a sampler, and neither is
`max_tokens`, because that buys the same truncation twice. In-process only: the durable version
is a column on `incidents`, which is a Supabase migration and Scott's explicit yes.

**2. Cadence and TTL.** Already one decision by M5's construction: a TTL is `ttl_ticks` times
the cadence at injection, so changing the cadence rescales every scenario with it, and the
catalog's minimum of two ticks means every fault straddles two sweeps at nominal cadence. What
remained was the miss: a sweep in backoff while a short fault ages out. `SweepResult` now carries
`overlay_observed`, the event ids whose sensor the sweep actually read, and at heal time an event
this run injected that was never observed logs `chaos_event_missed` and lands in `chaos_missed`
on the line. Animal events are excluded because nothing observes them yet (see the coyote item
below). **The live run produced exactly one**, and it was real: `chaos-1-13`, a `stream_run_dry`
on `alkali-spring-flow`, injected by tick 11 seconds before that tick's sweep failed, expired four
ticks later during the outage, and healed on tick 16 having never been read.

**3. Per-upstream backoff.** Five keys: `mcp`, `sensor`, `sw_ops`, `evidence` (Farm, Feed, and
Care sit behind one stage, and telling them apart is `evidence.py`'s business), and `model`.
Exponential and deterministic, 60s base and 900s cap, with no jitter because one loop retrying
one service has no herd to break up. The loop itself never sleeps past one cadence. A free-pass
upstream inside its window skips the whole free pass and **the tick still writes its line**,
short, naming who is sick; a spend upstream inside its window runs the free pass and holds the
new incidents. The model never raises out of `fan_out`, so its outage is read off the orders:
every order dying in transport marks it down, one real answer clears it.

**4. Windows shutdown.** Nothing here calls `loop.add_signal_handler`. Python 3.11's
`asyncio.Runner` already turns the first Ctrl+C into a cancel of the main task and the second
into `KeyboardInterrupt`, so the tick runs behind `asyncio.shield`, the cancel lands in the loop,
the tick drains, the line is written, exit 0; a second Ctrl+C escapes and `main.py` returns 1.
SIGTERM and SIGBREAK go through `signal.signal` and set the stop event, which also wakes the
cadence sleep. **SIGBREAK is what made shutdown scriptable**: it is the one interrupt Windows
delivers to a child process, and the live run was stopped with it.

### The free run: 30 ticks, one dead upstream, two rotations, $0.00

Run `c45d85b664a1`, `--no-spend`, `SW_OPS_TARGET=test`, cadence **60s**, chaos armed on the
test store, `LOG_ROTATE_BYTES=8000`, `LOG_DIR=logs/m4-free`. The Sensor API was reached through
a forwarding proxy on localhost, because settings are read once and cached so nothing can be
repointed mid-run; killing the proxy is the upstream dying (`docs/cookbook.md` #27).

| | |
| --- | --- |
| ticks | **30 in 30 minutes**, one line each, 02:29:16 to 02:58:16 UTC, every start on the minute |
| a healthy tick | 3.0 to 3.6s, 160 read, 0 failed, 10 to 24 opened |
| the kill, at tick 11 | sweep failed in 18s with `all 160 reads failed (transport)`; `sensor` backed off 60s |
| ticks 12, 14, 15 | **skipped, and each wrote its line**: `skipped_upstreams=["sensor"]`, `error=null`, 0 to 1ms |
| tick 13 | attempted, failed again, backoff 120s |
| tick 16 | proxy back one minute earlier; sweep succeeded, `upstream_recovered after_failures=2`, and the missed chaos event healed |
| chaos | 30 events injected over 30 ticks, all `sensor_overlay`, 1 missed and reported |
| rotation | three files (`tick.jsonl`, `.1`, `.2`), one `run_id` across all of them, and `sw_ticks` joined them on it |
| shutdown | `CTRL_BREAK_EVENT` at 02:59:12 during the cadence sleep; `stop_requested` and `loop_stopped` in the same millisecond; **exit 0** |
| cost | `cost_usd: 0.0` on every line, `spent_usd: 0.0` at stop |

### The paid run: two ticks, then the ceiling did its job

Run `f1376b4e8bbc`, prod ledger, `CHAOS_ENABLED=0` set explicitly (see defect 2), cadence 120s,
`SPEND_CEILING_USD=3.00` so the halt would be exercised for real if the ranch was busy. It was: 12 then 14
newly-opened, 26 work orders, 26 shipped, 0 rejected, 0 no-answer, 0 held, both reports fused. After tick 2 the
run stood at **$4.70 against a $3.00 ceiling**, `loop_halted` was written with `reason=spend_ceiling`, and the
process **exited 4** on its own before a third tick started. The overshoot is the one tick the design allows. The
table and the steady-state finding it overturned: the M4 rows in `docs/model-routing.md`.

### Divergences from the plan

| Planned | What shipped | Why |
| --- | --- | --- |
| `cost_usd` at M7 | at M4 | the ceiling is in dollars; see above |
| exits 0 / 1 | plus **4** for the ceiling | a halt is neither clean nor broken, and a restart policy has to be able to tell |
| kill by pointing at a bad URL | kill by stopping a proxy in the path | settings are read once and cached; repointing needs a restart, which is not a mid-run failure |
| rotation "will be real" | forced with `LOG_ROTATE_BYTES=8000` | 30 tick lines are 18KB against a 10MB cap; nothing rotates in 30 minutes at the default |
| a held column | an in-process held set | a column is a Supabase migration and needs an explicit yes |
| the coyote verification | **still owed, and reframed** | nothing in the tick reads the Care API, so `herd_health` has no discovery path regardless of chaos or the supervisor. That is a new free stage plus a triage category, not three lines, and it is its own scoped item |

### Five defects M4 caught in itself

1. **A dead Sensor API was a green tick.** 160 per-sensor errors and zero readings raised nothing, so backoff never fired. `run_tick` raises when the sweep has errors and no readings. Found by predicting what the live kill would show, and the prediction was "nothing." Cookbook #25.
2. **The test suite was reading Supabase.** `.env` had `CHAOS_ENABLED=1` and `resolve_store()` defaults to prod. An autouse `chaos_off` fixture disarms both call sites. Cookbook #26.
3. **The brief's premise about `cost_usd`** (above).
4. **Loop-level lines said `tick=0`.** A task copies its context, so the loop binds the tick before creating the task.
5. **Two fixtures in the loop rails lied about the loop**: a fake sleep that did not advance the clock, a fake skipped tick that did not report its skip. Both were the fixture.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| `--no-spend` | the free verification run needs a documented way to run the loop with the paid stages off, and an env var for "do not spend" is the flag nobody sets |
| `LOG_ROTATE_BYTES` | rotation could not otherwise happen inside the run that is supposed to prove it |
| the all-failed-sweep raise | without it the kill test would have proved nothing |
| `chaos_off` in `conftest.py` | see defect 2 |
| `overlay_observed` on `SweepResult` | the miss check has to know which faults were seen, and only the sweep knows which sensors answered |
| `skipped_upstreams`, `held`, `chaos_fired`, `chaos_healed`, `chaos_missed` on the tick line | every rule the loop enforces has to be readable off the line, or the line cannot tell a heartbeat during an outage from a calm ranch |

### Addendum, same day: the debounce. One bad read is not an incident

The paid run's finding was that every tick is a storm tick, and the plain-language version of
why is this: the deployed Sensor API rolls fresh dice on every read, a healthy tank comes up
"empty" one sweep in a while, we paid a model to write a work order about it, and it fixed
itself five minutes later. Ten to twenty times a tick. Scott asked for the fix the same day.

**`INCIDENT_CONFIRM_SWEEPS`, default 2, and migration `0003`.** A finding has to be present on
two consecutive sweeps before it opens. Until then the row is `pending`: live in the ledger, so
the unique index and the unread guard both cover it, but not in `opened`, so nothing is routed,
paged, or billed. A pending row whose sensor reads clean is `dismissed`, not `resolved`, because
nothing was ever alarmed; the row is kept so the churn stays countable. Unread is not read-clean
for a pending row either, so an outage during the confirmation window holds rather than dismisses.
The partial unique index now names the live set (`pending`, `opened`, `ongoing`) instead of
saying "not resolved", because with two terminal states those stopped meaning the same thing.
Setting the knob to 1 restores open-on-first-sight, which the rails whose subject is something
else use through a `first_sight` fixture, and which the rail table in `tests/CLAUDE.md` says not to
reach for otherwise.

**Migrated on Supabase with an explicit yes**, `0002 -> 0003`, 2026-09-10. The downgrade
promotes pending to opened and dismissed to resolved before rebuilding the old index, so it can
actually run.

**Three free ticks on the prod ledger, twenty seconds apart, right after the migration:**

| | would have opened | opened | dismissed | routed |
| --- | --- | --- | --- | --- |
| tick 1 | 14 | **0** (14 pending) | 0 | nobody |
| tick 2 | 17 | **2** | 12 | compliance 1, water_feed 1 |
| tick 3 | 21 | **1** | 16 | water_feed 1 |

Twelve of tick 1's fourteen pending rows read clean on tick 2 and were dismissed. Two were still
bad and opened. That is the dice being filtered out, and at the measured $0.18 per work order it
takes the bill from roughly $2.30 a tick to roughly $0.20 to $0.40. Every chaos scenario has a
TTL of at least two ticks and the seven permanently-bad sensors are bad on every read, so
nothing that is supposed to be found is lost; the chaos end-to-end rail in `test_tools.py` now
shows the fault pending on its first sweep and opened on its second, which is the honest shape.

Gate after the change: **310 tests**, `ruff` clean, `mypy` clean, one alembic head at `0003`.


## M6 - The gate and validation

**Landed 2026-09-11.** A work order may now carry a `proposed_write`; three code checks decide
whether it pauses at all; the pause is a LangGraph `interrupt()` checkpointed in `sw_ops` so it
outlives the process; a human answers from `python -m src.agent.gate`; both halves land in
`audit.jsonl` under one `audit_id`; `GATE_LANDED` flipped last. Migrations `0004` (the
checkpointer's tables) and `0005` (`incidents.held_reason`, closing `docs/open-issues.md` #3) ran on
Supabase with an explicit yes. **353 tests**, `ruff` and `mypy` clean, one alembic head at `0005`.

### The three questions, answered before code

**The mechanism.** Scott's default was a pending-writes table: same durability, no graph runtime.
The first answer agreed with it and argued the case: `interrupt()` resumes a computation, and there
is nothing to resume, because the work order is one forced tool call already complete when the
proposal arrives. Scott's reply was the one that mattered: part of the project's purpose is to learn
LangGraph, and through M5 nothing in `src/` imported it. So the design became **one tiny graph per
proposal** (`ask` interrupts, `execute` performs) rather than the whole tick as a graph, which
keeps both requirements: the pause outlives the process, and one waiting question does not stop
the ranch being watched. The tick stays hand-wired. Code owns the checks, the key, the audit lines,
and the duplicate suppression; LangGraph holds the pause and decides nothing.

**The verification.** "Let a tick pause on a `create_observation`" cannot run: `herd_health` is
handed nothing because no stage reads the Care API, and M6 did not absorb that (issue 1, now also
issue 11). The mechanics were verified with a planted `restock_feed` on `sw_ops_test`, and the
channel live on whatever `water_feed` actually proposed, which was nothing. Below.

**The migration.** Both, asked once with the full DDL, run after the tests were green.

### What was found before the first line

**There was no proposal channel.** `WORK_ORDER_SCHEMA` had no write field, `WorkOrder` had none,
and `Finding.proposed_write` was set by nothing. The plan's M6 assumed a model could already say
"someone should restock this," and it could not. The field was added to the schema as a required
object with `tool` and `args`, `tool` empty meaning none, and described neutrally: none is the
usual answer, it is a proposal a person approves, and every id and quantity must be on the page.
The brief's paragraph about it is **rendered from the allowlist**, so the names the model is told
and the names `check` validates cannot drift.

**The write tools' argument shapes were not written down anywhere in this repo.** Read off the wire
from `tools/list` on 2026-09-11, the way `DEPLOYED_TOOLS` was, into `WRITE_TOOL_ARGS` with a kind
per argument: `id` and `number` graded against the page, `enum` and `timestamp` against
themselves, `text` not graded because a `reason` cannot be on the page verbatim.

### The spike, and what it measured

Sixty lines against `sw_ops_test` before `gate.py` existed answered five questions about the checkpointer in one
run (cookbook #34): the pause survives a new connection; the metadata filter finds only the run that passed it;
a resume on a finished thread is a silent no-op, so `decide()` checks for a live interrupt itself; an unknown
thread is empty `values`; and the node runs twice, so the `proposed` line is written outside the graph (#29).
Two platform facts came with it: `AsyncPostgresSaver` refuses Windows' default event loop, so the checkpointer is
the sync saver in a thread (#30), and `setup()` would create tables on prod silently, so migration `0004` runs
the library's own DDL through alembic and `memory.checkpointer()` refuses a database behind the library (#31).

### The rails, in the order they were written

The audit rail first: every `audit_id` appears exactly twice, **or once while its pause is still
open**, and `gate.unpaired_audit_ids` minus `gate.pending` is empty. The plan's wording ("exactly
twice") could not survive a visible open pause, and a visible open pause is the requirement.

Then the planted-bad-proposal suite: twelve `water_feed` fixtures and five `herd_health` fixtures,
each asserting **which** code fires, alone. `write_shape_invalid` (not an object, no args, unknown
or missing argument, string quantity, bad enum, bad timestamp), `write_tool_not_allowed` (a read
tool, another agent's write, a write in no slice), `ungrounded_write_arg` (an id the page never
printed, a quantity it never printed, and `6.7` against a page that says `16.7`, because the grader
compares number tokens, not substrings). A proposal wrong in two ways names the first wrong thing
only. **None of the three blocks the work order**: the prose ships, the proposal is stripped, the
code is recorded. `write_key_unknown` is checked in `executor._gate_step` against the routed set,
with the checkpointer patched to explode so the rail proves the gate was never opened.

Then the pause on Postgres: propose on one connection, list on another; the same write for the
same incident asked once (and a different tool for the same incident asked separately); reject then
approve with the `Approval` object arriving at the injected performer; a write that dies on the wire
still gets its `decided` line and is not retried; a proposal the checkpointer cannot persist is
`dropped` with a paired line rather than dangled; two ticks, one pending write; the gate
unreachable holds the incident with reason `gate_unavailable` and the tick still reports; the held
set survives a restart; the loop starts from what the previous run was carrying; the CLI lists,
approves, rejects, and refuses a second answer.

### The planted live check, on `sw_ops_test`

Two proposals planted through `propose()`. `python main.py --no-spend` at a 15s cadence, two ticks,
then `CTRL_BREAK_EVENT` from a driver process while asleep in the cadence: `stop_requested
SIGBREAK`, `loop_stopped`, **exit 0**, and both pauses still listed by a new process with one audit
line each. The drain answered nothing. Then `reject` with a reason (`not_executed`, 72s to
decision) and `approve` before the flip, which came back **`transport_McpUnavailableError`**: the
belt held, but `ranch_session` had wrapped the `WriteGateError` as an outage. `perform_write` now
checks `assert_callable` before it opens a session, so a refused write reads as a refusal. A
second `approve` on the rejected id was refused naming the earlier decision. `list` then read
zero. `unpaired_audit_ids` over the real file, after the test-fixture lines below were removed,
read empty.

### The two paid attempts, prod ledger, chaos off, after the migration

| | attempt 1 | attempt 2 |
| --- | --- | --- |
| opened / pending / dismissed | 1 / 12 / 20 | 2 / 17 / 10 |
| routed | `infrastructure` 1 | `water_feed` 2, both `feed_low`, SOP `feed.md` |
| work orders | 1/1 shipped, 0 violations | 2/2 shipped, 0 violations, both `escalate` |
| tokens, cost | 6,929 + 834, $0.17 | 10,588 + 2,127, $0.32 |
| `writes_proposed` | 0 | 0 |

**No live tick proposed a write, and that is the recorded result.** Attempt 1 routed to an agent
with no write in its slice, so its brief carried no proposal paragraph at all. Attempt 2 handed
`water_feed` two feed-low incidents with `restock_feed` and `consume_feed` named in its brief with
their arguments, and it returned `tool: ""` on both, with no shape, tool, or grounding violation
logged. The prompt was not steered to change that. What the attempts did verify: the schema with
the new required field is accepted by the API, three real answers parsed with the field present,
the `writes_*` fields are on the line (`writes_pending: null`, the gate was never opened), and the
per-order cost is unchanged from M3 at roughly $0.16 to $0.17. The live pause with a real
proposal remains to be seen the first time an agent judges a packet where the standing orders
call for a restock, and `write_paused` in the console stream is the line that will say so.

### Divergences from the plan

- **Per-proposal graphs, not the tick as a graph, and not a pending table.** Above.
- **The audit rail's wording changed** from "exactly twice" to "twice, or once while pending."
- **`create_observation` was not the verified write.** Issue 11.
- **`gate.py` is a new leaf** the plan's tree did not have; the tree names it now.
- **The held column is `0005`, not the `0004` issue 3 predicted**, because the checkpointer took `0004`.
- **The `decided` line carries more than `docs/logging.md` promised**: `tool`, `incident_key`,
  `reason`, `upstream`, and `result` is a code rather than a status number. Corrected there.

### Six defects M6 caught in itself

**1. The audit rail counted commentary as receipts.** `write_proposal_dropped` mentions the
audit id; the rail read a `dropped` proposal at count three. Only `phase` in `{proposed, decided}`
counts now (#32).

**2. Nine fixture lines in the real `logs/audit.jsonl`.** `alembic/env.py` calls
`configure_logging()`, the session-scoped `migrated_store` runs before any function-scoped patch,
and `configure_logging` is idempotent, so every file handler in the test process pointed at the
repo's `logs/` and any audit line written outside `capture_logs` landed in the receipt file. Both
fixtures point `log_dir` at a temp directory now, a rail asserts no configured handler points inside
`logs/`, and the leaked lines (nine `proposed`, six orphaned `decided`, two more on the next run)
were removed by hand (#33).

**3. The belt's refusal read as an MCP outage.** Above; `assert_callable` before `ranch_session`.

**4. The loop's held restore broke a loop rail.** `run_loop` reading the ledger at start hit the
loop rails' deliberately bogus URL and ate a two-second timeout. The restore is resolved at call
time and the test module stubs it; the two rails about restoring pass their own.

**5. `writes_pending: 0` would have lied.** A tick with nothing to propose never opens the gate, so
it cannot count what is waiting. The field is `None` on that tick and the line says so.

**6. `_Cli` and an unused import** left over from a first draft of the CLI, caught by ruff.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| `proposed_write` on the schema and `WorkOrder` | there was no channel; nothing could be gated |
| `WRITE_TOOL_ARGS` and the rendered brief paragraph | the shape check needs a contract and the model needs the same names |
| `Approval`, and `assert_callable(approval=...)` | otherwise the flip turned the runtime belt off for the next helper script |
| `ThreadedPostgresSaver` | the async saver does not run on this platform's event loop |
| migration `0004` from the library's own DDL, and the "behind the library" refusal | `setup()` is an unreviewed prod migration |
| `write_key_unknown` in `_gate_step` | the key is code's, and "cannot happen" is what a hand-built work order will say |
| the test-suite log directory fix | a receipt file must never carry a test |
| `src/agent/gate.py` as a leaf | the tree had no home for a gate and its CLI |

## M7 - Model routing, measured and not adopted

**What was planned.** Tier 1 on Ollama, the cascade, the real pricing table, one job moved down at
a time with a rail and a ledger row. Three pending rows in `docs/model-routing.md`.

**What actually happened, in order.**

**The ledger was wrong about what the jobs were, and that was the first fix.** The three pending rows
named chaos observation prose (M5 made chaos pure code), packet judging, and the work-order write
(one call since M2). Two model jobs exist: the per-incident work order and the fused shift report. The
rows were rewritten before any code.

**The design conversation changed the predicate.** Scott worked it from the readings up: code owns
detection, code owns the page, the model writes; where can a cheaper model still miss? Not in
finding, which is code's, but in reading the page thinly and in the cross-world pattern, which only
the shift report sees. Two consequences, both decisions now (`docs/state.md` 28 and 29): "two or
more worlds" gates fusion only and is not a per-incident trigger; and escalation is a rewrite from
the identical page, triggered after the call by a rail rejection, `insufficient_information`, a
proposed write, or no answer, and before it only by critical.

**Three findings from the reads before a line was written.** The assumed rate was 3x the Opus 5
list price (cookbook #38). Thinking on Opus 5 is adaptive by default and `budget_tokens` is rejected,
which nothing here sends (`docs/open-issues.md` #13; 64 real calls, zero thinking blocks). And "proposes a
write" cannot be known before the call, so it became a post-call trigger.

**Built.** `routing.py` (price table, `tier_for`, `escalation_reason`), `call_tier1` on `ChatOllama`
with `format=` carrying the schema, the cascade inside `judge_packet` with the Tier-1 receipt kept
on the stored order, `insufficient_information` on the schema, `tier` / `tier1_orders` /
`escalations` / `escalation_reasons` on the tick line, `TIER1_ENABLED` and `TIER_COMPARE`,
`logs/compare.jsonl`, `normalize_citations`. Sixteen new tests including the planted local
all-clear that must escalate, the per-reason escalations, compare mode, and the tick line. 369
green, ruff and mypy clean.

**Measured before the first live local tick, as asked.** One real packet through Ollama: 3,024 to
3,586 tokens in against `num_ctx` 16,384 (cookbook #35), 4 to 13s warm, 22s cold, two concurrent
calls overlapping partly. Two problems on the first five calls: whole headings copied into
`rules_cited` (#37, trimmed in the parser) and `insufficient_information` on 5 of 5 (#36, wording
tightened once, then 2 of 5 water and 4 of 5 feed). The wall-clock answer for a 14-incident tick:
about 60s of local calls plus up to 60s of escalations, inside 300s.

**Three measured ticks, test ledger, cascade and comparison on.** 12 opened, 12/12 shipped, 0
rejected, 0 all-clears, 0 invented rules. 6 critical went straight to Opus. Of 6 Tier-1 candidates,
5 escalated on `insufficient_information` and 1 stayed local. Side by side on the six pairs
(`docs/transcripts/m7-compare-transcript.md`): the local model named the flagged neighbour 1 of 3 (Opus 3 of 3),
the head count 1 of 4 (Opus 4 of 4), padded actions with echoes of its brief on 3 of 6, and proposed
writes outside its slice on 4 of 6 (all stripped or escalated before the gate). $0.74 for the run
at the real rate, shadows excluded.

**Verdict.** No move. `TIER1_ENABLED` ships off. The local model was never unsafe, and it saved one
call in twelve while being thin about the two things a rancher reads the order for. The row says
so, and it names the change that would earn the next attempt: the forecast on the feed page
(`docs/open-issues.md` #12), so the honest answer stops being "I do not know."

### What diverged from the plan

| Planned | Happened | Why |
| --- | --- | --- |
| three Tier-1 jobs to move | two jobs exist; one measured, one kept at Tier 2 by decision | chaos is code, assembly is code, judging and writing are one call |
| "2+ worlds" escalates per incident | gates fusion only | the world count says nothing about one packet; per incident it is the whole storm-tick bill |
| a write proposal escalates before the call | after it, as a rewrite | the proposal is in the answer |
| a Tier-1 rejection escalates | plus `insufficient_information`, `proposed_write`, `no_answer` | each is a different fact at 2am |
| the job moves | it does not | the row |
| `cost_usd` at the old rate | per model, 3x lower | the list price |

### Defects the phase caught in itself

1. **The rate.** `(15, 75)` against a list price of `(5, 25)`. Every dollar since M2, three docs.
2. **A backspace in a regex.** The first `_RULE_ID_PREFIX` was written through a shell heredoc that
   turned `\b` into a literal backspace; the trim silently matched nothing and a test caught it.
3. **The measurement script's write in an async function** tripped `ASYNC240`; moved to the caller.
4. **`judge_packet` was not imported** where the new tests used it, and the tick-line test needed
   two incidents to show two tiers. Both found by the tests failing, both trivial.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| `normalize_citations` and `rule_citation_trimmed` | four of five correct local citations would have escalated over format |
| `tier1_*` receipt fields on `WorkOrder` | the tick line and the row have to be readable off the stored orders |
| `TIER_COMPARE` and `logs/compare.jsonl` | the row needs the same page to both models, and the ranch redraws every reading, so it cannot be reconstructed later |
| `FALLBACK_RATE` with one warning | an unknown paid model must over-count toward the ceiling, never bill at zero |
| `keep_alive=30m` | Ollama's default equals the cadence; the model would reload every tick |
| `tier1_context_full` | the config bug the design warned about, made to announce itself |
| the `insufficient_information` wording tightened once | 5 of 5 on the first packet was the description, not the model; a second pass was not tried because the rest is the page (#12) |

## M7A - The herd sweep, the coyote gap

**What was planned.** The M7A paragraph in `docs/plan.md`: a second free sweep off the Farm and Care
APIs, animal categories in triage, migration `0006`, `knowledge_base/herd.md`, `herd_health` handed
its first real work, and one paid run that closes `docs/open-issues.md` #1, #2, #11, and #15. Two wire facts
to read first: the herd count, and whether observations list ranch-wide.

**What actually happened, in order.**

**The two wire facts, and a third the plan did not know to ask for.** The herd is **1,195 head**
(1,025 cows, 156 sheep, 10 horses, 2 donkeys, 2 goats, 18 pastures), every one `active`, every
`updatedAt` the seed stamp. Observations list **per animal only**: `GET /observations` is a 404, the MCP
tool requires `animalId`, and every since-style parameter is silently ignored. And `GET /animals` runs at
roughly 90 ms a row: `limit=500` hits the API Gateway 30s wall and returns 503 every time, so the
catalog cannot be read in one call. The `status` filter was honoured and validated, so the first design
was four cheap reads: the pasture roster (1,195 `animalIds` in one 1.2s call) plus one filtered read per
non-active status, observations only for the changed set. Gate green at 404 tests on that design, and
one free live tick found the three overdue care tasks the wire had shown (cow-0777's down-cow recheck a
month late, cow-0512's pinkeye patch, sheep-0001's shearing), which Scott kept live.

**The first live kill broke the design, twice, and both breaks are now rails.** `chaos inject --tick 32`
PATCHed cow-0905 to `deceased`. The Farm API **nulled her `pastureId`**, so she left every roster, and
`GET /animals?status=deceased` **returned nothing** with her sitting in the unfiltered list as
`deceased`. A filter that validates its input and excludes a dead cow from `status=active` still cannot
find her by `status=deceased`. So the catalog became the full list, paged at 100. Then the pages failed:
at 12 or 20 in flight the Farm API returned HTTP 500 on 2 of 12, every time; at 6 in flight, 12 of 12,
five times running. Pages go out in waves of `HERD_PAGE_CONCURRENCY = 6` and stop at the first short
page: 12 pages, about 18s, and the herd stage never fails the tick. The restored cow, `active` and on
no roster, is vouched for by the list, and every animal with a live incident is in the changed set so
her packet always has a record (`watch`, read off the ledger by `memory.live_animal_subjects`).

**The ledger reset under the run, twice.** `conftest.migrated_store` drops `sw_ops_test` and
re-migrates it, and the demo was on `sw_ops_test`. A full `pytest` mid-run, and later a `-k` selection
that still touched a store fixture, each wiped the pending rows and the active chaos events. Cookbook
#41. No pytest from then until the run was done.

**The pause that would not come.** Ticks C, E, and F opened `cow-0905:deceased` and the three
care_overdue and routed them to `herd_health` and nobody else. Seven herd orders, all naming the animal
by id and tag, quoting the coyote note word for word, citing `HERD-01` and `HERD-05`, escalating to the
general manager on the predator note, and one catching the restore note sitting against the deceased
status and flagging the conflict. **`proposed_write` was null on all seven.** Opus was following the SOP:
`HERD-05` says never fabricate an observation, so every order told the person on site to record what
they find. The verification asked the model to propose the one write the SOP told it not to invent.
Scott's call: a legitimate write, not a steered prompt. `HERD-07` says the finding itself is recorded in
the care record, once, quoting only what is on the page, with a timestamp from the page. One more tick
pair: `write_paused` on `cow-0905:deceased`, `create_observation` with the note built from the page and
`observedAt` the Farm API's own update stamp, `audit_id 968e7f64fb7540dc9adeed547d15e802`. Approved at
the CLI by scooter after 49s, the note landed on the deployed Care API as observation
`2dd60c7d-b9a2-413d-b17c-3cae46563d64`. The audit stream carries chaos's `auto_allowed` pair (twice, the
kill was fired twice) and `herd_health`'s `proposed` / `decided` pair in one run.

**The storm front, fused.** Tick H opened the kill and `storm_front` together: 18 orders, three worlds,
17 shipped, `linked` naming eleven keys including `met-tower-wind:high_wind`,
`antelope-ridge-fence:fence_down`, and `home-place-temp:freeze_risk`, and fusing
`cow-0777:care_overdue` into the Red Canyon water run ("one drive covers a freezing tank behind 111
head, a 32-day-overdue recheck on a cow that was not rising, and a fence with no push"). That is the
supervisor joining the herd to the tanks, which is the line the cow's packet exists to protect.
`docs/open-issues.md` #2 closed. Then `chaos restore`, and `cow-0905:deceased` resolved on the next tick.

| tick | what | cost |
| --- | --- | --- |
| A | first sighting after the kill, everything pending, herd stage failed on 3 pages of 21 in flight | $0.00 |
| B | 10 sensor orders, fused; herd failed on 6 pages | $0.66 |
| C | paged list whole: deceased + 3 care_overdue opened and routed to herd_health, 3 worlds, 1 herd order rejected on `severity_mismatch` and `write_shape_invalid` | $0.42 |
| D | (ledger reset by pytest) first sighting again | $0.00 |
| E | 3 care_overdue orders, 0 rejected, 3 worlds | $0.70 |
| F | kill and storm opened, herd_health 1, 0 violations, fused | $0.29 |
| G | (ledger reset on purpose, `HERD-07` in the SOP) first sighting | $0.00 |
| H | 18 opened, **`write_paused`**, fused with `linked`, 1 infrastructure order rejected | $1.16 |
| I | after restore: `cow-0905:deceased` resolved, 5 sensor orders | $0.34 |
| | **ten test-ledger ticks** | **$3.57** |

Under the $5 Scott set, and about 3.5x the $1.00 estimated, because the design changed twice mid-run
and the ledger reset twice, so the permanently bad sensors were paid for four times over.

**Built.** Migration `0006` (`subject_id` / `subject_type`, applied to Supabase with Scott's yes),
`SUBJECT_ANIMAL` and `is_animal` on `Finding` and `Incident`, `tools/herd.py` (the paged list in waves,
the roster as context, the care tasks, observations for the changed set, `HerdSweepResult.answered`),
`triage.triage_herd` and the four animal categories, `ROUTES` for `herd_health`, the animal page in
`evidence.py` with no sensor reading on it, `knowledge_base/herd.md` (HERD-01 to HERD-07), the herd stage
in `run_tick` with `herd_animals` / `herd_errors` / `herd_error` on the tick line, `read_subject_ids` in
reconcile, animal events in the chaos miss check, `memory.live_animal_subjects`, and `LOG_TRANSCRIPTS`
made real. 407 tests, ruff and mypy clean.

### What diverged from the plan

| Planned | Happened | Why |
| --- | --- | --- |
| herd catalog once, observations for changed animals | the roster plus the whole list paged in waves of 6, observations for the changed set | the status filter cannot find a non-active animal and the Farm API 500s above 6 pages in flight |
| `reconcile` takes the subjects that answered | yes, and the list is the vouching, never the roster | a deceased PATCH nulls `pastureId`; the restored cow is on no roster |
| the model proposes `create_observation` | it did not, seven times, because the SOP forbade fabricating one | `HERD-07` names the one legitimate note; Scott's decision |
| one paid run, under $5 | ten ticks, $3.57 | two design changes and two ledger resets mid-run |
| `LOG_TRANSCRIPTS=1` writes full bodies | it wrote nothing; `write_transcript` had no caller since M0 | wired into `judge_packet` and `synthesize` |

### Defects the phase caught in itself

1. **The status filter design shipped green and was blind to the first real kill.** 404 tests passed against
   fakes that honoured `status=deceased`. The wire did not. Cookbook #39.
2. **The Farm API's concurrency ceiling** was never measured before 21 requests went out at once. Cookbook #40.
3. **`pytest` drops the demo ledger.** Cookbook #41.
4. **`write_transcript` was dead code** and the flag documented in `docs/logging.md` did nothing.
5. **A shadowed `watch` Stopwatch** took 24 tests down for one rename; mypy named it.
6. **respx route order.** A specific route added after a helper's catch-all never matches, and an identical
   pattern silently replaces; two rails were asserting against the wrong mock until they failed.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| `HERD_PAGE_CONCURRENCY` and wave paging | the herd stage failed 2 ticks in 2 at the sweep's concurrency |
| `memory.live_animal_subjects` and `watch` | a restored kill could never resolve otherwise |
| `HERD-07` | the verification was impossible under the SOP as written; a rule, not a prompt tweak |
| `write_transcript` wired in, with `current_tick` | the herd order's prose was unreadable after the tick, and `linked` with it |
| `herd_roster_disagrees` warning | the roster and the list are two sources; a disagreement beyond the pastureless is worth a line |

## M8 - The read API

**What was planned.** `docs/plan.md`'s M8 paragraph and `src/api/CLAUDE.md`: FastAPI, five routes
(`/health`, `/ops/incidents`, `/ops/report`, `/ops/stream`, `/ops/gate`), the ranch's `{data, meta}`
envelope and error shape, `X-Request-ID` on every request, and the one rule that decides everything
else: the API never calls the ranch and never calls a model, it reads `sw_ops`. Scott's brief added two
questions to answer before code, and one issue to decide rather than defer.

**What actually happened, in order.**

**The two questions, answered in the check-in.** *What can the API actually read?* Two of the five
routes had no source: the shift report was synthesized every tick and written to `logs/`, and the only
record of a tick was a line in `logs/tick.jsonl`, which a separate process cannot see. So migration
`0007` adds `ticks` (typed columns for what the API sorts on, the whole line as `fields` jsonb, because
the line grew five fields at M7 and three at M7A and a column per field is a migration every phase) and
`shift_reports` (the page plus `incident_keys`, the keys it was handed). `run_tick` writes both beside
the log line, best-effort, after it: the line is the heartbeat, the row is the projection, and a ledger
that cannot take the row is a warning, never a failed tick. *Who may approve a write over HTTP?*
`OPS_API_TOKEN`, `name:secret` pairs, sixteen-character floor, `compare_digest`, `--api` refusing to
start without one (exit 2), `decided_by` from the token's name and never from the body (a body that
names one is 422). Reads stay open.

**Issue #10, decided.** The M6 note assumed `/ops/gate` would run in the loop's process. It does not,
so the API became the second writer on `audit.jsonl` the issue was about. A third table in `0007`,
`audit_receipts`, primary key `(audit_id, phase)`: the "exactly twice" rail as a constraint. `propose`
writes the row before the file line through the checkpointer's own connection
(`ThreadedPostgresSaver.record_receipt`, since the process holding the pause holds that connection),
`_execute` the same for `decided`. The file is a projection. The chaos guard's pairs stay file-only and
that is #20.

**The spec grew a sixth route, deliberately.** `GET /ops/gate`, the CLI's `list` in an envelope. The
window cannot approve a pause it cannot see. Scott asked whether his "do not redesign the spec" rule was
prudent; it was, and it did its job: the route was asked for, not slipped in, and `src/api/CLAUDE.md`
was edited first with the reason before code read it.

**Three named errors in `gate.py`, so the API never matches on message text.** `GateNotFound` (404),
`GateAlreadyDecided` (409), `GateInvalidDecision` (422) subclass `GateError`; the CLI still catches the
base and nothing changes for it. The plan's rule held: the one behaviour the API needed that the CLI
could not express went into `gate.py`, and the CLI got it too.

**`sse-starlette` came out the same hour it went in.** The library keeps a process-global exit event
bound to the first event loop it sees and patches uvicorn's exit handler at import; the second stream
rail, on pytest-asyncio's second loop, had its generator cancelled mid-query. `tick_events` is now
twenty lines on `StreamingResponse` with a ping comment from the poll loop. Cookbook #43.

**`app.py` folded into `routes.py`.** The factory, the middleware and the handlers were first written as
`src/api/app.py`. `docs/plan.md` names `routes.py` and `schemas.py`, and the tree matches the plan, so
the factory moved to the bottom of `routes.py` and the file was deleted before the commit.

### The live run, two processes on this machine, test ledger, 2026-09-11

`SW_OPS_TARGET=test TICK_INTERVAL_SECONDS=20 python main.py --no-spend` in one process (fourteen ticks,
about 22s each, so every one overran the cadence and said so), `SW_OPS_TARGET=test API_PORT=8765
python main.py --api` in another. `curl -N .../ops/stream?limit=2` opened before the first tick finished
and showed the row land from the other process. Every route once, envelopes as served:

```jsonc
// GET /health   (X-Request-ID: journey-health-01 echoed; a request without one got a 32-hex id)
{"data":{"status":"ok","service":"sweetwater-ops","time":"2026-09-11T18:33:52.745Z"}}

// GET /ops/incidents?limit=2   (the test ledger held one row from the suite at that moment)
{"data":[{"id":1,"incident_key":"fx-water-02:water_low","subject_id":"fx-water-02","subject_type":"water-level","location":"South Draw","category":"water_low","severity":"critical","status":"resolved","summary":"South Draw: stock-tank level reads 0.8 gal on fx-water-02, at or below the critical line of 2 gal. ...","last_value":"0.8","unit":" gal","threshold":2.0,"occurrences":2,"first_seen_at":"2026-09-10T14:30:00Z","last_seen_at":"2026-09-10T14:35:00Z","resolved_at":"2026-09-10T14:50:00Z","tick_opened":3,"tick_last_seen":5,"run_id":"e2e","owner":null}],"meta":{"count":1,"limit":2,"offset":0}}

// GET /ops/incidents?limit=0   -> 422, and ?status=open -> 422
{"error":{"code":"VALIDATION_ERROR","message":"limit: Input should be greater than or equal to 1","details":{"field":"limit","reason":"Input should be greater than or equal to 1"}}}
{"error":{"code":"VALIDATION_ERROR","message":"status: Input should be 'pending', 'opened', 'ongoing', 'resolved' or 'dismissed'","details":{"field":"status","reason":"..."}}}

// GET /ops/report   (before the first tick finished -> 404; after tick 11, the code-assembled page)
{"error":{"code":"REPORT_NOT_FOUND","message":"no shift report has been written to this ledger yet; one lands at the end of every tick","details":{}}}
{"data":{"id":11,"run_id":"4d7cb8db15cb","tick":11,"at":"2026-09-11T18:37:54.075971Z","source":"code","headline":"No work orders this tick, so nothing here reports on the shift","situation":"Nothing reached a sub-agent this tick. ...","priorities":[],"linked":[],"escalations":[],"worlds":[],"incident_keys":[],"work_orders":0,"violations":[],"provider":"","model":"","finish_reason":"","latency_ms":0,"input_tokens":0,"output_tokens":0}}

// GET /ops/stream?limit=1   (the latest row on connect, from the loop process; `fields` is the whole tick line)
event: tick
id: 14
data: {"id":14,"run_id":"4d7cb8db15cb","tick":11,"at":"2026-09-11T18:37:54.075971Z","store":"test","duration_ms":22578,"cost_usd":0.0,"error":null,"failed_stage":null,"fields":{"held":0,"tier":null,"opened":0,"ongoing":10,"pending":16,"critical":9,"findings":26,"resolved":4,"sensors_read":160,"herd_animals":5,"shift_report":"code","ledger":{"ongoing":10,"pending":16,"resolved":21,"dismissed":108}, ...}}

// GET /ops/gate   (a pause planted by a third process with M6's method)
{"data":[{"audit_id":"c5d602affb3c4fd586f2546f24ef9e56","incident_key":"cow-0903:deceased","agent":"herd_health","tool":"create_observation","args":{"animal_id":"cow-0903","observation_type":"mortality","severity":"high","notes":"M8 live gate verification, planted, will be rejected"},"proposed_at":"2026-09-11T18:38:07.378Z","tick":0,"run_id":"m8-plant","age_s":19}],"meta":{"count":1,"limit":500,"offset":0}}

// POST /ops/gate without a token -> 401 with WWW-Authenticate: Bearer; with the token on an unknown id -> 404; a body that is not JSON -> 400
{"error":{"code":"UNAUTHORIZED","message":"POST /ops/gate needs a bearer token from OPS_API_TOKEN","details":{}}}
{"error":{"code":"GATE_NOT_FOUND","message":"no proposal with audit_id nope","details":{"audit_id":"nope"}}}
{"error":{"code":"MALFORMED_BODY","message":"the request body is not valid JSON","details":{"field":"body","reason":"JSON decode error"}}}

// POST /ops/gate {"audit_id":"c5d6...","decision":"reject","reason":"planted for the M8 live check; nothing to record on the herd"}   -> 200, decided_by from the token
{"data":{"audit_id":"c5d602affb3c4fd586f2546f24ef9e56","incident_key":"cow-0903:deceased","agent":"herd_health","tool":"create_observation","args":{...},"decision":"reject","decided_by":"scooter","reason":"planted for the M8 live check; nothing to record on the herd","decided_at":"2026-09-11T18:38:26.609Z","result":"not_executed","upstream":"","latency_to_decision_ms":19231}}

// the same id again, approve -> 409
{"error":{"code":"GATE_ALREADY_DECIDED","message":"c5d602affb3c4fd586f2546f24ef9e56 was already decided: reject by scooter at 2026-09-11T18:38:26.609Z (not_executed)","details":{"audit_id":"c5d602affb3c4fd586f2546f24ef9e56"}}}

// GET /ops/nope -> 404 in the same shape
{"error":{"code":"NOT_FOUND","message":"Not Found","details":{}}}
```

After the reject: `logs/audit.jsonl` held the `proposed` line from the planting process and the `decided`
line from the API process under one id, `sw_ops.audit_receipts` held the same two rows (`run_id`
`m8-plant`, `decided_by` `scooter`, `result` `not_executed`), `python -m src.agent.gate list` said
`0 writes waiting for a human (test)`, and the ledger held 16 tick rows across two run ids and 13
report rows. No `tick_row_failed` on the loop's console. Approve over HTTP was not fired live: it
performs a real write on the ranch, and the rail proves it with an injected performer.

### What diverged from the plan

| Planned | Happened | Why |
| --- | --- | --- |
| five routes | six: `GET /ops/gate` added, with Scott's yes, spec edited first | the window cannot approve a pause it cannot see |
| `/ops/report` and `/ops/stream` read "the report" and "ticks" | migration `0007`: `ticks`, `shift_reports`, written by the loop beside the log line | a separate process cannot read a log file; the API reads `sw_ops` only |
| #10 fixed for free by the API running in the loop's process | `audit_receipts` with `(audit_id, phase)` as primary key; the file is a projection | the API is its own process, so it was the second writer |
| no auth in the plan | `OPS_API_TOKEN`, bearer, exit 2 without it, `decided_by` from the token | an unauthenticated POST is an approval anyone who can reach the port can make |
| `sse-starlette` for the stream | hand-rolled on `StreamingResponse` | process-global loop-bound state, cookbook #43 |
| `schemas.py`: Finding, Incident, ShiftReport, GateDecision, ChaosEvent | `IncidentOut`, `ShiftReportOut`, `TickOut`, `PendingWriteOut`, `GateDecisionIn` / `Out`, the envelope | projections of the ledger rows, not the tick's own models; no chaos route exists to need `ChaosEvent` |
| `--api` exits 3 until M8 | exit 3 retired, not reused | the last unbuilt mode landed |

### Defects the phase caught in itself

1. **The SSE library's global state** cancelled the generator on the suite's second event loop. Cookbook #43.
2. **A console line about a refused receipt was counted as a receipt** by the audit rail, because it carried `audit_id` and `phase`. Cookbook #44.
3. **The 500 response had no request id**: Starlette's error layer sits above the middleware. Cookbook #45.
4. **A malformed-JSON 400 named the byte offset as the field.** FastAPI puts the offset in `loc`; the envelope now says `body`.
5. **The tick-row rail passed alone and failed in the suite**: every test shares one `run_id`, so an earlier rail's tick 1 collided with this one's on `uq_ticks_run_tick`. The `target` fixture truncates `ticks` and `shift_reports` now.
6. **Source written through a shell heredoc lost its escapes** three times running. Cookbook #46.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| `GateNotFound` / `GateAlreadyDecided` / `GateInvalidDecision` | 404 / 409 / 422 without matching on message text; the CLI inherits them for free |
| `Last-Event-ID` and latest-on-connect on the stream | a window that reconnects should not replay history, and a window that just opened should not be blank for five minutes |
| `?limit=N` on the stream | the only way to curl it or test it through the ASGI app, which buffers a response until it ends |
| `Settings.ops_tokens()` raising, and `_SECRET_KEYS` gaining `ops_api_token` | the refusal has to happen before a port is bound, and the token must never reach a log line |
| `test_no_route_here_imports_a_ranch_client_or_a_model_client` | the rule in `src/api/CLAUDE.md`, as a grep, so it cannot erode quietly |


## M9 - The window, the last M phase

**What was planned.** `docs/plan.md`'s M9 paragraph and Scott's brief: Next.js App Router against
`/ops/*`, light mode, the panel set lifted from `agent-lab-ui` (moved into this repo as `web/` and
committed untouched first, so every later diff reads as the port), the token never reaching the browser,
the gate verified from a browser on a planted pause, a three-command gate of its own, and the doc close
that used to be M10's. Two questions were to be answered in the check-in before code: where the map's
coordinates come from, and where the API is reachable from.

**What actually happened, in order.**

**The two questions, answered before code.** *The map.* The plan said the coordinates are already in
the catalog, and they are; the catalog is `ranch://sensors/map`, which is the ranch, and neither the
read API nor a browser calls the ranch. So the map panel is a placeholder that says exactly that, and
the source it needs, a catalog snapshot the loop writes to `sw_ops` and the API exposes, is written up
with its DDL as `docs/open-issues.md` #21 for a yes, rather than migrated in on the window's coattails.
*Where the API lives.* Vercel cannot reach `127.0.0.1`, and where the loop and API run is #5, a
decision and not this phase's. Built and verified against `next dev` on this machine with `--api` local;
the Vercel deploy is #23, owed and not faked with a tunnel.

**The panel mapping held in four places out of five.** Gauge, feed, summary, and gate map onto the API
as briefed. **Rails did not**: the API carries no work orders and no per-order violations, and there is
no orders route. What the tick line carries is counts (`work_orders_rejected`, `escalations` with
reasons, `shift_report_violations`, `skipped_upstreams`, `herd_error`, `held`), and a count that should be
zero and is not is a rail that fired. So the rails are eight chips read off the latest tick row, plus
the pending count from `GET /ops/gate`, and the API was not bent to fit a panel.

**The first commit was `web/` as it sat.** Twelve files from `agent-lab-ui`, byte for byte, with
`.gitignore` gaining `web/node_modules/`, `web/.next/`, `web/.env*.local`, and `docs/plan.md`'s tree and
Structure row gaining `web/`. Scott offered to rename it `old-web/` so the scaffold could land clean;
declined, because then git sees a delete and a create and the history of `style.css` snaps. The
scaffold (`create-next-app`, TypeScript, ESLint, App Router, no Tailwind, no `src/`) went into a temp
directory and was copied over the top. `style.css` became `app/globals.css` by `git mv`, so its diff is
a diff. Deleted after that commit: `emit.mjs`, `events.ts`, `server.ts`, `readme.txt`, `index.html`,
`app.js`, the old `package.json`, lock, and `tsconfig.json`. The old README's architecture note was
right about what survives a port and right that the canvas gauge is the one thing to replace; Recharts
replaced it. Next 16.3.5, React 19.2.8, Recharts 3.10.1, Node 22.

**The proxy is the design, not a CORS workaround.** `POST /ops/gate` needs the bearer token, and a
token in browser JavaScript is a token in every visitor's dev tools. `web/lib/proxy.ts` forwards every
`/api/ops/*` call server-side with `OPS_API_URL` and `OPS_API_TOKEN` from the window's own environment,
attaches the token only on the POST, forwards `X-Request-ID` or mints one and echoes the API's back,
forwards `Last-Event-ID`, and pipes bodies as streams so the SSE stream is never buffered. An
unreachable API is a 502 in the same `{error}` envelope. `API_CORS_ORIGINS` is moot: same origin.

**Two charts, not a dual axis.** Tokens and dollars are different measures and never share a scale, so
the gauge is input and output tokens per tick on one chart and `cost_usd` per tick on a second beneath
it, one x-axis, one point per tick row from the stream. The two series colours are the old UI's
`--sent` and `--full`, run through a palette validator before use (both pass on the white surface).

**The window's own gate.** `npx tsc --noEmit`, `npm run lint` (`eslint-config-next`), `npm run build`.
Three problems on the first run, all fixed before the first browser load: the template's `LayoutProps`
is a global type Next generates during a build, so `tsc` alone cannot see it (the layout types its own
props now), and the React Compiler lint rule refused two `setState`-shaped calls inside effects (the
first load now rides on the stream's `open` or its first `error`, and a filter change calls the loader
directly). All three commands went into the root `CLAUDE.md` Commands block and were re-run at the close.

### The live run, three processes and a browser, test ledger, 2026-09-11

`SW_OPS_TARGET=test LOG_DIR=logs/m9-close TICK_INTERVAL_SECONDS=20 python main.py --no-spend` and
`SW_OPS_TARGET=test LOG_DIR=logs/m9-close python main.py --api` as two processes, `next dev` on 3000
with `web/.env.local` holding the API URL and one secret, three pauses planted through `propose()` from
a third process. Then, in the browser:

| | audit id | tool | decision | result | to decision |
| --- | --- | --- | --- | --- | --- |
| 1 | `a8012fd5…` | `add_care_note` (not one of the 19) | approve | `transport_McpUnavailableError`: `Tool add_care_note not found` | 489,696 ms |
| 2 | `3839d1a1…` | `restock_feed` (real) | reject, reason typed in the box | `not_executed` | 516,837 ms |
| 1 again | `a8012fd5…` | | approve | **409** `GATE_ALREADY_DECIDED`, rendered as a sentence naming the earlier decision, the code, the status, and the request id | |
| 3 | `ceefaefe…` | `add_care_note` | approve | `transport_McpUnavailableError` | 14,611 ms |

`decided_by` was `scooter` on all three, from the token and never from the body. The pending rows
disappeared from the panel on the refresh the decision triggered, the gate rail flipped from
`1 write waiting on a human` to `no writes waiting` without a reload, and `sw_ops_test.audit_receipts`
held both rows for every id. `logs/m9-close/audit.jsonl` held both halves for the third under one id;
the first two had only their `decided` lines there, because the planting script skipped
`configure_logging` and its `proposed` lines reached only the console. That is cookbook #33 biting the
harness, not the system, and the third plant was the fix. Every route went through the proxy: the
network log shows `/api/ops/stream` open once, then `/api/ops/incidents`, `/api/ops/report`,
`/api/ops/gate` on every tick and the gate every 20s, and no request from the browser to port 8000.

**The approve is real, and the phase almost proved it the wrong way.** Approving the planted `restock_feed` from a
browser tab would have restocked a real feed bin; the first plant used a made-up tool name by luck. The rule is
cookbook #47 and the demo recipe in `docs/state.md`; the misleading `transport_*` label on a missing tool is
`docs/open-issues.md` #22.

**An orphan on the test ledger.** The gauge's x-axis interleaved two runs: this phase's, and run
`4d7cb8db15cb`, a `--no-spend` loop and an `--api` started at 14:33 local, thirty minutes before this
session, at a 20s cadence on `sw_ops_test`. That profile is the M8 close's live check, never killed. It
spent nothing and hit the live ranch every 20s for nobody, and it would have had its ledger dropped by
`pytest`. Killed at Scott's go (he did not recognise it either), 114 ticks in. Written down so the next
session checks for stray `main.py` processes before it plants anything.

### What diverged from the plan

| Planned | Happened | Why |
| --- | --- | --- |
| a ranch map from the catalog, "no new backend work" | a placeholder that says why, and #21 with the DDL for a catalog snapshot | the catalog is the ranch, and neither the API nor a browser calls it |
| rails from violation counts on the work orders | eight chips off the latest tick line plus the pending count from `/ops/gate` | the API carries no work orders; the line carries counts |
| Vercel, "three ticks land without a refresh at the Vercel URL" | `next dev` on this machine, ticks and three decisions landing without a refresh; Vercel is #23 | Vercel cannot reach `127.0.0.1`; where the API lives is #5 |
| the token gauge as one chart | two charts on one x-axis | tokens and dollars never share a scale |
| scaffold into an empty `web/` | scaffold into a temp directory and copy over the untouched commit | `create-next-app` refuses a non-empty directory, and renaming the old tree would snap `style.css`'s history |
| approve from the browser | approve on a tool the ranch does not have; reject on the real one; a confirm on approve | an approve performs the write on the deployed ranch |

### Defects the phase caught in itself

1. **Feed rows keyed on incident key plus run collided.** A key legitimately resolves and reopens
   inside one run, so the ledger holds two rows for it; React warned on 145 of 500. Rows key on the row
   id now.
2. **The gate rail read `writes_pending` off the tick line**, which is null on a tick that proposed
   nothing, and said "no writes waiting" beside two pauses. It reads the pending count from
   `GET /ops/gate` now. Cookbook #48.
3. **`LayoutProps` is not visible to `tsc` without a build.** A gate that only passes after `next build`
   has run is a gate with an order dependency; the layout types its props itself.
4. **Two effects called `setState`-shaped loaders synchronously** and the React Compiler rule refused
   them. The first load rides on the stream's `open` / first `error`; filters call the loader directly.
5. **Source through a shell heredoc failed again, three times**: a batch of five files died on an
   unmatched quote and wrote nothing, a `\n` inside a template literal came out as a real newline, and
   this entry itself would not go through one. Cookbook #46. The Write tool wrote the files.
6. **The planting script skipped `configure_logging`**, so two `proposed` audit lines reached only the
   console. Cookbook #33, on the harness this time.
7. **`add_care_note` came back as an MCP outage.** Not the window's defect and not fixed this phase;
   `docs/open-issues.md` #22.

### Work not asked for, and why each one is here

| Added | Why it was not optional |
| --- | --- |
| a confirm on approve, naming the tool and args | the one click on the page that performs a write on the deployed ranch |
| "answered this session" cards that keep their buttons | the only way a human can produce the 409 the brief asked to see rendered |
| the gate list polling every 20s beside the stream | a decision made at the CLI does not land a tick, and the panel would lie until the next one |
| 502 `API_UNREACHABLE` and 503 `WINDOW_NOT_CONFIGURED` in the API's own envelope | one error shape for the client to render; a dead API is a sentence in every panel, not a blank page |
| a 404 at the proxy for any `/api/ops/*` path the API does not have | the proxy is not a general forwarder; an unknown path is never a probe upstream |
| `docs/open-issues.md` #21 with the full DDL | the map's source is a migration, and a migration is a yes, not a drive-by |
| the cookbook re-sectioned by pain with stable numbers and an index | the M9 brief; stable numbers because every `#N` in this file and `open-issues.md` has to keep meaning what it meant |
| killing two orphaned `main.py` processes | they blocked `pytest` and hit the live ranch every 20s for nobody; Scott's go |

### The M phases, closed

M0 through M9 are landed, in the order `git log` says rather than the order the numbers say (M5 before
M3's close, the debounce between M4 and M6, M7A inserted before M8). Every phase closed with the same
four steps, and this file was written at every boundary, so this final pass is a read for consistency
and not a reconstruction. What a session picks up next is in `docs/state.md`'s Next row: #12 and the
cascade re-measure, the M7A four (#16 to #19), and the decisions waiting on Scott (#5 to #8, #21).
FUTURE-1, dockerizing, stays deferred until #5 lands on a host that wants a container.
