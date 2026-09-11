# JOURNEY

What actually happened, and where it diverged from the plan. Written as we go rather
than reconstructed at the end, because a reconstruction only records the decisions that
worked.

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

The first two are the reason logging lands first rather than last; the third is the
reason `mypy --strict` is in the gate rather than aspirational.

**1. `failed_stage: null` on a connect failure.** `failed_stage` was only assigned once
inside the session context, so a connection failure produced an error line that said a
tick died without saying where, which is the single question the field exists to answer.
Now set **before** each stage is attempted.

**2. `unhandled errors in a TaskGroup (1 sub-exception)`.** The MCP transport runs on
anyio task groups, so every underlying failure surfaced as that string, identical whether
the host refused the connection, DNS failed, or the server returned a 401. A message that
looks like information and carries none is worse than no message. Added
`flatten_exception()`, which unwraps `ExceptionGroup` to the leaves and deduplicates
them - the same failure now reads `ConnectError: All connection attempts failed`.
Deduplication matters because 160 concurrent reads failing the same way otherwise produce
160 identical leaves.

The catch also had to widen from `Exception` to `BaseException` (re-raising
`KeyboardInterrupt` and `SystemExit`): an `ExceptionGroup` from the transport does not
reliably inherit from `Exception`, so the narrower catch let the real cause escape
unreported.

**3. `getattr(c, "text", None)` type-checked by accident.** Both content readers picked
text out of an MCP response with a duck-typed attribute probe. MCP tool content is a union
of five block types (text, image, audio, resource link, embedded resource), and a
`getattr` probe does not narrow a union, so `mypy --strict` flagged four `union-attr`
errors. The runtime consequence was worse than the typing one: if the upstream ever
returned a non-text block, the probe would quietly contribute an empty string and the
caller would parse `""` as "the tool returned nothing" rather than "we could not read
this." Replaced with `isinstance(c, TextContent)` and `isinstance(c, TextResourceContents)`.

Worth noting the shape of this one: the code **worked** against the live server and the
tests passed. Only the type checker knew, which is the argument for having it in the gate.

### A found fact worth writing down

**A Lambda Function URL routes every path to the same handler.** The first attempt to test
the failure path appended a bogus path to `MCP_URL` and the handshake **succeeded** - the
MCP server answers at any path. Failure paths have to be tested against an unreachable
host. Filed here because the invalid test looked exactly like a passing test.

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

`docs/STATE.md` records one temperature sensor reading `-500` with `status: "online"`. The
obvious fix is to special-case `-500`. That is a fix for exactly one sentinel, and the next
one the upstream picks walks straight through it. So `triage.py` carries a **per-type
physical-plausibility window** instead: a value outside what the instrument could physically
report is a sensor fault, not a reading, whatever the number is. Same cost, catches the case
that has not happened yet.

### An M0 defect that only M1 could find

**Foreign stdlib records skipped the structlog processor chain.** M0's logging looked
correct because M0 only ever logged through structlog. M1 was the first phase to pull in
libraries that log on their own, and `alembic` and `httpx` lines came out with no level, no
timestamp, and no `run_id`. The three streams join on `run_id` and `tick`, so a line without
them is not in the stream, it is beside it. Pinned the chain so foreign records go through
the same processors.

Worth the note because M0's gate was genuinely green: the defect needed a second library in
the process before it could exist.

### Reaching into the frozen upstream, and retracting it

Writing the triage thresholds, the fastest way to get 13 types right looked like reading
the deployed Sensor API's own generator in `C:\temp\MCP-Farm`. Did it, then retracted it.

The clone is there and reading it feels like research, but a constant lifted out of another
service's internals is a value **nothing in this repo can verify**, and it fails silently
when the other side retunes it: the code keeps running and starts being wrong. The
thresholds are derived from the ranch mission in `docs/sweetwater-ranch.md` and the observed
distributions in `docs/STATE.md`, both of which live here and can be re-measured over the
wire. Full entry as cookbook #5, and the rule is now sharpened in root `CLAUDE.md` and
`docs/STATE.md`: **the contract is the tools and the REST surface, not that repo's source.**

