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

**Reopened at M3, from the other side.** Stripping timestamps from the page and not from the
prose fails in the strict direction: `compliance` is briefed to write for an auditor eight months
out, so it quotes the date it was handed, and `2026-09-10T20:08:41Z` in an assessment graded as
five invented numbers. Times and dates now come off **both** sides. The cost is that a fabricated
timestamp goes ungraded, which is accepted and written down rather than discovered later: nothing
in the prose is anchored to a time. A normalization applied to one side of a comparison is a bug
waiting for the other side to start using the thing you normalized away.

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

## 22. Every rail was local, so the one thing they could not see was scope

**Pain.** None yet, which is the point. M3 ran the same Opus call on the same evidence packet
twice, once with `COMPLIANCE_MANDATE` in the brief and once with it removed. The expectation was
an obviously worse answer. **Both answers passed every rail with zero violations and would have
shipped.** The unbriefed one echoed severity correctly, named its sensor, quoted only numbers on
the page, cited three real rule ids, and asked for a real action.

**Why.** Read the rails as a set and the hole is obvious in hindsight. `severity_mismatch`,
`all_clear`, `invented_rule`, `sensor_not_named`, `schema_invalid` - **every one of them is a
question about one incident, answerable from inside one work order.** An unbriefed model answers
all of them correctly, because none of them asks the question the brief exists to answer: which of
four agents are you, what belongs to your neighbours, and who is reading this in a year.

What the unbriefed answer did instead was invisible to code. One action instead of five, and that
action was filing a note. The two stock tanks it correctly identified as somebody else's got
"belong to a separate water work order" with no name attached. The gauge it could not believe went
into `unknowns`, which is where facts go to be nobody's problem, rather than being handed to the
agent that repairs instruments. Its headline promised a GM escalation its actions list never
contained.

**Fix.** There isn't one, and reaching for one is the trap. A rail that counts actions gets
satisfied by padding; a rail that greps for neighbour names teaches the model to name neighbours.
What went in instead: both answers pinned as fixtures next to the packet they were given, and the
recorded-answer calibration test that catches a brief regressing **by diff rather than by rule**.
The transcripts are committed in `docs/`, because the claim "a sub-agent inherits nothing" is worth
less as an assertion in a doc than as two files a person can read side by side.

**Also worth its own sentence:** the whole of `compliance.md` was on the page in both runs,
including the two rules that name the owner of a bad instrument and of a stock tank in as many
words. **The SOP did not rescue it.** Standing orders describe the domain; the brief says which
part of the domain is yours. They are not substitutes and one does not imply the other.

**Lesson.** When you have a set of validators, ask what they have in common rather than what each
one covers, because the shared assumption is the shape of the hole. Here every rail took one
incident as its unit, so no rail could see a cross-incident property, and the failure mode that
survived is the one that only shows up between work orders. **A clean suite is evidence about the
questions you asked.**

**Found:** M3, by an experiment set up expecting a different result. The experiment was worth
running precisely because it disagreed with its own hypothesis.

---

## 23. A budget bug that arrives wearing the model's clothes

**Pain.** The first live M3 tick logged `shift_report_violations=['all_clear']`. Read literally:
the supervisor looked at a ranch with ten critical incidents on it and reported that everything
was fine. That is the single worst output this system can produce and the rail it has the loudest
opinion about.

**It had not done that.** `SHIFT_REPORT_MAX_TOKENS` was 1,024 and the answer needed 1,646, so the
tool call was cut off inside the priorities list.

**Why the label was wrong.** A tool call truncated at `max_tokens` still arrives with an `input`
dict on it, **partially filled**. `synthesize` checked `payload is None`, which was False, so a
half-written answer went through the rails, where empty priorities read as an all-clear. Both
things then happened at once: the correct fallback fired and the page shipped, and the log blamed
the model for a number in a config file.

**Fix.** `if response.payload is None or response.truncated`, in both `synthesize` and
`to_work_order`, which is what `ModelResponse.ok` already said and neither caller was asking.
Budget raised to 3,072, sized off the 1,646 measurement with the schema's own caps reasoned about
in the comment, rather than picked again.

