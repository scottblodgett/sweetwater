# tests - what each rail proves

## Never point a test at Supabase

Store tests use `DATABASE_URL_TEST`, a **local** Postgres, schema `sw_ops_test`. A test
suite that drops and recreates a schema against the same database the demo reads from
is how three answer keys got invalidated in a previous life of this project. There is a
local Postgres 16 on this machine; no Docker is required.

A test that needs the live ranch is marked and skipped by default. `pytest` must pass on
a plane. The store tests skip rather than fail when no local Postgres answers, for the
same reason.

## Three files, and do not add a fourth

`docs/Plan.md` names exactly `test_agent.py`, `test_tools.py`, and `test_api.py`, and the
tree matches it. Suites inside a file are separated by a section banner, not by splitting
the file, because one rail per module produced seven modules by the end of M1 and the plan
had drifted from the tree without a single gate noticing. `conftest.py` holds the
`sw_ops_test` fixtures. A new rail goes in the file that owns its subject; if that reads
wrong, the argument is with the plan, not with the layout.

## What each rail is actually protecting

A rail is only useful if the next person knows what it proves. Loosening one to make it
pass is the failure mode this file exists to prevent.

| Rail | Proves | If it fails |
| --- | --- | --- |
| tool counts per agent | isolation is enforced in **code**, not requested in a prompt | an allowlist drifted, or a tool was added to the wrong slice |
| no agent names a tool outside its set | the same, from the other direction | someone widened a slice to fix a symptom |
| triage truth table | severity is deterministic and code-owned | a threshold moved, or a type fell through a default |
| unknown sensor type trips `warn_once` | a new type can never read as nominal by accident | someone added a default branch |
| sweep concurrency ceiling | 160 reads never go out unbounded | a `gather` replaced `gather_bounded` |
| the three reconcile buckets | a persisting fault is `ongoing`, never re-alarmed | the incident key changed, or the partial unique index was dropped |
| a sensor that did not answer resolves nothing | one upstream outage cannot close every incident and report an all-clear | `read_sensor_ids` stopped being passed, or was widened to the whole catalog |
| an empty catalog fails the tick | an unreadable map cannot present as a calm ranch | someone made the empty case a quiet success |
| no SQL in this repo names a ranch schema | the only route to ranch data is HTTP | a query reached into `farm`, `feed`, `animal_care`, or `sensor` |
| only `sw_ops_test` is creatable or droppable | the answer-key accident cannot happen twice | the engine allowlist grew a third value |
| chaos replay under a fixed seed | chaos is a **fixture**, not a flake | a `random` call bypassed the seeded instance |
| a Tier-1 all-clear on a flagged incident is rejected | the cheap model cannot produce the worst possible output | **do not relax this one.** Escalate instead. |
| gate survives a process restart | a pause is a gate, not a delay | the checkpointer is not actually writing |
| every `audit_id` appears twice | no side effect is proposed without a recorded decision | a decision path skipped its log line |
| `finish_reason` present on every model call | "too weak" stays distinguishable from "never answered" | a call path bypassed `llm_client.py` |

## Graded, not asserted

Two checks grade the **reason text**, not the label: a work order must name a real
sensor and quote its real reading. A model can get severity right by echoing it and
still produce prose that helps nobody, and a label-only assertion passes happily while
the product is useless.

## Determinism

Fixtures use frozen literal timestamps. No test calls `datetime.now()` for a value it
later asserts on.