### The defect this phase caught in itself: the tree stopped matching the plan

Found at the boundary, when Scott asked whether `docs/Plan.md` still described the repo. It
did not, in four ways, none of which any test could see:

| Plan says | M1 shipped |
| --- | --- |
| `src/agent/executor.py` holds the loop | `src/agent/tick.py` |
| `src/agent/agent.py` holds the supervisor **and** routing | routing in its own `src/agent/routing.py` |
| three test modules | seven |
| eleven named leaves exist | absent, including the whole of `src/prompts/` |

The `routing.py` one was the live grenade. `docs/Plan.md` also names
`src/models/routing.py`, a completely different question (which model **tier** runs a job,
not which **agent** owns a finding), which lands at M7. Two modules called `routing.py`
doing unrelated work is a mis-import that type-checks. Folding the table into `agent.py`
costs one rename now and would have cost an afternoon at M7.

The absent leaves were the subtler half. An empty `src/prompts/` directory is
indistinguishable from a forgotten one, and it read to Scott as a hole in the build rather
than as M2 not having happened yet. Fixed both ways: eleven docstring-only placeholders,
each naming the milestone that fills it and carrying the decision already made about it, and
a **"lands at" marker column** on every line of the plan's tree, so unbuilt reads as unbuilt.
No prompt text was drafted, because a brief written against an imagined evidence packet
reads fine and grounds nothing.

Renames done with `git mv` so the history survives, and the seven-into-two test merge
verified by diffing the sorted set of collected test function names before and after: **89
before, 89 after, identical set.** A merge that silently drops a rail is the one outcome
that would have made the whole exercise negative.

### And the process defect underneath it

**M1's first commit did step 1 of the phase-close ritual and skipped step 2 entirely.** No
`STATE.md`, no `JOURNEY.md`, no `cookbook.md`. So for one commit the session-start briefing
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

`ANTHROPIC_API_KEY` was empty and `docs/STATE.md` called it a hard M2 blocker. It was not
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

`docs/Plan.md` had `agent.py` carrying the routing table **and** the worker factories. M2 split
the second half into `workers.py`, because the two answer different questions: `agent.py` says
which agent owns a category, `workers.py` says what an agent's answer must satisfy. Folding the
rails, the checker, and the fan-out into a 113-line routing table reproduces the exact confusion
the M1 `routing.py` rename existed to remove.

The plan's tree is amended with the reason written into it, which is the M1 lesson applied
rather than repeated: the defect there was **silent** drift, and no test reads a diagram.

### Five defects M2 caught in itself

Full entries as cookbook #10 through #15. The first three are all consequences of printing
the packet first.

**1. A name join across two services returned an empty set, not an error.** The first packet
had **zero** siblings and no pasture. `ranch://sensors/map` spells a location
`"Alkali Flat (alkali-flat)"` where REST says `"Alkali Flat"`, and the Farm API's pasture for
sensor location `"East Allotment"` is `"East BLM Allotment"`. Now slugified to the id and
matched on that. The dangerous part is that "no siblings here" and "no animals in this
pasture" are plausible facts about a ranch, so the packet read as complete and merely thin.

**2. Two honest numbers on one page with no note about precedence.** Triage judged 3.4 gal
while the newest history point said 0.8 gal at a *later* timestamp. Both are honest -
`/sensors/:id` and `/sensors/:id/readings` are synthesized independently - but a model handed
two contradictory numbers picks one and sounds equally confident either way. `render()` now
says in one line that the series is shape and trend only.

**3. The token estimate was off 4x.** Sized `MAX_OUTPUT_TOKENS = 1536` against a guess of
~1,400 in / ~450 out. Measured **5,555 in / 1,137 out**. The SOP file, loaded whole, is the
majority of the input and had been filed mentally as "just a few rules." Raised to 2,048 with
the measured numbers in the comment. The finding underneath it: **the cost lever is the SOP,
not the evidence**, which matters before M7 optimizes the wrong half.

