# Cookbook

The patterns, ordered by **the pain that produced them** rather than by technique. Filled
in as milestones land; a cookbook written in advance is a table of contents.

Each entry answers three questions: what went wrong, what it looked like while it was
going wrong, and what the fix actually was.

---

## 1. An error message that looks like information and carries none

**Pain.** Every MCP transport failure reported `unhandled errors in a TaskGroup (1
sub-exception)`. Identical string whether the host refused the connection, DNS failed, or
the server returned a 401.

**Why.** anyio task groups wrap everything underneath in an `ExceptionGroup`, and
`str(exception_group)` describes the *group*, not the cause.

**Fix.** `flatten_exception()` in `src/tools/mcp_client.py` walks the group to its leaves,
formats each as `TypeName: message`, and deduplicates. `ConnectError: All connection
attempts failed` instead. Deduplication is load-bearing at 160 concurrent reads, where an
un-deduplicated flatten produces 160 identical leaves.

Also: catch `BaseException`, not `Exception`. An `ExceptionGroup` does not reliably inherit
from `Exception`, so the narrower catch lets the real cause escape past the handler that
was written to report it. Re-raise `KeyboardInterrupt` and `SystemExit`.

**Found:** M0.

---

## 2. A null field is not the same as no failure

**Pain.** A failed handshake logged `failed_stage: null` beside a real error, saying a tick
died without saying where.

**Why.** `failed_stage` was assigned inside the block it was meant to describe, so any
failure *before* the first assignment left it at its initial value.

**Fix.** Set the stage marker **before** attempting the stage, never after. The invariant
is that the variable always names the thing currently being attempted.

**Found:** M0.

---

## 3. A test that passes because it never tested anything

**Pain.** Verifying the failure path by pointing `MCP_URL` at a bogus path on the real
host. The handshake **succeeded**.

**Why.** A Lambda Function URL routes every path to the same handler. There is no 404 to
provoke.

**Fix.** Test failure paths against an unreachable host (`http://127.0.0.1:59999`), not a
wrong path on a reachable one. Worth writing down because the invalid test looked exactly
like a passing test, which is the most expensive shape a mistake can take.

**Found:** M0.

---

## 4. A duck-typed probe that reads a union hides a real bug

**Pain.** Nothing, yet. The code worked against the live server and every test passed.
`mypy --strict` was the only thing that objected.

**Why.** MCP tool content is a union of five block types (text, image, audio, resource
link, embedded resource). `getattr(c, "text", None)` extracts text from the one that has
it and silently yields `None` for the other four, so a non-text block would contribute an
empty string. The caller then parses `""` and concludes "the tool returned nothing" rather
than "we could not read this," which are very different facts.

**Fix.** `isinstance(c, TextContent)`. Narrow the union properly instead of probing it.

**Lesson.** A `getattr` probe against a union type-checks by accident and reads as
defensive. It is the opposite: it converts an unhandled case into a plausible-looking
empty value. This is the same disease as the upstream fault that returns HTTP 200 with a
perfect empty envelope, one layer down.

**Found:** M0, by the type checker, which is why it is in the gate rather than aspirational.

---

## 5. A constant copied out of another service is a value you cannot verify

**Pain.** Writing thresholds for 13 sensor types, the fastest route looked like reading the
deployed Sensor API's own reading generator. The clone sits at `C:\temp\MCP-Farm` and reading
it feels like diligence.

**Why it is wrong even though the numbers are right.** The upstream is frozen and owned
elsewhere. A constant lifted out of its internals is a value **nothing in this repo can
verify**, and it does not fail loudly when the other side retunes it: the code keeps running
and quietly starts being wrong. Worse, it converts a contract into a coupling. The contract
is the 19 tools, the `ranch://sensors/map` resource, and the four REST surfaces, all of which
can be re-measured over the wire at any time.

**Fix.** Derive from what lives here and can be re-measured: the ranch mission in
`docs/sweetwater-ranch.md` and the observed distributions in `docs/STATE.md`. If a fact is
genuinely only knowable from the other side's source, that is a scoped conversation about
the other repo, not a drive-by copy.

