# tests - what each rail proves

## Never point a test at Supabase

Store tests use `DATABASE_URL_TEST`, a **local** Postgres, schema `sw_ops_test`. A test
suite that drops and recreates a schema against the same database the demo reads from
is how three answer keys got invalidated in a previous life of this project. There is a
local Postgres 16 on this machine; no Docker is required.

**No test in this repo touches the live ranch, and none is marked to.** `pytest` must pass
on a plane, so every upstream is a respx route against a fake host. Live verification is a
**documented command**, not a test: `--handshake` proves the deployed contract (and fails on
tool-surface drift against `allowlists.DEPLOYED_TOOLS`), and `--once` is the per-phase live
run. That split is deliberate. A suite that is green only on a good network stops being a
gate, and a check that has to reach the wire belongs in the thing whose whole job is
reaching the wire.

The store tests skip rather than fail when no local Postgres answers, for the same
plane-must-pass reason. That is the one conditional skip here.

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
| one **agent** raising is not an outage for the other three | a tick survives a whole slice failing, not just one packet | `fan_out` stopped catching per-agent exceptions, or dropped the packets it was carrying |
| the concurrency ceiling is global across four agents | `AGENT_CONCURRENCY = 4` means four Opus calls in flight, not sixteen | the shared semaphore stopped being passed down and each agent bounds only itself |
| every routed incident produces a work order | nothing that reached the fan-out is silently dropped | a grouping step lost a key, or an error path returned fewer orders than packets |
| `herd_health` returns empty and logs nothing | the routing table working, not a gap (`docs/STATE.md` decision 5) | an idle agent started warning once per tick, which trains everyone to ignore the log |
| one world reporting costs zero tokens | the supervisor's cost lever is the `if`, not the model | the `FUSION_THRESHOLD` check moved below the call |
| a shift report linking an incident nobody handed over is thrown away | the fusion claim is checkable data rather than prose | `linked` stopped being compared against the keys the page carried |
| a rejected shift report is replaced, never retried | the rail stays a rail instead of becoming a sampler | somebody added a second attempt to get a cleaner page |
| a supervisor that never answered still produces a page | the person coming on shift is briefed whatever failed upstream | the fallback stopped carrying the work orders or the receipt |
| a tick told not to spend produces zero work orders | the free pass is genuinely free | a spend stage ran above the `spend` check |
| nothing a model reads carries an em dash | the house convention reaches the prompt too | a rewrite of the brief or an SOP |
| the unbriefed and the briefed answer are **both** clean | the honest limit of every rail above: they grade one incident, not scope | nothing. It is a pinned finding, not a rail. See below |

## Graded, not asserted

Two checks grade the **reason text**, not the label: a work order must name a real
sensor and quote its real reading. A model can get severity right by echoing it and
still produce prose that helps nobody, and a label-only assertion passes happily while
the product is useless.

`ungrounded_numbers()` in `test_agent.py` is the grader: every number in the prose that
appears nowhere on the page the model was given. It comes with its own failure case, because
a grader that cannot fail is decoration.

**It compares number tokens on both sides, and strips times and dates from both sides first.**
The first version asked whether the string `"40"` appeared anywhere in the rendered packet.
It did - inside `13:40:00` - so an invented "40 head short by dark" graded as grounded
against an unrelated timestamp. Substring containment is not grounding.

Stripping only the page failed the other way, which the `compliance` transcript below caught:
that agent is briefed to write for an auditor eight months out, so it quotes the date it was
given, and `2026-09-10T20:08:41Z` in the prose then graded as five invented numbers. Times come
off both sides. The cost is that a fabricated timestamp goes ungraded, and that is accepted: no
number in the prose is anchored to a time anyway.

## The two things a rail cannot see

`docs/no-brief-transcript.md` and `docs/with-brief-transcript.md` are one live pair: the same
model, the same evidence packet byte for byte, one variable, which is whether
`COMPLIANCE_MANDATE` was in the brief. **Both answers pass every rail with zero violations.**
The unbriefed one writes one action instead of five, hands nothing to a named neighbour, buries
a suspect gauge in `unknowns`, and promises an escalation in its headline that its actions list
never contains.

Both are pinned in `test_agent.py` as `NO_BRIEF_ANSWER` and `WITH_BRIEF_ANSWER`, and the tests
over them assert the **sameness** of the rail verdict, not a quality difference. That is the
point: every rail asks whether an answer is defensible about its own incident, and scope is not
answerable from inside one work order. `RECORDED_ANSWER` is the only thing in the suite that
would notice a brief regressing, and it notices by diff rather than by rule.

Do not turn any of it into a blocking rail. A rail that counts actions is a rail that gets
satisfied by padding.

## Determinism

Fixtures use frozen literal timestamps. No test calls `datetime.now()` for a value it
later asserts on.