**4. The grader's own failure case caught the grader.** `ungrounded_numbers()` asserted that
`300` and `40` in an invented sentence were both ungrounded; it found only `300`. `"40"` is a
substring of the timestamp `13:40:00` in the rendered packet, so a substring test grounded an
invented head count against an unrelated minute field. Now strips ISO timestamps and compares
number **tokens** on both sides, which made the grader strictly harder to pass. It only
surfaced because the grader had a red case at all.

**5. Ten test rails were about to start billing.** `run_tick` grew two spend stages and ten
existing rails called it. Fixed twice over: `spend=False` at every call site, and an autouse
`conftest.no_model_calls` fixture that raises if anything constructs a real client. The flag
is the intent, the fixture is what happens when somebody forgets it. Same shape as
`assert_local_test_url` keeping the suite off Supabase.

### The doc defect, and this time step 2 is what found it

M0 concluded "re-run every command the docs claim works." M2 is the first phase where one of
those commands had real data to run against, and it failed immediately:

> `jq -r 'select(.finish_reason!="stop")' logs/agent.jsonl` **# should be empty**

It returned **all 19 lines, every one healthy.** Anthropic's stop reasons are `tool_use`,
`end_turn`, `stop_sequence`, `max_tokens`; `stop` is the OpenAI and Ollama spelling and no
Anthropic call has ever produced it. So the diagnostic written to find config bugs reported a
100% config-bug rate on a phase that worked perfectly. It shipped in **four** files -
`src/models/CLAUDE.md`, `README.md`, `docs/logging.md`, `docs/Plan.md` - and survived two
milestones, because no test reads a `jq` line out of a markdown file.

**And the first fix was broken too.** Written as
`select(["stop",…]|index(.finish_reason)|not)`, which is wrong in a way that reads perfectly:
inside the pipe `.` is the array, so it tries to index an array with a string and every line
errors. Thirty errors on stderr, nothing on stdout, and `wc -l` reports `0` - identical to a
healthy log. Caught only by adding a **negative control** (`IN("stop")` alone must list all 30
Anthropic lines) and running it. A diagnostic that fails by producing no output cannot be
verified by running it once; see cookbook #10 and #11, which arrive at the same rule from
opposite directions.

Fixed by naming the healthy **set** rather than one healthy value, in all four files, with
`ModelResponse.ok` and `.truncated` holding the same sets in code as the definition. The same
audit found `docs/logging.md` and `docs/Plan.md` printing `tokens_in` / `tokens_out` where the
code writes `input_tokens` / `output_tokens`, which is a query that returns `null` and looks
like a calm ranch, plus `README.md` and root `CLAUDE.md` still calling `--once` a stub. Both
`tick.jsonl` examples were also two milestones out of date on their field names.

The general lesson is M0's fourth defect one more layer out: **a doc defect and a code defect
have the same cause, and only the code one gets caught by a test.** The habit works. It just
cannot fire until the command has something to say.

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

Asked for and delivered before the fan-out was wired, then re-measured live at the boundary. Two
`--once` runs against a ledger that had just been refilled:

| | tick A | tick B |
| --- | --- | --- |
| newly-opened, so calls | 15 across 3 worlds | 14 across 3 worlds |
| tick total tokens | 104,500 in / 15,308 out | 91,468 in / 15,545 out |
| wall clock | 85 s | 96 s |
| calls that failed a rail | 0 of 16 | 0 of 15 |

At an assumed $15/M in and $75/M out that is **$2.72 and $2.54**. The rate is an assumption stated
in one place, because the pricing table and `cost_usd` on the tick line are M7's and inventing them
early would have put a number in the ledger nothing measured. **A calm tick is $0.00 exactly**, not
approximately: zero newly-opened incidents is zero responder calls, and one world synthesizes in
code. And the supervisor is about 13% of the bill for one call against fourteen, which makes it the
cheapest thing in the tick per unit of value, because it is the only call that reads across worlds.

Neither tick is a steady-state budget and `docs/model-routing.md` says so with the arithmetic
attached: 288 ticks a day at tick B is roughly $730, and that figure is fiction. M4 runs unattended
for 30 minutes and measures the real thing.

### The experiment disagreed with its own hypothesis, which is why it was worth running