**Lesson.** Two of them, and the second is the general one. First: #13 all over again, so the
version of that lesson that actually sticks is **print the page, then size the budget, then check
that a budget failure still says budget.** Second, and bigger: this repo is careful to make every
failure produce output - a `no_answer` work order, a code-assembled shift report, a tick line even
when the tick dies. A fallback that fires correctly while **attributing the failure to the wrong
component** is worse than a crash, because it is quiet and it accuses. When you write a fallback,
test what it says the cause was, not only that it ran.

**Found:** M3, on the first live `--once` after the stage was wired, by reading the log line
instead of trusting the exit code.

---

## 24. A concurrency ceiling that got multiplied by the number of workers

**Pain.** Caught while writing the fan-out, before it ran. `AGENT_CONCURRENCY = 4` had one
meaning at M2, when there was one agent: at most four Opus calls in flight. Fanning out to four
responders, each calling `gather_bounded(..., limit=AGENT_CONCURRENCY)`, means each agent politely
bounds **itself** at four. The ceiling is sixteen, and nothing in the code says sixteen anywhere.

**Fix.** `gather_bounded` takes an optional `sem`, and `fan_out` builds **one** semaphore and
passes the same object into every agent. The rail measures peak in-flight calls across a 16-packet
fan-out and asserts it never exceeds the constant, rather than asserting the constant equals 4,
which would have passed happily on the broken version.

**Lesson.** A limit expressed per-worker is not a limit on the resource; it is a limit times the
number of workers, and the multiplier is invisible at the call site that looks correct. Any
constant whose name is about a shared external resource - an API's rate limit, a connection pool,
a spend rate - belongs to **one object that everybody shares**, not to a value everybody reads.
And test the property, not the constant: a test that asserts a config value is a test that a
config value exists.

**Found:** M3, in design, by asking what the constant was a statement about.

---

## 25. A hundred and sixty item failures added up to a green tick

**Pain.** Found at M4 by asking, before killing an upstream live, what a dead Sensor API would
look like from inside the code. The answer was: nothing. `read_sensor` turns every transport
failure into a per-sensor `SweepError`, which is correct for one dark sensor, so 160 of them
produced a sweep with zero readings and zero raised exceptions. The tick was green,
`sensors_read` was 0, reconcile correctly resolved nothing, and the loop would have read 160
connection errors on every tick at cadence forever. Backoff never fires on a stage that did not
fail.

**Fix.** `run_tick` raises when the sweep has errors and no readings: "sweep read nothing: all
160 reads failed." One dark sensor is still one dark sensor; every sensor dark is the upstream.
The rail serves three broken sensors and asserts `failed_stage == "sweep"`, then serves one
broken and asserts the tick is fine.

**Lesson.** An aggregate of item failures is a different fact from the item failures, and code
that handles each item well can be blind to all of them failing together. Any loop that
tolerates per-item errors needs a second question at the end: did anything succeed at all? And
plan a live kill by first predicting what the code will show; if the prediction is "nothing
changes," the kill would have proved nothing and the code has a hole.

**Found:** M4, in design, before the live run.

---

## 26. An armed feature flag plus a prod default put the test suite on Supabase

**Pain.** `.env` on this machine has `CHAOS_ENABLED=1`, left over from driving M5. Every tick
test calls `sweep()`, which calls `active_overlay()`, which reads the real settings, sees chaos
armed, and resolves a store with `resolve_store()`, whose default is prod. So `pytest` had been
reading `sw_ops.chaos_events` on Supabase, read-only and by accident, on every tick test. The
first rule of `tests/CLAUDE.md` is "never Supabase," and no rail caught it because a read
leaves no trace. From M4 it would have been worse: the tick injects when armed, and the plan
would have been written into `sw_ops_test` on every test.

**Fix.** An autouse `chaos_off` fixture in `conftest.py` patches both `get_settings` call sites
with a copy of the real settings and chaos disarmed. A test that wants chaos patches it back,
which the chaos suite already did, and a later patch wins.

**Lesson.** A feature that reads an env flag and a store whose default is prod are each fine;
together they mean a developer's `.env` decides whether the test suite touches production. Any
test suite that shares a `.env` with the live process needs one fixture whose whole job is
disarming the flags that reach outward, and it has to be autouse: opt-in safety is the same as
none. Also: `CHAOS_ENABLED` "belongs at 0" was true in the docs and false on the machine.
Check the machine.

