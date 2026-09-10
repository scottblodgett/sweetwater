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

_Not started._
