# tests - what each rail proves

## Never point a test at Supabase

Store tests use `DATABASE_URL_TEST`, a **local** Postgres, schema `sw_ops_test`. A test
suite that drops and recreates a schema against the same database the demo reads from
is how three answer keys got invalidated in a previous life of this project. There is a
local Postgres 16 on this machine; no Docker is required.

A test that needs the live ranch is marked and skipped by default. `pytest` must pass on
a plane. The store tests skip rather than fail when no local Postgres answers, for the
same reason.

## And never point a test at a model

Same rule, other expensive mistake. `conftest.no_model_calls` is **autouse** and replaces
`llm_client.build_client` with something that raises, so a test that reaches a real model
fails instead of billing. From M2 the tick's last two stages spend money, so `run_tick`
takes `spend=False` and every rail passes it; the fixture is what happens when somebody
forgets. Rails that need a response build a `ModelResponse` directly, which is the honest
way to test a parser anyway. A recorded real Opus answer sits in `test_agent.py` as
`RECORDED_ANSWER` and is the calibration fixture: **if a prompt change makes it fail, the
rails did not get stricter, the answer got worse.**

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
| `finish_reason` logged **before** validation | a rejected answer still leaves a receipt | the log line moved below the parse |
| the recorded Opus answer passes every rail | the rails are calibrated against real prose | a prompt change made the answer worse, or a rail got stricter without meaning to |
| stored severity is triage's when the echo disagrees | one question has one answer | somebody stored the echo "because the model was right" |
| no-real-action, and an all-clear headline, cannot ship | the worst possible output cannot reach a human | **do not relax this one** |
| an accurate sentence about a healthy sibling is **not** an all-clear | the rail reads actions, not prose | somebody made the matcher scan the assessment |
| an invented rule id is rejected | a citation points at something that exists | the SOP text stopped reaching the checker |
| a call that never answered still produces a work order | an incident is never silently dropped | an exception path returns `None` instead of a `no_answer` order |
| one agent raising is not an outage for the other ten | a tick survives one sub-agent failing | `gather_bounded` lost its per-task guard |
| a tick told not to spend produces zero work orders | the free pass is genuinely free | a spend stage ran above the `spend` check |
| nothing a model reads carries an em dash | the house convention reaches the prompt too | a rewrite of the brief or an SOP |

## Graded, not asserted

Two checks grade the **reason text**, not the label: a work order must name a real
sensor and quote its real reading. A model can get severity right by echoing it and
still produce prose that helps nobody, and a label-only assertion passes happily while
the product is useless.

`ungrounded_numbers()` in `test_agent.py` is the grader: every number in the prose that
appears nowhere on the page the model was given. It comes with its own failure case, because
a grader that cannot fail is decoration.

**It compares number tokens on both sides, and strips ISO timestamps from the page first.**
The first version asked whether the string `"40"` appeared anywhere in the rendered packet.
It did - inside `13:40:00` - so an invented "40 head short by dark" graded as grounded
against an unrelated timestamp. Substring containment is not grounding.

## Determinism

Fixtures use frozen literal timestamps. No test calls `datetime.now()` for a value it
later asserts on.