**Found:** M4, while wiring chaos into the tick and asking where the tests would inject.

---

## 27. You cannot repoint an upstream mid-run, so own the thing between you and it

**Pain.** The M4 verification calls for killing an upstream mid-run "by pointing it at a bad
URL." Settings are read once at process start and cached, correctly, so there is nothing to
repoint without a restart, and a restart is not a mid-run failure. Editing code to reload
settings for the sake of one test is the test bending the product.

**Fix.** A forty-line forwarding proxy on localhost, outside the repo, and `SENSOR_API` pointed
at it for the run. Killing the proxy process is the upstream dying; starting it again is the
upstream recovering. The loop sees `ConnectError`, backs off, skips, retries, and recovers, and
knows nothing about the proxy.

**Lesson.** To fault a dependency you do not control from a process you cannot reconfigure,
put something you do control in the path and fault that. It is the same move as the chaos
overlay: the lie lives in one place you own, and the thing being tested is untouched.

**Found:** M4, planning the live run.

---

## 28. Write the shutdown for the platform it runs on, not the one in the tutorial

**Pain.** Every asyncio shutdown example is POSIX: `loop.add_signal_handler(SIGTERM, ...)`.
On Windows 11, where this runs, that call raises `NotImplementedError`, SIGTERM is never
delivered to a console process, and Ctrl+C arrives as `KeyboardInterrupt`. The predictable
path is to write the POSIX version, watch it fail, and weaken shutdown to a flag nobody sets.

**Fix.** Lean on what Python 3.11 already does: `asyncio.Runner` turns the first Ctrl+C into a
cancel of the main task and the second into `KeyboardInterrupt`. Run the tick as its own task
behind `asyncio.shield`, catch the cancel in the loop, drain the tick, write the line, exit 0.
Register SIGTERM and SIGBREAK through `signal.signal`, which exists everywhere and is a no-op
where the signal is never delivered. Verified live with `CTRL_BREAK_EVENT` from a driver
process, which is the one interrupt Windows will deliver to a child.

**Lesson.** Graceful shutdown is a platform question before it is an asyncio question. Find
out what the platform actually delivers, then build on the runtime's own handling of it rather
than fighting it. And a shutdown path that cannot be exercised from a script is a shutdown path
that has never been tested; SIGBREAK made it scriptable.

**Found:** M4, in design, from the prompt's own warning.

---

## 29. Code before `interrupt()` runs twice

**Pain.** The obvious shape of a human gate node is: write the `proposed` audit line, then
`interrupt()`. The first spike counted the node's runs across one propose and one resume and got
two. LangGraph does not freeze a node mid-line; it re-executes the node from its first statement
when the graph resumes, and `interrupt()` returns the human's answer the second time through.
Anything above it with a side effect happens twice, and for an audit stream that means a
`proposed` line per proposal plus one more per decision, which reads as a second proposal nobody
answered.

**Fix.** The node does nothing but ask. The `proposed` line is written by `propose()` outside the
graph, before `ainvoke`; the `decided` line is written by the node after the interrupt, which runs
exactly once per decision.

**Lesson.** An `interrupt()` is a re-entry point, not a pause button. Treat everything above it in
the node as idempotent or move it out. Measure the run count in the spike before building on it.

**Found:** M6, first spike, on `sw_ops_test`.

---

## 30. The async Postgres checkpointer refuses Windows' default event loop

**Pain.** `AsyncPostgresSaver` is the documented pairing for an async graph. On Windows it raises
`Psycopg cannot use the 'ProactorEventLoop'` on connect, and the Proactor loop is what asyncpg,
httpx, and the MCP client in this process already run on. The obvious fix, switching the whole
process to the selector loop, is a policy change every other component and every test inherits to
please one driver.

**Fix.** The sync `PostgresSaver`, driven from the async graph through a worker thread:
`memory.ThreadedPostgresSaver` runs each sync method under `asyncio.to_thread` behind one lock,
because a psycopg connection is not safe for concurrent use. The sync saver has no loop affinity
at all, which also sidesteps the "engine bound to the loop it connected on" problem the store
fixtures already work around. The sync saver's own async methods raise `NotImplementedError`
rather than doing this, which is how the second spike found out.

