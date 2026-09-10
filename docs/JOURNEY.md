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

---

## M1 - The free pass

_Not started._

## M2 - One agent, Opus only

_Not started._