**Lesson.** The test is not "is this number correct," it is **"can anything on this side
detect it going stale."** A value that fails that test is a landmine with a comment on it.

**Found:** M1, by doing it and retracting it.

---

## 6. A special case for a sentinel fixes exactly one sentinel

**Pain.** One temperature probe returns **`-500`** with `status: "online"`. The obvious fix
is `if value == -500: fault`.

**Why that is a trap.** `-500` is not a documented contract, it is one observed value. The
next sentinel the upstream picks, `-999`, `NaN`, `0`, walks straight through the check, and
it gets triaged as a reading. A temperature of `-500` triaged as cold is a **critical alert
for weather that is not happening**, which trains a human to ignore the alerts that are real.

**Fix.** A per-type **physical-plausibility window** in `src/tools/triage.py`. A value
outside what the instrument could physically report is a sensor fault, whatever the number
is. Same amount of code, catches the case that has not happened yet.

**Lesson.** When you find yourself hardcoding an observed magic value, ask what class it
belongs to and check for the class. Sentinels travel in families.

**Found:** M1.

---

## 7. A log line that skips the processor chain is not in the stream, it is beside it

**Pain.** M1 pulled in `alembic` and `httpx`, both of which log on their own. Their lines
came out with no level, no timestamp, and no `run_id`.

**Why.** structlog sits over the stdlib `logging` module here specifically so foreign chatter
lands in the same stream. But records created by a foreign logger do not pass through the
structlog processor chain unless the chain is explicitly pinned onto the stdlib handler.
M0's logging looked correct because M0 logged through nothing else.

**Fix.** Pin the processor chain so foreign records go through the same processors as native
ones.

**Lesson.** The three streams join on `run_id` and `tick`. A line missing them is not a
badly formatted member of the stream, it is **not a member of the stream**, and it will be
invisible to every `jq` query written against it. Also: a logging defect cannot be found by
the phase that writes the logger. It needs a second library in the process, which is an
argument for treating the instrument as unproven until something foreign has logged through
it.

**Found:** M0, by M1.

---

## 8. "No reading" and "no finding" are different facts

**Pain.** None, because it was caught while writing `reconcile`. The shape is worth the entry
anyway, because the failure mode is the worst one this system has.

**Why.** Reconciliation resolves an incident when its sensor stops reporting a fault. The
naive implementation resolves anything absent from this tick's findings. But a sensor that
**did not answer** is also absent from the findings. So one upstream outage, one expired
token, one Lambda cold-start storm, closes every open incident at once and the next report
reads **all clear on a ranch that is on fire.**

**Fix.** `reconcile` takes the set of sensor IDs that actually replied, and resolves only
within it. Everything else is held, and the count of held incidents is logged. Related: an
empty catalog is a **failed** tick, not a calm one, and it reconciles nothing at all.

**Lesson.** Absence of evidence arrives through the same door as evidence of absence, and in
a monitoring system the two have opposite meanings. Every place a set difference decides
something, ask what a partial read does to it.

**Found:** M1, in design.

---

## 9. A diagram is a claim, and no test reads a diagram

**Pain.** `docs/Plan.md` says the loop lives in `executor.py` and routing lives in
`agent.py`. M1 shipped `tick.py` and a separate `routing.py`, plus seven test modules where
the plan names three, and eleven named files that simply did not exist. Every gate was green.

**Why.** Tests read code. `mypy` reads code. `ruff` reads code. The plan's tree is prose, so
nothing in the gate can disagree with it, and drift accumulates at exactly the rate you stop
looking. The `routing.py` case was live: `docs/Plan.md` also names `src/models/routing.py`
for a different question entirely, which model **tier** runs a job rather than which **agent**
owns a finding. Two modules with one name doing unrelated work is a mis-import that
type-checks.