**Lesson.** When a library's async variant fights the platform, the sync variant in a thread is
usually the smaller change than bending the platform to the library. Twenty lines of adapter
versus a process-wide event loop policy.

**Found:** M6, first and second spikes.

---

## 31. A library that creates its own tables is a migration you did not review

**Pain.** `saver.setup()` creates the checkpointer's four tables at first use, on whichever
database the process is pointed at, silently. Decision 1 says every Supabase migration is one
alembic revision Scott has seen in full before it touches prod. Those two facts cannot both hold
if the loop calls `setup()`.

**Fix.** Migration `0004` reads the library's own `MIGRATIONS` list at migration time (not copied
into the file, so it cannot drift from the installed version), strips `CONCURRENTLY` because alembic
runs in a transaction and the tables are empty, and writes the version rows `setup()` would have
written so a later `setup()` is a no-op. `memory.checkpointer()` then refuses a database whose
`checkpoint_migrations` is behind the installed library, naming the next alembic revision as the
fix, and never calls `setup()` itself.

**Lesson.** "The library manages its own schema" means the library runs DDL on prod when it
feels like it. Run its DDL through your migration path, and turn "the library got upgraded" into
an error that names the migration rather than a schema change nobody reviewed.

**Found:** M6, reading `setup()`'s source before deciding who runs it.

---

## 32. A rail that counts log lines has to know which lines are receipts

**Pain.** The audit rail is "every `audit_id` appears exactly twice." Its first implementation
counted every captured log line carrying an `audit_id`, and the first run against real logs
reported a `dropped` proposal at count three. The extra line was `write_proposal_dropped`, a
console line that mentions the id so a human can find it, not a receipt. The rail would have
flagged every `write_paused` line the same way.

**Fix.** The rail counts only lines whose `phase` is `proposed` or `decided`. Commentary may
mention an id; only the two audit phases are receipts.

**Lesson.** When a field is useful in two streams for two reasons, the check has to name the
stream it is checking. Otherwise the more you log for humans, the more the rail lies.

**Found:** M6, first run of the rail against real lines.

---

## 33. The first `configure_logging` in a process decides where every test writes

**Pain.** Nine `proposed` lines with a test fixture's `run_id`, and six orphaned `decided` lines,
in the real `logs/audit.jsonl`, each one a pause nobody would ever answer and a false positive for
the rail above. `configure_logging` is idempotent, `alembic/env.py` calls it, and the session-scoped
`migrated_store` fixture runs the migration before any function-scoped fixture can patch the log
directory. From then on every file handler in the process pointed at the repo's `logs/`, and any
audit line a test wrote outside `capture_logs` landed in the receipt file.

**Fix.** `migrated_store` runs the migration under a patched `get_settings` whose `log_dir` is a
temp directory, the autouse `settings` fixture patches the same for every test, and a rail asserts
that no configured file handler points inside the repo's `logs/`. The leaked lines were removed by
hand, once, because a fixture is not a receipt.

**Lesson.** Idempotent process-wide configuration is decided by whoever calls it first, and in a
test suite that is a session-scoped fixture, not the test you are looking at. A receipt file that
a test can reach is a receipt file that will eventually carry a test.

**Found:** M6, running the audit rail over the real file after the suite.

---

## 34. Measure the library's behaviour in a spike before writing the module around it

**Pain.** Five questions about LangGraph's checkpointer had no reliable answer from memory:
whether `interrupt()` survives a new connection, whether `config["metadata"]` reaches the
checkpoint row so a filter can find gate threads, what a resume on a finished thread does, what
an unknown thread returns, and how many times the node runs. Any one of them wrong would have
been discovered in the tests, after the module was written around the wrong assumption.

