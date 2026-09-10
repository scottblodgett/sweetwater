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

_More entries arrive with M2 onward. Candidates already known from the design: severity
ownership, the `num_ctx` shim trap, `finish_reason` as a diagnostic, seeded chaos as a
fixture rather than a flake, and why a gate must outlive its process._