The one thing to prove rather than port: run a sub-agent with no brief, capture it flailing, pass
the brief, capture it working. `docs/no-brief-transcript.md` and `docs/with-brief-transcript.md` are
that pair - same model, same evidence packet byte for byte, one variable, whether
`COMPLIANCE_MANDATE` was in the brief.

**It did not flail.** The unbriefed answer echoed severity correctly, named its sensor, quoted only
numbers that were on the page, cited three real rule ids, escalated for a real reason, and **passed
every rail with zero violations.** It would have shipped.

What it did instead was invisible to code. One action instead of five, and that action was filing a
note. The two stock tanks it correctly identified as somebody else's got "belong to a separate water
work order" with no name attached. The gauge it could not believe went into `unknowns`, which is
where facts go to be nobody's problem, rather than to the agent that repairs instruments. Its
headline promised a GM escalation its actions list never contained.

So the finding is sharper than the one the experiment was set up to catch, and it is now the honest
limit of the whole rail suite: **every rail asks whether an answer is defensible about its own
incident, and scope is not answerable from inside one work order.** A slice whose brief silently
regresses to nothing keeps a green suite. Both answers are pinned in `test_agent.py` and the tests
over them assert the *sameness* of the verdict rather than a quality gap. Nothing here became a
blocking rail: a rail that counts actions is a rail that gets satisfied by padding.

**Worth its own sentence.** The whole of `compliance.md` was on the page in both runs, including the
two rules that name the owner of a bad instrument and of a stock tank in as many words. The SOP did
not rescue it. Standing orders describe the domain; the brief says which part of it is yours.

### Four defects M3 caught in itself

**1. `SHIFT_REPORT_MAX_TOKENS = 1_024`, reasoned about instead of measured.** 15 work orders across
three worlds is a 32.5k-char page, and the first live tick spent the whole budget on the situation
paragraph and the first few priorities before `max_tokens` cut the tool call mid-object. This is the
M2 lesson word for word: **print the page before sizing the budget.** Now 3,072, sized off a
measured 1,646-token complete answer, with the schema's own caps reasoned about in the comment so
the next person does not size it a third time.

**2. The truncation mislabelled itself, and the label was the worst one available.** A tool call cut
off at `max_tokens` still arrives carrying a partially filled `input` dict. `synthesize` checked
`payload is None`, which was False, so the half-answer went through the rails, where empty
priorities read as an all-clear. The log said the supervisor wrote an all-clear about a ranch with
ten criticals on it. `ModelResponse.ok` already knew better; two call sites were not asking. Both
`synthesize` and `to_work_order` now treat truncated as no answer, and both have a rail. The general
version, in `docs/cookbook.md`: **a fallback that fires correctly while attributing the failure to
the wrong component is worse than a crash, because it is quiet and it accuses.**

**3. The grounding grader was asymmetric.** M2 fixed it in one direction (substring containment:
`"40"` graded as grounded against `13:40:00`) by stripping timestamps from the page. `compliance` is
briefed to write for an auditor eight months out, so it quotes the date it was handed, and
`2026-09-10T20:08:41Z` in an assessment then graded as five invented numbers. Times and dates now
come off **both** sides. The accepted cost is written down rather than discovered later: a fabricated
timestamp goes ungraded, and nothing in the prose is anchored to a time anyway. **A normalization
applied to one side of a comparison is a bug waiting for the other side to start using the thing you
normalized away.**

**4. `AGENT_CONCURRENCY = 4` would have meant sixteen.** Caught in design rather than in production.
The constant had one meaning when there was one agent; four agents each calling `gather_bounded` at
four is a ceiling of sixteen that appears nowhere in the code. `fan_out` builds **one** semaphore and
hands the same object down, and the rail measures peak in-flight calls across a 16-packet fan-out
rather than asserting the constant equals 4, which would have passed on the broken version.

### Divergence from the plan

**`agent_prompts.py` is a new module, and `MANDATES` moved out of `system_prompts.py`.** M2 put the
one brief it needed beside the schemas. Four briefs plus a supervisor's is a different thing from a
schema, and `system_prompts.py` keeps what a machine consumes while `agent_prompts.py` keeps what a
model reads. `docs/STATE.md` said the mandates lived in `system_prompts.py`; that line is now
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
events. That is `docs/STATE.md` decision 5 working, so it is not warned about: a line per tick per
idle agent trains everyone to ignore the log.