**Fix, two parts, because the drift had two causes.** For the wrong names, conform the code
with `git mv` and verify the test merge by diffing the sorted set of collected test function
names before and after (89 and 89, identical). For the missing files, **docstring-only
placeholders** that name the milestone which fills them, plus a **"lands at" marker column**
on every line of the plan's tree.

**Lesson.** An empty directory is indistinguishable from a forgotten one. `src/prompts/`
being empty read as a hole in the build rather than as M2 not having happened yet, and that
ambiguity is what surfaced the whole problem. A placeholder that names its milestone is
cheap and converts "missing" into "not yet," which is a different fact. And more generally,
this is cookbook #4's shape again at the document layer: **the thing no automated check can
read is the thing that has to be put where a human trips over it.**

**Found:** M1, at the boundary, by being asked whether the plan still described the repo.

---

## 10. A field whose vocabulary is per provider, and a query that hardcoded one word

**Pain.** `src/models/CLAUDE.md`, `README.md`, and `docs/logging.md` all shipped
`jq -r 'select(.finish_reason!="stop")' logs/agent.jsonl` with the comment "should be empty.
Anything in it is a config bug." Run against M2's first real `agent.jsonl`, it returned
**every single line**, all 19 of them, all healthy.

**Why.** Anthropic's stop reasons are `tool_use`, `end_turn`, `stop_sequence`, `max_tokens`.
It never emits `stop`; that is the OpenAI and Ollama spelling. So the query that exists to
find config bugs reported a 100% config-bug rate on a phase that worked perfectly, which is
the exact loss of signal it was written to prevent. Worse, a diagnostic that cries wolf
about everything gets ignored, and then it is not there when one line really is `max_tokens`.

**Fix.** Name the healthy **set**, not one healthy value:
`select(.finish_reason | IN("stop","end_turn","tool_use","stop_sequence") | not)`. Both
vocabularies pass, and both truncation spellings still fail. `ModelResponse.ok` and
`.truncated` hold the same sets in code, and they are the definition. Fixed in all four files
that carried it: `src/models/CLAUDE.md`, `README.md`, `docs/logging.md`, `docs/Plan.md`.

**The first attempt at that fix was also broken, and running it is the only reason we know.**
It was written `select(["stop",…]|index(.finish_reason)|not)`, which is wrong in a way that
reads perfectly: inside the pipe `.` is now the *array*, so `.finish_reason` tries to index an
array with a string and every line errors. That puts thirty errors on stderr and **nothing on
stdout**, so piped to `wc -l` it reports `0` - which is precisely what a healthy log looks
like. A broken query and a passing query produce the same output. So the check needs a
**negative control**: `IN("stop")` on its own must list all 30 Anthropic lines. It does.

**Lesson.** The moment a field's values come from an external vocabulary, a check written
against one literal is a check written against one provider. And this is M0's fourth defect
again: **the gate was green while the file describing how to read the gate was wrong**, for
two whole milestones, because no test reads a `jq` line out of a markdown file. Running every
documented command at the boundary is what caught it, on the first boundary where the command
had real data to run against.

And the sharper half: **a diagnostic that fails by producing no output cannot be verified by
running it once.** It has to be run against data that is known to trip it. That is the same
demand cookbook #11 makes of the grounding grader, arriving here from a completely different
direction, which is usually the sign it is a rule rather than a coincidence.

**Found:** M0 wrote it, M2 was the first phase that could disprove it.

---

## 11. Substring containment is not grounding

**Pain.** The grading rail that catches invented numbers in a work order. Its own failure
case asserted that `"300"` and `"40"` in "roughly 300 gallons short and 40 head short by
dark" are ungrounded. It reported only `{"300"}`.

**Why.** The first implementation asked `if n not in packet.render()`, a substring test.
`"40"` appears in the rendered page - inside the timestamp `13:40:00`. So an invented head
count graded as grounded against an unrelated minute field.

**Fix.** Strip ISO timestamps from the page first, then compare **number tokens on both
sides** rather than asking whether a string occurs somewhere. That made the grader strictly
harder to pass, which is the only direction a grader is allowed to move.

