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