### The gate

**281 tests**, `ruff check .` clean, `mypy` clean on 29 source files, one alembic head. Live
verification, in five parts:

| Check | Result |
| --- | --- |
| `--handshake` | 19 tools, 160 sensors, 32 locations, 13 types, and the deployed list matches `DEPLOYED_TOOLS` exactly |
| `--once`, tick A | 15 packets across 3 worlds, 16/16 shipped, and the truncated supervisor found |
| `--once`, tick B after both fixes | 14 packets, 15/15 shipped, `shift_report=model`, `shift_report_violations=[]` |
| the cross-domain fusion the phase was supposed to prove | 3 worlds in one tick, both runs, so the storm front arrived for free exactly as `docs/STATE.md` predicted and the supervisor fused it rather than concatenating two unrelated pages |
| bare `python main.py` and `--api` | exit 3, naming M4 and M8 |

The chaos CLI's read-only commands (`status`, `plan --ticks 3`) were re-run too, since they are
in `docs/STATE.md` and this repo's rule is that a documented command is a command that gets run.

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

**1. The `PATCH` was pointed at the wrong service, and every rail passed anyway.**
`/animals` is on the **Farm API**; the observation is on the **Care API**. Both calls were
written against `CARE_API`. `src/tools/CLAUDE.md` has warned about exactly this base-URL
trap since M2 and it was walked into regardless. What makes it worth an entry is why it was
invisible: **a `respx` mock answers whatever host it is pointed at**, so a suite that mocks
one base URL cannot tell a wrong base URL from a right one. The fix is not just the split
across two clients, it is that the rail now mounts `PATCH` on one host and `POST` on
another, so collapsing them back fails loudly. Caught by a live probe, not by the suite.

**2. The observation body was wrong in three ways at once.** It carried
`{type, notes, observedBy}`. The real shape is `{type, severity, note, observedAt}`, all
four required: `notes` is a 422, `observedBy` is accepted and silently dropped, and both
`severity` and `observedAt` were simply missing. `restore`'s default status was `healthy`,
which is not in the status enum at all (it is `active`). So the reset path, the one meant to
clean up after a demo, could not have worked. All three enums are now validated in
`parse_catalog` at load, so a typo is a load-time `ChaosCatalogError` rather than a 422
nobody is watching for mid-demo.

**3. A rail that asserted nothing.** The first store test contained
`live = await active_overlay(...) if False else events`, which is a test that compares a
value to itself. It passed, it was counted in the total, and it proved nothing. Replaced
with a real read back through `ChaosEvent.from_row`.

**4. Three rails failing for a reason that was the rail's fault, not the code's.** The
`sentinel`, `gate_open`, and `drift` cases passed a custom `sensor_id` to the reading helper
while leaving the event's `target_id` at its default, so the overlay correctly declined to
match and the assertion correctly failed. Worth a line because the first instinct was to
suspect the matching logic, and the matching logic was right.

**5. A documented `jq` query that was wrong in three ways, caught by step 2 running it.**
`docs/logging.md` gained a chaos section, and its example query named a file that does not
exist (`logs/app.jsonl`; there are three JSONL streams and chaos is on none of them), keyed
off `event` when a processor renames that field to `msg` before any renderer sees it, and
assumed the stream was pure JSON when a CLI prints human summaries and tracebacks to the
same place. It failed on all three counts on the first run. The same family as M2's `jq`
defect and M0's documented-command defect: **a doc defect and a code defect have the same
cause, and only the code one gets caught by a test.** Step 2 keeps earning its place.

### An unauthorized write that cannot be undone

The animal contract was learned by **sending deliberately invalid bodies to a nonexistent
animal id and reading the 422s**. That technique names the enum, mutates nothing real, and
does not require reading the frozen upstream's source, which is why it is now in
`docs/cookbook.md` and `src/tools/CLAUDE.md`.

