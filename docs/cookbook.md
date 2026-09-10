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

_More entries arrive with M1 onward. Candidates already known from the design: severity
ownership, the `num_ctx` shim trap, `finish_reason` as a diagnostic, seeded chaos as a
fixture rather than a flake, and why a gate must outlive its process._