**Lesson.** A grounding check is a claim that a specific value came from a specific place.
`in` answers a different and much weaker question, and it fails in the generous direction:
every digit string in a long page is a false alibi for some invented number. Also worth
noting the shape - **the bug was in the test's own failure case, and only having one
revealed it.** A grader with no red case is decoration, and it would have shipped here
looking green.

**Found:** M2.

---

## 12. Two honest numbers that disagree, handed to a model with no note

**Pain.** The first printed evidence packet said the Alkali Flat tank read **3.4 gal**
(what the sweep and triage judged) while the newest point of its own history series said
**0.8 gal**, at a *later* timestamp.

**Why.** `GET /sensors/:id` and `GET /sensors/:id/readings` are synthesized independently on
every call. Neither is wrong and neither is stale; they are two draws from the same
generator. The packet presented both as facts on one page.

**Fix.** One line in `render()` saying the series is shape and trend only and is not the
current reading, and the current reading stated once, unambiguously, as the thing triage
judged. The live model then quoted 1.9 gal as current and used the history only as a range
and a direction, which is exactly the intended behavior.

**Lesson.** A model handed two contradictory numbers with no guidance **will pick one**, and
it will sound just as confident either way. Assembling evidence in code is not only about
gathering it; the packet has to say which fact is load-bearing. Anywhere two sources of the
same quantity land on one page, the page owes the reader a sentence about precedence.

**Found:** M2, by printing the packet before any model existed, which was the whole reason
to do it in that order.

---

## 13. A token budget sized against an imagined page

**Pain.** `MAX_OUTPUT_TOKENS = 1536`, chosen against an estimate of ~1,400 input and ~450
output. The first live call measured **5,555 in and 1,137 out**. The estimate was off 4x on
input, and the output cap had about 400 tokens of headroom left on an answer that could
easily have been longer.

**Why.** The estimate counted the interesting part. The SOP file, loaded whole into every
prompt, is the majority of the input and was mentally filed as "small, it's just rules."

**Fix.** Measure, then set the constant, with the measured numbers **in the comment beside
it** so the next person changing it knows what it was fitted to. Raised to 2,048. And the
real finding: the cost lever is the SOP, not the evidence, which is the opposite of what it
looks like from the outside.

**Lesson.** A ceiling set below a real response does not error, it **truncates**, and a
truncated structured answer looks like a weak model rather than a config mistake. That is
what makes `finish_reason` worth logging, but the cheaper move is to print one real page
before choosing any number that bounds it.

**Found:** M2, on the first live call.

---

## 14. A flag that prevents an expensive mistake needs a guard behind it

**Pain.** None, because it was caught while wiring the spend stages. `run_tick` grew two
stages that call Opus. Ten existing test rails called `run_tick`. All ten would have started
billing, silently, and the suite would still have gone green.

**Fix, two layers.** `run_tick(spend=False)` at every call site, **and** an autouse
`conftest.no_model_calls` fixture that replaces `build_client` with something that raises.
The flag is the intent; the fixture is what happens when somebody forgets the flag.

`spend` defaults to `True`, deliberately. A default that quietly does nothing is a default
that ships, and a tick that silently stopped spending is indistinguishable from a calm ranch.

**Lesson.** Exactly the same shape as `assert_local_test_url`, which keeps the suite off
Supabase: a rule that only exists as "remember to pass the flag" is not a rule. This repo now
has two expensive mistakes, and each one has a flag for intent and a guard for reality. When
you add a third capability that costs money or mutates something, the question is not whether
there is an option to turn it off, it is **what fails loudly when nobody turns it off.**

**Found:** M2, in design.

---

## 15. Joining two services on a display name

**Pain.** The first evidence packet came back with **zero** sibling sensors and no pasture
context. Both lookups matched on location name.