The fifth probe body was valid. It returned **201** and created observation
`0328d7e2-d271-4410-907c-a84020c2c8c7` against the ghost id `zz-does-not-exist-0000`. The
ghost id is why nothing real was touched, but it was still a write to a live deployed
service that had not been authorized, and it was disclosed before it was asked about.

**It cannot be cleaned up from this side.** Observations are append-only upstream: only
`GET /animals/:id/observations` and `POST /animals/:id/observations` exist.
`DELETE /observations/{id}`, `DELETE /animals/{id}/observations/{id}`, `GET` on either
single-observation path, and `OPTIONS` on both all return 404. The row is reachable only by
listing observations for an animal id that does not exist, so it is invisible to every real
animal and to every query the agents make. Removing it would be a scoped change in the
frozen upstream repo, which is a conversation and not a drive-by. **It stays, and it is
written down here so it is a known artifact rather than a mystery row.**

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

Between the CLI verification and the cleanup step, `sw_ops_test` lost `chaos_events` and its
stamp fell from `0002` back to `0001`. Nothing in this worktree did it. **The two sessions
share one local Postgres and one `sw_ops_test` schema, and `conftest.py` drops that schema
`CASCADE` and re-migrates from its own worktree's `alembic/` directory.** The other
session's `pytest` run therefore rebuilt the schema without migration `0002` in it, pulling
the table out from under this one. Each suite repairs the schema for itself on the next run,
so neither is broken, but a `pytest` in one worktree **during** a `pytest` in the other is a
cross-session flake with no local cause. It cost twenty minutes of looking for a bug that
was not there. Now in `docs/cookbook.md`.

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
`docs/STATE.md`'s owed row rather than fixed here. Fixing another session's file quietly is
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
`SPEND_CEILING_USD=3.00` so the halt would be exercised for real if the ranch was busy. It was.

| | tick 1 | tick 2 |
| --- | --- | --- |
| newly-opened, so calls | 12 across 3 worlds | 14 across 2 worlds |
| responder tokens in / out | 72,692 / 10,523 | 82,086 / 13,060 |
| shift report | `model`, fused | `model`, fused |
| `cost_usd` | **2.16** | **2.54** |
| wall clock | 80s | 90s |
| rail failures | 0 of 13 | 0 of 15 |

**26 work orders, 26 shipped, 0 rejected, 0 no-answer, 0 held.** After tick 2 the run stood at
**$4.70 against a $3.00 ceiling**, `loop_halted` was written with `reason=spend_ceiling`, and the
process **exited 4** on its own before a third tick started. The overshoot is the one tick the
design allows. Full numbers and the steady-state finding: `docs/model-routing.md`.

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

**1. A dead Sensor API was a green tick.** `read_sensor` turns every transport failure into a
per-sensor error, correctly, so 160 of them produced a sweep with zero readings and no exception.
Backoff never fires on a stage that did not fail. Found by predicting what the live kill would
show before running it, and the prediction was "nothing." `run_tick` now raises when the sweep
has errors and no readings. `docs/cookbook.md` #25.

**2. The test suite was reading Supabase.** `.env` on this machine has `CHAOS_ENABLED=1`, and
`sweep()` reads the overlay through `resolve_store()`, whose default is prod. Every tick test had
been reading `sw_ops.chaos_events` on the hosted database, and from M4 the tick would have
injected into `sw_ops_test` on every test. An autouse `chaos_off` fixture disarms both call
sites. `docs/cookbook.md` #26. `docs/STATE.md` said the flag "belongs at 0"; it was at 1.

**3. The brief's premise about `cost_usd`** (above).

**4. Loop-level lines said `tick=0`.** `bind_tick` runs inside the tick, the tick runs as its
own task, and a task copies its context, so the loop's `upstream_backoff` line about tick 11
carried `tick: 0`. Seen on the first live run. The loop binds the tick before creating the task.

**5. Two fixtures in the loop rails lied about the loop.** The fake sleep did not advance the fake
clock, so start-to-start cadence read as drift; and a fake skipped tick did not report its skip,
so `observe` read it as a recovery. Both were the fixture, not the loop, and both are exactly the
two facts the real tick reports and the rail exists to check.

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