**Fix.** A sixty-line script on `sw_ops_test` answered all five in one run before `gate.py`
existed: the pause survives a new connection; the metadata filter finds the proposal's
checkpoints but not the resume's unless the resume passes the same metadata; a second resume is a
silent no-op returning the final state, so `decide()` has to check for a live interrupt itself;
an unknown thread is empty `values`, not an error; the node runs twice (#29). The module was
written to those measurements.

**Lesson.** For a library feature you have not used, the spike is cheaper than the second draft
of the module. Write down what it measured, because the docstrings that follow are claims and the
spike is the evidence.

**Found:** M6, before the first line of `gate.py`.

---

## 35. Count the page in the target model's tokenizer before the first local call

**Pain.** `num_ctx` was set to 16,384 on the strength of "the Opus count is 5,500, that fits." Two
tokenizers, two counts, and Ollama does not fail a prompt longer than `num_ctx`: it truncates from
the front and answers about the rest, which for this repo means a model that never saw the SOP and
cites rules it did not read. That presents as a weak model and is a config bug.

**Fix.** One real assembled packet through `call_tier1` before any tick, reading
`prompt_eval_count` back: 3,024 (feed) and 3,586 (water) tokens against 16,384, so the SOP was never
at risk. `call_tier1` now logs `tier1_context_full` when the count is within a hundred of the
ceiling, so the day a packet grows the log says so before the citation rail does.

**Lesson.** "It fits" is a number in the tokenizer that will read it. Measure once in that
tokenizer, then make the code say when the measurement stops holding.

**Found:** M7, by measuring first. The trap the design warned about since M2 was paid for without
tripping it.

---

## 36. A model allowed to say "I do not know" will say it wherever the rulebook asks for a fact the page lacks

**Pain.** `insufficient_information` was added so the local model could decline instead of
inventing. It declined on 5 of 6 candidates. Reading the pairs, it was right each time: FEED-02
asks for the forecast, SENSOR-01 asks how long the sensor has been dark, and the page carried
neither. Opus, same pages, wrote around the gap and listed it under `unknowns`.

**Fix.** The field's description now names what is an unknown (forecast, fuel, head count, tank
capacity) and what is insufficiency (the incident's own reading missing, no rule on the page). That
moved the water packet from 5/5 to 2/5 and the feed packet from 5/5 to 4/5. The rest is not a prompt
problem: the feed page needs the weather station on it (`docs/issues.md` #12).

**Lesson.** An honest exit gets used exactly as often as the inputs are honestly insufficient. When
a cheap model takes it constantly, look at the page before the model; the fix is usually more
evidence, not less permission.

**Found:** M7, first measurement.

---

## 37. A citation can be right and fail a format check; trim in the parser, judge in the rail

**Pain.** `rules_cited: ["FEED-02 - A bin at the warning line is a delivery to schedule, not a
fire"]` on four of five local answers. The id was right every time. `invented_rule` compared the
whole string and rejected, which would have escalated a correct order to Opus over punctuation.

**Fix.** `normalize_citations` trims each citation to the rule id at its front before the rail runs,
and records `rule_citation_trimmed` (non-blocking). The rail is unchanged: a trimmed id still has to
be a heading in the SOP the packet carried, and `WATER-09 - anything` still rejects.

**Lesson.** Separate "did it name a real rule" from "did it spell the reference the way we like."
The first is a rail. The second is a parse step, and a parse step that is recorded is still
countable in a ledger row.

**Found:** M7, first measurement.

---

## 38. The rate was one constant, and it was wrong by 3x; the tokens beside it were fine

**Pain.** `ASSUMED_RATE_USD_PER_M = (15, 75)` from M4, stated as an assumption in one place. The
Opus 5 list price is $5 / $25. Every dollar figure in three docs for four phases was three times the
real bill, including the "$28 an hour" that motivated the debounce (which was still the right call at
$9).

**Fix.** `routing.PRICE_TABLE`, per model id as the provider spells it, Tier 1 free by tier, an
unknown paid model billed at the most expensive known row with one warning. The historical rows
were left as written with the correction beside them, because their token counts are the
measurement and the dollars were arithmetic.

**Lesson.** State the assumption in one place, yes, and also write down where it came from, so
the day it is checked the check is one lookup. And when it turns out wrong, correct the arithmetic
and keep the measurement; rewriting history to the new rate destroys the record of what you thought
when you decided.

**Found:** M7, loading the price table.

---

_Candidates still known from the design and not yet paid for: none._