**Why.** The `ranch://sensors/map` resource spells a location `"Alkali Flat (alkali-flat)"`
while the Sensor API's REST records say `"Alkali Flat"`. And the Farm API's pasture for the
sensor location `"East Allotment"` is named `"East BLM Allotment"`. Two independent name
mismatches, one cosmetic and one genuinely different label for the same ground.

**Fix.** Slugify to the id and match on that, handling both spellings of the map's format.
Match a pasture by id, never by display name.

**Lesson.** The failure mode is what makes this worth an entry: a name join that misses
returns **an empty set, not an error**. "No siblings at this location" and "no animals in
this pasture" are perfectly plausible facts about a ranch, so the packet read as complete and
merely thin. Same disease as cookbook #4 and #8 - a lookup that cannot distinguish "nothing
there" from "I asked the wrong question." When joining across services, join on the id, and
if a join can legitimately return empty, log the count so zero is visible.

**Found:** M2, by printing the packet and counting refs.

---

## 16. A mock answers whatever host you point it at

**Pain.** Chaos wrote animal events with `PATCH /animals/:id` and
`POST /animals/:id/observations`. Both were sent to `CARE_API`. Twelve rails passed,
including one asserting the exact request body. `/animals` is on the **Farm API**, and the
`PATCH` had never worked.

**Why.** `respx` intercepts by pattern, so a route registered on the base URL the code
happens to use always matches. **A wrong base URL and a right base URL are the same test.**
The suite could not have caught this, and `src/tools/CLAUDE.md` had warned about this exact
base-URL confusion since M2.

**Fix.** Two clients, and a rail that mounts `PATCH` on `https://farm.test` and `POST` on
`https://care.test`, with each route registered on one host only. Collapsing them back now
fails on an unmatched request rather than passing.

**Lesson.** When a mock is configured from the same constant as the code under test, the
constant is untested by construction. **Mount each service's routes on a different host in
the rail, so the topology is asserted and not assumed.** Any HTTP mock has this property;
it is not a `respx` quirk. Related: #4, #8, #15, all cases of a check that cannot fail.

**Found:** M5, by a live probe after the suite was already green.

---

## 17. Learning a frozen upstream's contract from its 422s

**Pain.** The animal observation body needed four fields and the code sent three, one of
them misspelled (`notes` for `note`) and one invented (`observedBy`, accepted and silently
dropped). The upstream repo is frozen and reading its source is forbidden, and the deployed
service ships no schema document.

**Fix.** **Send a deliberately invalid body to a nonexistent id and read the error.** A 422
from a validating API names the field and enumerates the enum, and a nonexistent id means
nothing real can be mutated even if a body turns out to be valid. Five probes produced the
whole contract: two required fields that were missing, one field name, and three enums.

**Lesson.** A validation error is free documentation, and it is documentation of the
**deployed** contract rather than of some repo's `main` branch. Then pin what you learned:
all three enums are now validated in `parse_catalog` at load, so a typo is a load-time error
instead of a 422 mid-demo.

**The caveat, learned the hard way.** A ghost id protects against mutating something real;
it does **not** protect against creating something. The fifth probe body was valid and
returned **201**, creating an orphan observation against an animal that does not exist. The
upstream is append-only, so it cannot be deleted (see `docs/JOURNEY.md`, M5). **Probe with
bodies you are confident are invalid, and stop the moment one succeeds.**

**Found:** M5, learning the write path without reading the frozen upstream.

---

## 18. Two worktrees, one test database

**Pain.** `sw_ops_test` lost a table mid-session and its alembic stamp went **backwards**
from `0002` to `0001`. Nothing in the worktree had run a downgrade. Twenty minutes went into
hunting a bug that did not exist.

**Why.** Two parallel sessions, two git worktrees, **one** local Postgres and one
`sw_ops_test` schema. `conftest.py` drops that schema `CASCADE` and re-migrates it with
`alembic upgrade head` from **its own worktree's** `alembic/` directory. The other branch had
no `0002`, so its test run rebuilt the schema without the table this branch depends on.

**Fix.** Nothing, structurally: each suite repairs the schema on its next run, so both stay
green in isolation. What is needed is the knowledge that concurrent `pytest` runs across
worktrees are a cross-session flake with **no local cause**.

**Lesson.** A session-scoped fixture that drops a shared schema is safe exactly as long as
one process at a time uses it, and a worktree does not isolate a database. If two agents are
going to build against one Postgres, the schema name is the thing that needs to be per
worktree, not the checkout. First suspect for any impossible-looking DB state: the other
session.

**Found:** M5's phase close, while trying to tidy up leftover rows.

---

## 19. An idempotent insert makes a demo replay a no-op

**Pain.** `chaos inject --tick 2 --seed 1` offered four events and inserted three. No error,
one log line, and the storm front was quietly missing a sensor.

**Why.** Event ids are derived from seed, tick, and index, so they are **stable by
construction** across replays, which is the property that makes a seeded demo reproducible.
The insert is `ON CONFLICT DO NOTHING` with no conflict target, so it skips on any unique
violation. A row from an earlier run of the same seed already held that id, and it was
`expired` rather than active, so the partial index on active events did not apply and the
primary key did.

**Fix.** Nothing in the code. The dedup is logged with a count and a hint naming both
causes, which is how it was diagnosed in one line.

**Lesson.** **Idempotence and replayability pull against each other.** A stable id means
running the same seed twice is a no-op, not a re-run, so re-driving a demo from the top means
truncating the table first. Both behaviours are correct; the failure mode is expecting the
other one. Log the skip count, because a silent skip and a successful insert look identical
from the caller.

**Found:** M5, exercising the CLI at the phase boundary.

---

## 20. `random.choices` is not a contract, `randrange` is

**Pain.** None yet, and that is the point of the entry.

**Why.** Seeded chaos is only worth building if a seed replays a demo months later, on a
different machine and a newer interpreter. `random.choices`, `random.sample`, and
`random.shuffle` are convenience helpers whose internal draw pattern CPython is free to
change; only the core generator is a documented, stable stream. A fixture that depends on a
helper's internals is a fixture with an expiry date nobody wrote down.

**Fix.** `plan()` rolls an explicit cumulative weight and calls `randrange` directly, and a
golden 14-row plan lives in `data/examples.json` with a rail asserting equality.

**Lesson.** When determinism is the deliverable, **depend on the narrowest primitive that is
actually specified.** The two lines a helper saves are not worth an unverifiable dependency.
And keep the seeded function pure: `plan()` takes a catalog and returns a list, so the golden
rail needs no database and no network, while the clock and the writes live in the impure
caller.

**Found:** M5, by design rather than by damage.

---

## 21. A console stream is not a log file, and `jq` says so badly

**Pain.** A documented query over the chaos log events died with
`startswith() requires string inputs` and then `parse error: Invalid numeric literal`. Both
messages blame the data.

**Why.** Three separate wrong assumptions in one line. The stream is the **console**, not a
file, so there was no file to read. The event name is `msg`, because a processor renames
structlog's positional `event` field before any renderer runs. And a console stream is
**not pure JSON**: the CLI prints a human-readable summary to the same place, and a traceback
is not JSON at all, so the first unparseable line kills the whole query.

**Fix.** `jq -Rrc 'fromjson? | select((.msg//"")|startswith("chaos_")) | …'`. `-R` reads raw
lines, `fromjson?` drops the ones that are not JSON instead of aborting, and `//""` survives a
line that has no `msg` at all.

**Lesson.** Any `jq` filter over a process's own output rather than over a curated file needs
`-R` with `fromjson?`, or it is a query that works until someone prints a sentence. And when a
log pipeline renames a field, **every documented query is a copy of that decision** and has to
be re-run when the decision changes. Same family as #10.

**Found:** M5's phase close, by the step that runs every command the docs claim works.

---

_More entries arrive with M3 onward. Candidates already known from the design: the `num_ctx`
shim trap, and why a gate must outlive its process._
