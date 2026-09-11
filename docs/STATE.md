# STATE

The session-start briefing. **Read this one file, then the nested `CLAUDE.md` for the package you are about to touch, then stop and ask.** It exists so a new session does not have to read the doc set, probe the live ranch, or re-derive a decision that is already made.

Refreshed at every milestone boundary, same step as `JOURNEY.md`. If it disagrees with the code, the code wins and this file is stale, say so.

**Last refreshed:** 2026-09-11, at the **M8** boundary. M3 and M5 were built in parallel
sessions on one `master` and M5 closed first, so the phase numbers are not the commit order;
the debounce landed between M4 and M6 as its own commit. `git log` is the authority on what
landed when.

---

## Where the build stands

| | |
| --- | --- |
| Done | **M0**: tree, three log streams, `config.py`, `mcp_client.py`, live handshake. **M1**: the free pass, `catalog -> sweep -> triage -> reconcile -> route`, five stages and zero tokens, plus the `sw_ops` store and Alembic. **M2**: the first phase that spends. `evidence.py`, `llm_client.py`, `system_prompts.py`, `workers.py`, the water and feed SOPs, and one agent (`water_feed`) writing a real work order per newly-opened incident. **M3**: the four responders and the supervisor. `allowlists.py` and the five tool slices, `agent_prompts.py` and the five briefs, `workers.fan_out` under one global ceiling, `agent.synthesize` and the shift report, and the four remaining SOPs. **M5**: chaos. The seeded overlay in `sw_ops.chaos_events` applied inside `sweep()`, the guarded animal write path, migration `0002`, the `python -m src.tools.chaos` CLI, and 64 new rails. Built beside M3, not after it. **M4**: the continuous loop. `run_loop` in `executor.py` with a start-to-start cadence, a spend ceiling that halts (exit 4), per-upstream backoff, a held set for unanswered incidents, chaos wired into the tick with a miss check, and a drain-on-interrupt shutdown that works on Windows. `cost_usd` on the tick line. `--no-spend`. **The debounce** (2026-09-10, own commit): `INCIDENT_CONFIRM_SWEEPS`, migration `0003`. **M6**: the gate. `proposed_write` on the work order, three code checks on it (`workers.check_write_proposal`), `src/agent/gate.py` (one LangGraph `interrupt()` graph per proposal, checkpointed in Postgres), `python -m src.agent.gate` for the human, `Approval` in `assert_callable`, migrations `0004` (the checkpointer's tables) and `0005` (`incidents.held_reason`), and `GATE_LANDED` flipped last. **M7**: model routing. `src/models/routing.py` (the price table, `tier_for`, the escalation predicate), `call_tier1` on `ChatOllama` in `llm_client.py`, the cascade inside `workers.judge_packet` as a rewrite with the Tier-1 receipt kept, `insufficient_information` on the schema, `tier` / `tier1_orders` / `escalations` / `escalation_reasons` on the tick line, `TIER_COMPARE` and `logs/compare.jsonl`. **Measured and not adopted**: `TIER1_ENABLED` ships off, see the M7 section. **M7A**: the herd sweep, the coyote gap. `src/tools/herd.py` (the Farm list in waves of 6, the roster as context, the care record for the changed set, `answered`), `triage.triage_herd` and four animal categories, migration `0006` (`subject_id` / `subject_type`, on Supabase with Scott's yes), `knowledge_base/herd.md` (HERD-01 to HERD-07), the animal page in `evidence.py`, `ROUTES` for `herd_health`, the herd stage in `run_tick`, animal events in the miss check, `LOG_TRANSCRIPTS` made real. Closed `docs/issues.md` #1, #2, #11, #15 with one live `create_observation` pause, audit `968e7f64fb7540dc9adeed547d15e802`. **M8**: the read API. `src/api/routes.py` (`create_app`, the envelope, the request-id middleware, six routes) and `schemas.py`, migration `0007` (`ticks`, `shift_reports`, `audit_receipts`), `_record_tick` in the executor, `record_receipt` in the gate with three named `GateError` subclasses, `OPS_API_TOKEN`, `--api` real and exit 3 retired. Closed `docs/issues.md` #10. |
| Gate | **433 tests**, `ruff check .` clean, `mypy` clean on 31 files, one alembic head at `0007` (applied to Supabase 2026-09-11 with Scott's yes), and every command in root `CLAUDE.md` re-run. M8's live check: the loop on the test ledger at a 20s cadence and `--api` beside it as a second process, fourteen ticks landing on `/ops/stream`, every route curled and the envelopes pasted into `docs/JOURNEY.md`, a pause planted by a third process listed at `GET /ops/gate` and rejected over HTTP with the token (`decided_by` from the token), the second answer 409, both receipt rows in `audit_receipts` and both lines in `audit.jsonl`. |
| Next | **M9, the window**, the last M phase: Next.js on Vercel against `/ops/*`, and its close carries the doc pass (`README.md` current, `docs/cookbook.md` ordered by pain, `docs/JOURNEY.md`'s final pass). `API_CORS_ORIGINS` is where its origin goes. Before it shows a human anything: #17 (a dead cow's pasture), #16 (a `high` observation on an `active` animal with no task is invisible). Still before the cascade is tried again: the forecast on the feed page (#12). |
| HEAD | The M8 boundary on `master`. A hash here is stale by one commit by construction, so trust `git log` over this cell and the milestone over both. |
| Owed | Nothing from M8. Open and scoped: `docs/issues.md` #4 (miss check across runs), #12 (the forecast on the feed page, before the cascade is retried), #13, #14, the M7A items #16 to #19, and #20 (the chaos guard's receipts are file-only). #10 closed at M8. |

Everything still open across phases, with what each would take: `docs/issues.md`. Milestone list and the shape: `docs/architecture.md`. The layout, the milestone order, and which leaf lands when: `docs/Plan.md`, which is the authority the tree matches. What happened and what diverged: `docs/JOURNEY.md`. Phase-close ritual: root `CLAUDE.md`.

---

## Where the code lives. Two modules were renamed at the M1 boundary.

`docs/Plan.md`'s tree is the authoritative layout, and the code was conformed to it rather than the reverse. There is **no `tick.py` and no `src/agent/routing.py`**; a search for either finds nothing, and that is the current state, not a missing file.

| Module | Holds | Grows |
| --- | --- | --- |
| `src/agent/executor.py` | `run_tick` (ten stages, the `spend` flag, `held` / `skip` / chaos inputs, the one `tick.jsonl` line), `_gate_step` (the key check, the hand-off to the gate, `writes_*`), `_write_held` / `restore_held`, `run_loop` (cadence, ceiling, shutdown, the held restore), `Backoff`, the exit codes, `STAGE_UPSTREAM`, `RETRIABLE_VIOLATIONS` | nothing structural. The tick is deliberately **not** a graph: one waiting proposal must not stop the watch, and `interrupt()` blocks the graph it is in |
| `src/agent/gate.py` | **M6.** `GateState`, `build_gate` (ask -> execute, `interrupt()` in `ask`), `propose` / `pending` / `decide`, `perform_write` (the one path that performs an agent's write), `unpaired_audit_ids` (the rail), and the CLI `python -m src.agent.gate list / approve / reject`. **M8**: `record_receipt` (the row before the line, strict on `proposed`), `GateNotFound` / `GateAlreadyDecided` / `GateInvalidDecision` | nothing structural; `/ops/gate` wraps `pending` and `decide` and adds nothing |
| `src/tools/herd.py` | **M7A.** `sweep_herd` (the roster, then the Farm list in waves of `HERD_PAGE_CONCURRENCY = 6` stopping at the first short page, the pending care tasks, observations for the changed set), `HerdSweepResult` (`animals`, `observations`, `care_tasks`, `answered`, `failure`), `PastureRoster` / `PastureContext` / `parse_pastures` / `slugify` (moved here from `evidence.py`, re-exported there), `parse_timestamp` | nothing structural. A pastureless-animal filter or a ranch-wide observation read upstream would each remove a wave |
| `src/agent/agent.py` | `ROUTES` (all 22 categories to an owner), `AGENTS` / `RESPONDERS`, and the supervisor's own stage: `render_shift_page`, `assemble_shift_report`, `check_shift_report`, `synthesize` | nothing structural |
| `src/agent/workers.py` | the rails (`check`, `check_write_proposal` with the three write checks), `to_work_order`, `judge_packet`, `run_agent` for any of the four, `fan_out` under one shared semaphore | nothing structural |
| `src/tools/allowlists.py` | `DEPLOYED_TOOLS` (all 19), `WRITE_TOOLS` (all 8), `WRITE_TOOL_ARGS` (per-argument kinds, read off the wire 2026-09-11), `SLICES`, `tools_for` / `bound_tools_for` / `proposable_tools_for` / `assert_callable(approval=)`, `Approval`, and `GATE_LANDED` (**True since M6**) | a new deployed tool, which is a conversation with the upstream first |
| `src/agent/state.py` | `RanchState`, `Finding`, `Incident`, `WorkOrder`, `ShiftReport` | |
| `src/agent/memory.py` | `sw_ops` only: reconcile (`read_subject_ids` from M7A), the engine allowlist, the chaos-event store, the held column (`held_incident_keys`, `record_held`), `live_animal_subjects` (M7A, the herd sweep's watch list), and the checkpointer (`checkpointer()`, `ThreadedPostgresSaver`, `assert_checkpointer_migrated`, `psycopg_url`) | nothing structural |
| `src/tools/chaos.py` | the scenario catalog, the pure seeded `plan()`, `inject_for_tick`, `apply_overlay`, the guarded animal write path, and the CLI | more scenarios; nothing structural |
| `src/tools/evidence.py` | `assemble` (a sensor page or, from M7A, an animal page built from `HerdSweepResult` with zero HTTP), `EvidencePacket.render`, `AnimalContext`, `SOP_FOR_CATEGORY` covering all 22 categories | nothing structural |
| `src/models/llm_client.py` | `resolve_provider`, `build_client`, `call_tier2`, `ModelResponse` (with `tier` and `.cost_usd`), `THINKING_BUDGET`; **M7**: `build_tier1_client`, `call_tier1`, `Tier1Unavailable`, `OLLAMA_KEEP_ALIVE`. `cost_usd` is re-exported from `routing` | a third provider, which widens `ok` / `truncated` and the price table together |
| `src/prompts/system_prompts.py` | what a **machine** consumes: `WORK_ORDER_SCHEMA`, `SHIFT_REPORT_SCHEMA`, the tool names and descriptions, `system_prompt()` | more schemas |
| `src/prompts/agent_prompts.py` | what a **model** reads: `INHERITED_RULES`, `MANDATES` (the four responders only, `chaos` absent and a test asserts it), `SUPERVISOR_MANDATE`. **`MANDATES` moved here at M3**; it is no longer in `system_prompts.py` | chaos's brief, if chaos ever needs one |
| `src/models/routing.py` | **M7.** `PRICE_TABLE` and `rate_for` / `cost_usd` (Tier 1 free by tier, unknown paid model at the most expensive known rate with one warning), `tier_for` (critical or cascade-off is Tier 2), `escalation_reason` (the post-call predicate: `no_answer`, `rejected`, `insufficient_information`, `proposed_write`), the `ESCALATE_*` codes | a new paid model (one row). **A different question than `agent.py`'s routing**, which is why the two do not share a name |
| `src/api/routes.py` | **M8.** `create_app` (the factory: lifespan builds the one engine, `RequestIdMiddleware`, CORS from `API_CORS_ORIGINS`, the four exception handlers that keep every error in the envelope), `approver` (the bearer-token dependency, `compare_digest`, the name is `decided_by`), the six routes, `tick_events` (the hand-rolled SSE generator: latest on connect, `Last-Event-ID`, `?limit`, a ping comment) | routes the window asks for; nothing that reaches the ranch or a model, and a rail greps for it |
| `src/api/schemas.py` | **M8.** `IncidentOut`, `ShiftReportOut`, `TickOut`, `PendingWriteOut`, `GateDecisionIn` (`extra="forbid"`, no `decided_by`), `GateDecisionOut`, `Meta`, `ErrorBody`, `AgentName` | fields the window needs |
| `tests/` | three files: `test_agent.py`, `test_tools.py`, `test_api.py` | |

Seven other leaves are docstring-only placeholders naming the milestone that fills them. A stub never claims to be implemented, and the marker column in `docs/Plan.md`'s tree is how you tell unbuilt from missing.

`data/knowledge_base/` holds **seven** files: `water.md` (`WATER-01` to `WATER-06`), `feed.md` (`FEED-01` to `FEED-04`), `infrastructure.md` (`INFRA-01` to `INFRA-06`), `sensors.md` (`SENSOR-01` to `SENSOR-05`), `wellhead.md` (`WELL-01` to `WELL-04`), `compliance.md` (`COMP-01` to `COMP-05`), and from M7A `herd.md` (`HERD-01` to `HERD-07`, the four animal categories). Infrastructure splits into three because a fence, a broken probe, and a gas wellhead are three unrelated bodies of knowledge, and `SOP_FOR_CATEGORY` is what maps each of the 22 categories onto one of them. **All six are derived from `docs/sweetwater-ranch.md`, one file per sensing world, and nothing else in this repo may source them.** A rule id is citable only if it is a heading in the file the packet carried, so an SOP file is the definition of what a work order is allowed to cite.

---

## Decisions already made. Do not re-litigate these.

1. **`sw_ops` exists and is migrated.** Schema plus an `incidents` table, SQLAlchemy async, asyncpg, `alembic_version` living inside `sw_ops` rather than `public`. One resolver picks the target for both `alembic` and `main.py`, because a migration applied to one database and a tick written to another presents as an empty ledger rather than as an error. **Any further Supabase migration is run only after asking Scott explicitly.**
2. **The sweep goes direct to `SENSOR_API` over httpx**, bounded by `SWEEP_CONCURRENCY`, because 160 reads per tick through a tool wrapper is pure overhead. The catalog comes from the `ranch://sensors/map` MCP resource, with `GET /sensors?limit=500` as fallback and second source of truth.
3. **Triage thresholds are derived from the ranch mission in `docs/sweetwater-ranch.md` and the observed distributions below.** Never lifted from the upstream service's source. See the boundary rule.
4. **Triage writes the human sentence in code.** Deterministic prose per finding, per the voice rules in `src/tools/CLAUDE.md`. A model never authors severity and never authors an all-clear.
5. **`herd_health` owns animals and nothing else.** Rewritten at M7A. The herd sweep's four categories (`deceased`, `inactive`, `observation_high`, `care_overdue`) route to it and no sensor category ever does, because it cannot read a sensor. A tick with a deceased animal on the Farm API that hands `herd_health` nothing is the failure now, and a rail says so.
6. **One bad read is `pending`; two in a row is an incident.** `INCIDENT_CONFIRM_SWEEPS`, default 2, migration `0003`, decided and migrated on Supabase 2026-09-10 with Scott's explicit yes. A pending row that reads clean is `dismissed`, never `resolved`. The reason is the M4 measurement: the Sensor API redraws every reading, so at 1 the loop paid for 10 to 24 work orders per tick about tanks that were never empty.
7. **A subject that did not answer resolves nothing.** `reconcile` takes the set of subjects that actually replied: the sensors the sweep read and, from M7A, the animals the herd list carried (`read_subject_ids`). A Care API outage resolves no cow. "No finding" and "no reading" are different facts, and conflating them lets one upstream outage close every incident and report an all-clear.
8. **An empty catalog is a failed tick, not a calm one.** Nothing is swept and nothing is reconciled, because resolving every incident on the strength of a map we could not read is the worst available outcome.
9. **Structured output is a forced tool call, not a "reply in JSON" instruction.** The schema is enforced by the API, and - the reason that actually matters - `finish_reason` stays honest: `tool_use` is a real answer, `max_tokens` is a config bug. With free-form JSON both arrive as text and the distinction is gone.
10. **Thinking is off for the work-order job, and `reasoning_effort` is an explicit per-call argument.** Turning thinking off is free exactly when the model is not the one classifying, and `triage.py` classified. Per-call rather than ambient so turning it on for one job later does not touch any other call site.
11. **A work order is never dropped.** A model that never answered, timed out, or got truncated still produces a `WorkOrder` with `status="no_answer"` and the reason in `assessment`. A tick that silently loses an incident is indistinguishable from a ranch with nothing wrong.
12. **A model never performs a write. From M6 it may propose one, and a human performs it.** `GATE_LANDED` (True since 2026-09-11, flipped last, after the pause and the audit stream were proven) made writes proposable, not callable: `assert_callable` still refuses any write without an `Approval`, which only `gate.py` mints after a human resumed the pause with `approve`. Still a boolean in one module and not a config value, because an env var is something somebody sets on a laptop at 11pm to make a demo work. The gate is for agent writes; `CHAOS_ALLOW_WRITES` is a different switch for a different actor and the two are not unified.
13. **Severity ownership extends to the shift report.** The supervisor may not restate a severity, and `linked` - its only causal claim - is checked in code against the incident keys the page actually carried. This is why `reasoning_effort` stays off even for fusion: the claim is verified rather than trusted.
14. **`ruff format` is deliberately not in the gate.** `E501` is ignored on purpose so a long line may stay long; the formatter hard-wraps at 140 with no escape hatch, so the two contradict. `ruff check` is the lint gate. Do not add the formatter back.
15. **The loop's spend ceiling halts; it never skips a tick and carries on.** `SPEND_CEILING_USD`, default $10, no unlimited value, exit **4**. Overshoot is one tick by design. Decided before the loop body was written, because M4 is the first phase where the money runs with nobody watching.
16. **Backoff is per upstream and the heartbeat never stops.** A tick in backoff writes its line naming who is sick. One dead service never stops the ranch watch, and the loop itself never sleeps past one cadence.
17. **A `no_answer` for a retriable reason is held and re-routed; a rail rejection never is.** The held set is in-process until a column on `incidents` gets an explicit yes.
18. **`cost_usd` is on the tick line from M4**, and from M7 it is computed per order at that order's model from `routing.PRICE_TABLE`, Tier 1 at $0.00. The M4 assumed rate ($15/$75) was **3x the Opus 5 list price** ($5/$25): every dollar figure quoted before M7 is three times the real bill and the tokens beside it were always the measurement. Bedrock's row is set at first-party parity and marked unverified.
19. **Shutdown is written for Windows.** No `add_signal_handler`; the Runner's Ctrl+C handling plus `signal.signal` for SIGTERM/SIGBREAK, and the in-flight tick drains.
20. **The gate is one LangGraph `interrupt()` graph per proposal, checkpointed in Postgres, and the tick is not a graph.** Scott's default was a pending-writes table; the library was chosen because learning it is part of the project's purpose, and per-proposal graphs keep both requirements: the pause outlives the process, and one waiting question does not stop the watch. Code owns the checks, the key, the audit lines, and the duplicate suppression. LangGraph holds the pause and decides nothing.
21. **The checkpointer is the sync `PostgresSaver` in a worker thread.** The async saver refuses Windows' default event loop, and changing the loop policy for one driver is a change every component and test inherits. Its tables are created by alembic (`0004`) from the library's own `MIGRATIONS` list, never by `setup()`, and `memory.checkpointer()` refuses a database behind the installed library rather than upgrading it silently.
22. **A proposal is checked in code before it may pause, and none of the three checks blocks the work order.** Shape (`WRITE_TOOL_ARGS`, read off the wire), tool (`proposable_tools_for`), grounding (every id and quantity on the page, numbers compared as numbers). The failed part is stripped and recorded; the prose ships. The incident key is code's and re-checked in `executor` as `write_key_unknown`.
23. **The same write for the same incident is asked once.** A held incident is re-judged every tick until answered, so `propose()` looks for an open pause on the same key and tool and suppresses the duplicate, with no audit line. Two ticks, one pending write, and a test says so.
24. **The gate never fails the tick.** A checkpointer that cannot be opened counts `writes_failed`, holds the incident with reason `gate_unavailable`, and the shift page ships. A pause that could not be written after its `proposed` line is completed with `decision="dropped"`, so a dangling `proposed` keeps its one meaning.
25. **The audit rail is twice, or once while pending.** `gate.unpaired_audit_ids` minus `gate.pending` is empty. Only lines whose `phase` is `proposed` or `decided` count; a console line that mentions an id is commentary.
26. **The held set is durable** (`0005`, `incidents.held_reason`). Written and cleared by `run_tick`, read by `run_loop` before its first tick. `docs/issues.md` #3 closed.
27. **The proposal field is described neutrally and the brief paragraph about it is rendered from the allowlist.** None is the usual answer and the description says so. Two paid ticks proposed nothing and the prompt was not steered to make the demo pause; that is the recorded result, not a defect.
28. **There are two model jobs, and "two or more worlds" gates only one of them.** The per-incident work order and the fused shift report. The plan's Tier-1 list had four jobs; chaos prose and shift-report assembly are code. The world count decides whether a tick needs someone reading across the ranch, which is the shift report, already Tier 2; it is **not** a per-incident trigger, because it says nothing about what one packet contains and would send every order on every storm tick to Opus. Scott's call, derived from the readings up in the M7 design conversation.
29. **Escalation is a rewrite from the identical page, never a review and never a retry at the same tier.** `routing.escalation_reason` reads the Tier-1 order after the rails: `no_answer`, `rejected`, `insufficient_information`, `proposed_write`. Critical is the one pre-call trigger. Opus's order is stored with `escalation=<reason>` and the Tier-1 attempt on it as `tier1_*`; a Tier-2 rejection is stored as rejected. Only a Tier-2 proposal may reach the gate.
30. **`TIER1_ENABLED` is a config knob, ships `False`, and stays so until a ledger row says the local model earned the job.** A cost and quality knob, not a safety one: the rails and the gate do not read it. Measured at M7 and not adopted; the row says why. `TIER_COMPARE` is the measurement mode and SPENDS at Tier-2 prices on every Tier-1 packet.
31. **A citation that carries the heading's title is trimmed to its id in code and recorded, not rejected.** `"FEED-02 - A bin at the warning line…"` on four of five local answers, id right every time. The `invented_rule` rail runs on the trimmed id and is unchanged; `rule_citation_trimmed` is a non-blocking note so a row can count it.
32. **An incident is about a subject, and a subject is a sensor or an animal** (migration `0006`, on Supabase 2026-09-11 with Scott's yes). `subject_id` / `subject_type`, where `subject_type` is one of the 13 sensor types or the literal `animal`; that literal is how the rows are told apart. A pure rename, no data rewrite. Chosen over writing cow ids into a column called `sensor_id`, which works today and lies to everyone who reads the table later.
33. **The herd catalog is the whole Farm list, paged in waves of 6, never the `status` filter and never the roster.** The filter is validated and still cannot find a non-active animal (cow-0905 deceased, `status=deceased` empty); the Farm API returns HTTP 500 on 2 of 12 pages at 12 or 20 in flight and 12 of 12 at 6; a deceased PATCH nulls `pastureId` so the roster loses her. The list is what vouches for an animal. About 18s a tick, measured.
34. **The herd stage never fails the tick.** A Farm or Care failure is `herd_error` on the line and an empty `answered` set: findings from what did come back still open, nothing about an animal resolves, and the tanks are still watched. An empty list is a failed stage, not an empty herd.
35. **Observations are read only for the changed set**: non-active status, pending care task, or a live incident. The one thing that leaves invisible is written down (`docs/issues.md` #16). A `high` observation opens only inside a 24-hour window, because the ranch has history and the debounce does nothing against a stable note.
36. **`sold` is not a finding, in code.** One non-active status is a ranch running normally.
37. **The one observation a model may propose is the flag itself** (`HERD-07`): a `general` note that the finding was made and a person sent, every word from the page, `observedAt` from the page. Opus proposed nothing on seven herd orders under `HERD-05` alone; it took `HERD-07` on the first tick it was offered. Decision 27 holds: the prompt was not steered, the SOP gained a legitimate case.

## The boundary rule, sharpened

`scott-jasper/mcp-farm` is the frozen upstream. A local clone exists at `C:\temp\MCP-Farm`. **Do not read it.** The 19 tools, the `ranch://sensors/map` resource, and the four REST APIs are the contract; that repo's source is not. Constants copied out of its internals are values nothing here can verify and that fail silently when they drift. Everything needed is reachable over the wire or already written down in this repo.

Learned the hard way on 2026-09-10, in this repo, by doing exactly that and retracting it. Full entry: `docs/cookbook.md` #5.

---

## Environment, verified 2026-09-10

- **Interpreter is `.venv/Scripts/python.exe`.** System `python` is 3.11.9 with none of the dependencies installed. Every command in the docs assumes the venv.
- **`.env` is filled in and working.** All five upstreams, both database URLs, chaos cohort. **It also has `CHAOS_ENABLED=1`**, which contradicts the line below saying it belongs at 0 and was the cause of `docs/cookbook.md` #26. The tests now disarm it themselves; a measurement run has to set `CHAOS_ENABLED=0` explicitly, as the M4 paid run did. Set it to 0 in `.env` when the demo is not being driven.
- **`ANTHROPIC_API_KEY` is still empty, and M4's paid run shipped on Bedrock anyway.** This machine authenticates to **AWS Bedrock** (`CLAUDE_CODE_USE_BEDROCK=1`, `us-east-1`, session-scoped temporary credentials), and `resolve_provider()` in `llm_client.py` picks first-party Anthropic when a key exists and Bedrock otherwise. Session credentials expire, so a loop meant to run for days needs a real key. From M4 an expiry mid-run is survivable: `ExpiredTokenException` becomes a `transport_error` no-answer, the incident is **held**, and the `model` upstream backs off; nothing is lost and nothing retries on a cadence against a dead credential.
- **`anthropic[bedrock]` is a first-order dependency now.** It was absent from `requirements.txt` and arrived transitively through `langchain-anthropic` without the extra, so the first live call failed on `No module named 'botocore'`. `llm_client.py` calls the raw SDK, not `ChatAnthropic`, because `langchain-anthropic` has no Bedrock path and M2 has no tool loop.
- **Ollama 0.33.2 is up** on `localhost:11434`, `gemma4:e4b` (8B, Q4_K_M) pulled, RTX 5070 with 12 GB. Measured at M7: 3,000 to 4,200 tokens in per packet against `OLLAMA_NUM_CTX=16384`, 4 to 13s a call warm, 22s cold; two concurrent calls overlap partly (8.8s wall for 11.7s of work). `keep_alive` is `30m` per call so the cadence does not unload the weights.
- **Supabase**: PostgreSQL 17.6, connects as `postgres`, can create schemas. **`sw_ops` now exists**, migrated to `0007` (2026-09-11), with `alembic_version` inside it. The connection also has write access to `farm`, `feed`, `animal_care`, and `sensor`, and must never use it. The engine pins `search_path` to the target schema, and a test fails if any SQL in this repo names a ranch schema.
- **Local Postgres**: 16.4, database **`farm_systems_test`**, connects as `postgres` with no password. It already holds the upstream project's `farm`, `feed`, `animal_care`, and `sensor` schemas, so **`sw_ops_test` is the only schema this repo may create or drop.** A two-value allowlist in `memory.py` refuses anything else, and the store tests skip rather than fail when no local Postgres answers. This is the exact accident that cost three answer keys in a previous life of the project.
- Both database URLs in `.env` use the bare `postgresql://` scheme. `config.py` upgrades them to `postgresql+asyncpg://` in a validator, since SQLAlchemy otherwise reaches for psycopg2, which is not installed. Supabase's pooler additionally needs `statement_cache_size=0`, set once in the engine factory with the reason in a comment. Do not clean either of those up.
- **`.env` has `OPS_API_TOKEN=scooter:m8-local-dev-token-change-me-0001`** from M8, a local development token. `--api` refuses to start without one. Replace it before any box other than this one serves the API.
- **`SW_OPS_TARGET`** picks the store: unset or `prod` is Supabase, `test` is the local `sw_ops_test`. Prod is the default on purpose, because a default that quietly writes somewhere harmless is a default that ships.

---

## The live ranch, measured over HTTP. Do not re-probe to learn this.

**160 sensors, 32 locations, 13 types.** Envelope is `{ "data": ..., "meta": { count, limit, offset } }`.

- `GET /sensors?limit=500` returns the whole catalog in one page. Pagination only, no `type` filter, so filtering happens in Python.
- `GET /sensors/:id` returns the catalog entry plus `latestReading: { value, recordedAt }`, **synthesized fresh on every call and unanchored to the previous one.** Never cache it, never expect two reads a second apart to agree.
- `GET /sensors/:id/readings?limit=N` returns invented history, newest first, one every ten minutes. **It is synthesized independently of `/sensors/:id` and does not contain the value the sweep read**, even when its newest point carries a later timestamp. Both are honest; they are two draws. The evidence packet therefore labels the series as shape and trend only.
- **`/animals` and `/pastures` are on the Farm API, not the Care API.** A wrong base URL 404s rather than naming itself.
- **Join across services on the id, never the display name.** The sensor map spells a location `"Alkali Flat (alkali-flat)"` where REST says `"Alkali Flat"`, and the Farm API's pasture for sensor location `"East Allotment"` is `"East BLM Allotment"`. A name match returns an **empty set, not an error**, which reads exactly like a pasture with no animals in it.
- `GET /health` on the Sensor API returns `{ status, service }`. There is no `/locations` route.
- A dark sensor returns `status: "offline"` **and** `latestReading: null`.
- A faulted probe returns **`-500`** with `status: "online"`. That is a sensor fault, not a cold snap, and must not be triaged as a temperature reading.
- `gate` values are **boolean**, not numeric. Every other type is a number.
- 160 bounded reads at concurrency 20 complete comfortably inside the tick budget, all 200s.

One sample per sensor from a single sweep on 2026-09-10, so treat these as indicative of the operating range, not as limits:

| Type | Unit | n | observed low | median | observed high |
| --- | --- | --- | --- | --- | --- |
| `water-level` | gallons | 28 | 0.3 | 17.9 | 27.4 |
| `temperature` | °F | 26 | -21.2 (plus one `-500` fault) | 48.2 | 101.1 |
| `humidity` | % | 26 | 5.1 | 42.8 | 97.1 |
| `gate` | open/closed | 24 | boolean, one offline with null | | |
| `soil-moisture` | % | 18 | 14.1 | 28.9 | 57.1 |
| `feed-bin-weight` | lbs | 12 | 363.2 | 1167.0 | 1727.6 |
| `fence-voltage` | kV | 8 | 0.4 | 6.1 | 9.9 |
| `battery-charge` | % | 8 | 11.7 | 82.1 | 99.3 |
| `fuel-level` | % | 2 | 4.9 | 42.6 | 80.3 |
| `wellhead-pressure` | psi | 2 | 189.5 | 310.0 | 430.4 |
| `stream-flow` | cfs | 2 | 2.4 | 7.2 | 11.9 |
| `wind-speed` | mph | 2 | 7.5 | 10.2 | 12.9 |
| `snow-depth` | in | 1 | 9.7 | | |

**The herd, read over HTTP on 2026-09-11 (M7A). Do not re-probe to learn this.**

- **1,195 head**: 1,025 cows, 156 sheep, 10 horses, 2 donkeys, 2 goats, across 18 pastures (3 empty). Every one `active` and every `updatedAt` the seed stamp `2026-08-10T14:00:00.000Z`. The chaos cohort cow-0901 to 0905 is real and started in `sweetwater-bottoms`.
- **`GET /animals` runs at ~90 ms a row.** `limit=50` 4.6s, `100` 9s, `200` 17s, **`500` is a 503 at the API Gateway 30s wall, every time**. The maximum accepted `limit` is 500 (422 above).
- **The Farm API fails under concurrency.** 12 pages at 12 or 20 in flight: 2 of 12 HTTP 500, five runs of five. At 6 in flight: 12 of 12, every time, 18s wall. `HERD_PAGE_CONCURRENCY = 6`.
- **The `status` filter cannot find a non-active animal.** It is validated (422 names the enum), `status=active` excludes a deceased cow, and `status=deceased` returned **empty** with cow-0905 deceased in the unfiltered list. `pastureId` is honoured; there is no pastureless filter (`pastureId=null` and friends return empty). `species` is honoured.
- **A deceased PATCH nulls `pastureId`.** The dead cow leaves every pasture roster. `chaos restore` puts the status back and leaves her pastureless (`docs/issues.md` #17).
- `GET /pastures?limit=50` returns all 18 pastures with `animalIds` inline, 1,195 ids, in 1.2s. Context and a second source, not the catalog.
- **Observations list per animal only.** `GET /observations` is a 404; `GET /animals/:id/observations` takes `limit` / `offset` and silently ignores `since`, `from`, `observedAfter`, `after`, `start`, and `severity`. The MCP `list_observations` requires `animalId`. ~0.35s a read, clean at 6 concurrent.
- **Care tasks are `GET /care-tasks`** (not `/tasks`), `status` and `animalId` honoured and validated. Five exist, all `pending`, three overdue on 2026-09-11 (cow-0777 since 08-10, cow-0512 since 08-18, sheep-0001 since 09-05). Their titles carry em dashes (#19).
- **The ranch has history.** cow-0777 carries a `high` `mobility` note from 2026-08-09; cow-0001 carries four notes, the newest 2026-09-09 saying "Overdue for contact per SOP-02", so something else writes into this Care API too.

The seven sensors that misbehave on purpose are listed at the bottom of `docs/sweetwater-ranch.md`. They read badly on every call, which makes them the only findings guaranteed to persist across ticks. Healthy sensors also draw extreme values a fraction of the time, so expect findings to churn tick to tick. M1 measured **18 to 23 per tick with chaos off**, which supersedes the 10-to-20 estimated here before the sweep existed. That churn is the feed working, not a bug to suppress.

---

## What M1 actually produces, so a new session does not re-derive it

The free pass is live and token-free. Numbers from the two verification runs on 2026-09-10, against the live ranch with chaos off:

- **160 sensors read, 0 failed**, both runs, catalog from the MCP resource.
- **18 to 23 findings per tick.** Run 1 opened 18. Run 2 showed **opened 15 / ongoing 8 / resolved 10**, which is the churn `docs/sweetwater-ranch.md` predicts and the proof that `reconcile` works.
- Routed to `infrastructure` and `water_feed`, with `compliance` appearing on run 2. `herd_health` and `chaos` get nothing yet, by design.
- Triage covers **13 sensor types across 18 categories**, every critical band with a warning tier under it, and a per-type physical-plausibility window that catches the `-500` sentinel as a fault rather than as a cold snap.
- Incident key is `sensor:category`. A partial unique index enforces one live incident per key while keeping the history of the resolved ones.

---

## What M2 actually produces, so a new session does not re-derive it

The tick now has seven stages. Five are free; the last two spend. `run_tick(spend=False)` stops at the end of `route`, which is how every rail above the model layer exercises the whole pipeline for nothing, and `conftest.no_model_calls` is the autouse guard behind the flag.

Per newly-opened incident that `water_feed` owns:

1. **`evidence.assemble`** buys roughly **four extra HTTP calls** - that sensor's history, its siblings at the same location, the pasture, the animal roster - and renders one page. Bounded by `SWEEP_CONCURRENCY`, invisible next to a 160-sensor sweep.
2. **`call_tier2`** sends that page plus the brief in **one** call, structured output as a **forced tool call** (`write_work_order`), thinking **off**. Measured **~4,800 to 5,600 in / ~1,100 out**, 14 to 16s, 4 in flight (`AGENT_CONCURRENCY = 4`).
3. **`workers.check`** runs the rails and stores a `WorkOrder`.

**The cost lever is the SOP file, not the evidence.** The SOP is the majority of those input tokens; the assembled facts are a few hundred. Worth knowing before M7 optimizes the wrong half.

Three live ticks on 2026-09-10, chaos off, against prod `sw_ops`:

| | tick 1 | tick 2 | tick 3 |
| --- | --- | --- | --- |
| packets judged | 9 | 6 | 4 |
| tokens (in + out) | 58,339 | 38,328 | 24,161 |
| ledger rows after | 25 | ~45 | 65 |

**19 work orders, 19 shipped, 0 rejected, 0 no-answer, every call `finish_reason=tool_use`.** Cost tracks **newly-opened** incidents, not open ones, which is the architecture's central claim arriving as a measurement. Rule citations discriminate rather than pattern-match: `WATER-05` on 12 of 19, but `east-allotment-water` cited only `WATER-02`.

**Severity is stored from the incident, never from the model's echo.** The echo lives in its own field so a disagreement is recorded rather than smoothed over, and a mismatch is a blocking violation. Five rails block (`severity_mismatch`, `all_clear`, `invented_rule`, `no_payload`, `schema_invalid`); two record and still ship (`no_rule_cited`, `sensor_not_named`). The all-clear rail reads the **actions list**, not the prose, because "the second tank at 16.7 gal is fine" is a correct sentence and the actual diagnosis. Details in `src/agent/CLAUDE.md`.

**There is no cascade, no local fallback, and no retry.** Tier 1 is absent rather than stubbed. M7 moves jobs down one at a time with a rail and a row in `docs/model-routing.md`, whose first row is now the Tier 2 baseline to beat.

---

## What M3 actually produces, so a new session does not re-derive it

The tick has **nine** stages now. Six are free, two spend per newly-opened incident, and the last one decides for itself.

**The tool slices are enforced in code, and the counts are the spec: 7 / 6 / 5 / 5 / 0.** A test asserts each one exactly, so a slice cannot grow by one tool without a deliberate edit to a number a human reads. Three agents legitimately share the three sensor read tools and that is **not** carved up: the isolation that matters is the brief, the SOP set, and which sensor types reach each agent, and carving it further would mean editing a frozen server. `herd_health` has no sensor reads at all, by design rather than omission.

**No write tool reaches a model before M6.** Three functions, and they are not redundant:

| | |
| --- | --- |
| `tools_for(agent)` | the declaration. Includes the writes, so the asserted counts are real counts |
| `bound_tools_for(agent)` | what a model would be handed. Subtracts `WRITE_TOOLS` while `GATE_LANDED` is False |
| `assert_callable(tool)` | the runtime guard in `mcp_client.call_tool`. Raises `WriteGateError`. This one covers **us**, not the model |

`WRITE_TOOLS` names all **eight** deployed writes, not the four that appear in a slice. `assign_to_pasture`, `remove_from_pasture`, `assign_to_shelter`, and `remove_from_shelter` are in no slice and belong in none, which is exactly why they are named: those are the ones somebody adds later while chasing one read out of the same API.

**`DEPLOYED_TOOLS` is all 19, read off the wire and not out of the upstream's source.** `--handshake` compares the deployed list against it and fails on drift in either direction. That check lives in the handshake rather than in `pytest`, because `pytest` has to pass on a plane.

The fan-out and the shift report:

- **`AGENT_CONCURRENCY = 4` is a global ceiling on Opus calls in flight.** `fan_out` builds one semaphore and hands the same object into every agent. Four agents each bounding themselves at four would be sixteen.
- **An agent failing is not the tick failing**, at both levels: a packet raising inside an agent, and the whole agent raising inside the fan-out. Either way every incident it carried comes back as a `no_answer` order.
- **`synthesize` runs on every tick and spends on almost none.** It calls a model only when `spend` is true *and* `FUSION_THRESHOLD = 2` or more sensing worlds opened incidents. Below that it assembles the page in code for free, and the same `assemble_shift_report` is both the calm-tick answer and the failure fallback, deliberately. A rejected report is **replaced**, carrying its violations, never retried.
- Two rails on the report, both blocking: `invented_incident` (`linked` against the keys the page carried) and `all_clear` (priorities and headline, never the `situation` prose).

Two live `--once` runs on 2026-09-10, against a ledger that had just been refilled, so both are unusually expensive:

| | tick A | tick B |
| --- | --- | --- |
| newly-opened, so calls | 15 across 3 worlds | 14 across 3 worlds |
| tokens (in + out) | 104,500 + 15,308 | 91,468 + 15,545 |
| wall clock | 85 s | 96 s |
| rail failures | 0 of 16 | 0 of 15 |

**29 work orders, 29 shipped, 0 rejected, 0 no-answer.** Per-call cost is unchanged from M2, so fan-out multiplies calls and not price. The supervisor is about 13% of the bill for one call against fourteen. **A calm tick is $0.00 exactly.** Do not read either tick as a steady-state budget; M4 measures that. Full numbers and the assumed rate: `docs/model-routing.md`.

**`herd_health` was handed nothing on both ticks and logged nothing about it.** Decision 5 working. A warning per tick per idle agent trains everyone to ignore the log.

**The rails cannot detect a missing brief, and that is measured rather than suspected.** `docs/no-brief-transcript.md` and `docs/with-brief-transcript.md` are the same model on the same packet with `COMPLIANCE_MANDATE` removed, and the unbriefed answer **passes every rail with zero violations** while naming no neighbour at all. Every rail asks whether an answer is defensible about its own incident; scope is not answerable from inside one work order. Both are pinned as fixtures. Do not turn any of it into a blocking rail: a rail that counts actions gets satisfied by padding.

---

## What M4 actually produces, so a new session does not re-derive it

`python main.py` is the loop. `python main.py --no-spend` is the loop with the two paid stages
off, which is how the mechanics are exercised for free. Exit codes: 0 clean drain, 1 forced or
broken, 2 config, 3 not built, **4 spend ceiling.** Knobs, all in `config.py` with the reasons:

| Knob | Default | What it does |
| --- | --- | --- |
| `TICK_INTERVAL_SECONDS` | 300 | start-to-start cadence. Chaos TTLs are in ticks and multiply by this at injection, so the two rescale together |
| `SPEND_CEILING_USD` | 10.00 | per-run ceiling, summed from `cost_usd` on the tick lines, checked after every tick, **halts with exit 4.** Must be positive; no unlimited value. Not applied to `--once` |
| `BACKOFF_BASE_SECONDS` / `BACKOFF_CAP_SECONDS` | 60 / 900 | per-upstream exponential window (`mcp`, `sensor`, `sw_ops`, `evidence`, `model`). The loop itself never sleeps past one cadence |
| `LOG_ROTATE_BYTES` | 10 MB | size cap on `tick.jsonl` and `agent.jsonl`; set small to force a rotation inside a verification run |

**What the live runs measured, 2026-09-10.** The free run: 30 ticks in 30 minutes at 60s, a
healthy free tick is **3.0 to 3.6s**, and a tick against a dead Sensor API fails in 18s with
`all 160 reads failed`. Backoff went 60s then 120s, ticks 12, 14, and 15 were skipped and each
wrote a line, tick 16 recovered on its own. One `chaos_event_missed`, real: a 4-tick stream fault
injected seconds before the outage healed during it. Two rotations at 8KB, one `run_id` across
three files. `CTRL_BREAK_EVENT` during the cadence sleep stopped it in the same millisecond, exit
0. The paid run, prod ledger, chaos off, 120s: tick 1 opened 12 and cost **$2.16** in 80s, tick 2
opened 14 and cost **$2.54** in 90s, 26/26 shipped, both reports fused, then **exit 4 at $4.70
against a $3.00 ceiling.**

**The finding that matters most, and what was done about it the same day: on this ranch every tick was a storm tick.**
The prediction in `docs/model-routing.md` was that a steady-state loop opens a handful of
incidents per tick. It opened 10 to 24 on every one of 30 free ticks and 12 then 14 on the paid
ones, because the deployed Sensor API synthesizes a fresh, unanchored reading on every call and
healthy sensors draw extremes a fraction of the time. At 300s that is roughly **$28 an hour.** The
levers are a debounce (open only on two consecutive bad sweeps, which the seven permanently-bad
sensors pass and a one-draw extreme does not) or M7's cascade. **The debounce shipped the same
day** as `INCIDENT_CONFIRM_SWEEPS = 2` and migration `0003` (decision 6); the live check is in
`docs/JOURNEY.md` under the M4 addendum. M7 is next.

**The held set and the miss check are in-process.** Both live for the run and die with it. A held
incident survives a restart only as `ongoing`; a fault injected by a previous run is never this
run's miss to report. Both durable forms are columns, and columns are a Supabase migration.

**Nothing in the tick reads the Care API.** Chaos's animal events are real writes, and the loop
fires them when armed and permitted, but no stage produces an animal finding, so `herd_health`
is handed nothing and the coyote verification cannot run in any tick yet. Its own item.

---

## What M6 actually produces, so a new session does not re-derive it

The tick has **ten** stages. `gate` sits between `fan_out` and `synthesize`, costs no tokens, and
never fails the tick. A work order may carry `proposed_write` (`{"tool", "args"}`, `tool` empty
meaning none); `workers.check_write_proposal` runs shape, tool, and grounding on it; a survivor is
handed to `gate.propose`, which writes the `proposed` audit line, then pauses one LangGraph
`interrupt()` graph in the checkpointer's tables in `sw_ops`. The tick moves on. The four fields on
the line are `writes_proposed`, `writes_duplicate`, `writes_failed`, and `writes_pending` (`null`
when the gate was never opened).

The human half, the same shape as the chaos CLI. `SW_OPS_TARGET` picks the ledger as everywhere:

```bash
python -m src.agent.gate list                                  # every proposal still waiting, oldest first
python -m src.agent.gate approve <audit_id> --by scooter       # performs the write over MCP with an Approval; exit 1 if the ranch refused it
python -m src.agent.gate reject  <audit_id> --by scooter --reason "tank 2 is fine"   # --reason is required
```

`approve` and `reject` both write the `decided` line with `decision`, `decided_by`, `result`,
`latency_to_decision_ms`, `reason`, and `upstream`. A second answer on a decided id is refused
naming the first. `write_paused` in the console stream, with the audit id and the CLI hint, is the
line that says a human is needed.

**What the live checks measured, 2026-09-11.** Planted on `sw_ops_test`: two pauses, the loop
killed with `CTRL_BREAK_EVENT` mid-pause, exit 0, both pauses listed by a new process with one
audit line each, reject then approve landing their `decided` lines (72s and 74s to decision), a
second answer refused. The pre-flip approve came back `transport_McpUnavailableError` because
`ranch_session` wrapped the belt's `WriteGateError`; `perform_write` now checks the belt before it
opens a session. Paid, on the prod ledger after the Supabase migration:

| | attempt 1 | attempt 2 |
| --- | --- | --- |
| opened / pending / dismissed | 1 / 12 / 20 | 2 / 17 / 10 |
| routed | `infrastructure` 1 | `water_feed` 2, both `feed_low` |
| shipped / violations | 1/1, 0 | 2/2, 0 |
| cost | $0.17 | $0.32 |
| `writes_proposed` | 0 | 0 |

**No live tick proposed a write.** `water_feed` had `restock_feed` and `consume_feed` in its brief
with their arguments and returned `tool: ""` on both feed-low packets. The prompt was not steered.
The channel is verified (the schema is accepted, three real answers parsed with the field present,
the fields are on the line); the live pause on a real proposal is the piece still owed, and the
`create_observation` pause specifically waits on the coyote gap.

**Two things a new session will trip on.** `AsyncPostgresSaver` does not run on Windows' default
event loop; use `memory.checkpointer()`, which is the sync saver in a thread. And never call
`saver.setup()`: the checkpointer's tables are alembic's (`0004`), and a newer library is a new
revision, which `memory.checkpointer()` will tell you by refusing to open.

---

## What M7 actually produces, so a new session does not re-derive it

The cascade exists and ships off. `TIER1_ENABLED=1` turns it on; `TIER_COMPARE=1` additionally
shadows every Tier-1 packet with Opus on the identical page and writes the pair to
`logs/compare.jsonl`. Three measured ticks with both on, test ledger, 2026-09-11:

| | tick A | tick B | tick C |
| --- | --- | --- | --- |
| opened (critical) | 9 (4) | 1 (0) | 2 (2) |
| Tier-1 candidates -> stayed local | 5 -> 1 | 1 -> 0 | 0 -> 0 |
| `escalation_reasons` | critical x4, insufficient_information x4 | insufficient_information | critical x2 |
| shipped / rejected / all-clears | 9 / 0 / 0 | 1 / 0 / 0 | 2 / 0 / 0 |
| wall clock, `cost_usd` | 77s, $0.52 | 23s, $0.05 | 36s, $0.16 |

**The local model was never unsafe and it saved one call in twelve.** The rails did not fire on it
once. It escalated itself on `insufficient_information` on 5 of 6 candidates, honestly: the SOPs
ask for facts (the forecast, how long a sensor has been dark) the packet does not carry. On the six
side-by-side pairs it was thin about the two things on the page a rancher reads the order for: the
flagged neighbour (1 of 3, Opus 3 of 3) and the head count (1 of 4, Opus 4 of 4). Full pairs:
`docs/m7-compare-transcript.md`. The close-out tick added 4 candidates, 2 local, 2 escalated. The row is in
`docs/model-routing.md` and it says **no move**.

**What a next attempt needs first**: the forecast on the feed page (`docs/issues.md` #12). Then the
same three ticks, the same columns.

**Three traps a new session will hit.** `AGENT_CONCURRENCY = 4` is still the ceiling for both tiers
and Ollama serves roughly two at once, so a 14-incident local tick is about 60s of local calls, not
15s. The `THINKING_BUDGET` path (`reasoning_effort` other than `"none"`) sends `budget_tokens`,
which Claude Opus 5 rejects with a 400; nothing uses it and it is `docs/issues.md` #13. And
`logs/compare.jsonl` carries two work orders of model prose per line, on purpose and only when
`TIER_COMPARE` is on; it is not one of the three operational streams.

---

## What M7A actually produces, so a new session does not re-derive it

The tick has **eleven** stages: `herd` sits between `sweep` and `triage`, costs no tokens, about 18s
and 20 requests a tick against the sensor sweep's 3s and 160, and **never fails the tick**. On a healthy
tick the line reads `herd_animals=6 herd_errors=0 herd_error=null` (the changed set: the three
care-task animals, cow-0001 and bull-01 by task, plus whatever is non-active or watched). A Farm or Care
outage reads `herd_error="farm: page:300 http_error HTTP 500"` and no animal answers.

Per animal in the changed set, triage decides in code: `deceased` critical, `inactive` warning, `sold`
nothing, a `high` observation inside 24 hours critical for `injury` / `mobility` and warning otherwise
(never on an animal that already has a status finding), `care_overdue` warning. Key is `animal:category`.
The packet is the record, its pasture, its last 10 observations, its open care tasks, its herd-mates
whose state changed, and **no sensor reading**, built from the sweep with zero HTTP.

**What the live run measured, 2026-09-11, test ledger, $3.57 over ten ticks.** The kill on cow-0905
pending on the tick after the PATCH and open on the one after, routed to `herd_health` alone. Ten herd
orders, nine shipped, every one naming the animal by id and tag and quoting the coyote note verbatim,
the deceased ones escalating to the GM; 6,800 to 7,000 tokens in, about $0.06 each. `proposed_write`
null on the first seven (the SOP forbade fabricating a note); `HERD-07` added; the next herd order
proposed `create_observation` with a note built from the page and paused: audit
`968e7f64fb7540dc9adeed547d15e802`, approved at the CLI by scooter in 49s, landed upstream as
observation `2dd60c7d-b9a2-413d-b17c-3cae46563d64`. Tick H fused three worlds, `linked` naming eleven
keys and joining `cow-0777:care_overdue` to the Red Canyon water run. After `chaos restore` the
deceased incident resolved on the next tick through the list, and `cow-0905:observation_high` went
pending because the kill's note is inside the window (#17).

**Three things a new session will trip on.** `pytest` **drops `sw_ops_test`**, so a demo on the test
ledger and a pytest run cannot interleave (cookbook #41). The three care_overdue incidents open on
every fresh ledger's second tick and stay `ongoing`; that is the ranch, not a defect. And `HERD-07`
makes `herd_health` propose a note on every `deceased` and critical `observation_high`, so a kill on
the cohort is a pause waiting at `python -m src.agent.gate list` until somebody answers it.

---

## What M8 actually produces, so a new session does not re-derive it

`python main.py --api` is a **second process** that reads `sw_ops` and nothing else. It binds
`API_HOST:API_PORT` (default `127.0.0.1:8000`), honours `SW_OPS_TARGET` like everything else, needs
`OPS_API_TOKEN` (`name:secret[,name:secret]`, 16-character floor) or exits 2 before binding, and never
calls the ranch or a model: a rail greps `src/api/` for the client modules. Six routes, all in the
ranch's `{data, meta}` envelope with `{error: {code, message, details}}` on every failure and
`X-Request-ID` echoed or minted on every response including the 500:

| Route | What it reads | Notes |
| --- | --- | --- |
| `GET /health` | nothing | never touches the database, by design and by comment |
| `GET /ops/incidents` | `incidents` | newest first by `last_seen_at`; `limit` 1..500 default 100, `offset`; filters `status`, `owner`, `subject_type`; a bad value is 422 naming the field; `meta.count` is what came back |
| `GET /ops/report` | `shift_reports` | the latest row, or 404 `REPORT_NOT_FOUND` |
| `GET /ops/stream` | `ticks` | SSE, `event: tick`, `id` = row id, `data` = the row with the whole tick line in `fields`. Latest row on connect, `Last-Event-ID` resumes after it, `?limit=N` closes after N, a `: ping` comment every 15s idle, polls every `API_STREAM_POLL_SECONDS` (2) |
| `GET /ops/gate` | the checkpointer | the CLI's `list` in an envelope |
| `POST /ops/gate` | the checkpointer | bearer token required (401 otherwise, nothing changed); body `{audit_id, decision, reason}`, `decided_by` is the token's name; 404 `GATE_NOT_FOUND`, 409 `GATE_ALREADY_DECIDED`, 422 on a bare reject; performs the write through `gate.decide` exactly as the CLI does |

**What the loop writes for it, from M8.** At the end of every tick, after `log_tick` and never failing
the tick: a `ticks` row (the line as `fields` jsonb, unique on `(run_id, tick)`) and, when the tick has a
report, a `shift_reports` row. A skipped tick whose sick upstream is the ledger itself writes no row. The
gate writes `audit_receipts` rows before its file lines through the checkpointer's connection; the
primary key `(audit_id, phase)` is the "exactly twice" rail as a constraint. `logs/audit.jsonl` is a
projection from M8 (#10 closed); the chaos guard's pairs are still file-only (#20).

**Two things a new session will trip on.** Every test in a process shares one `run_id`, so any rail that
runs `run_tick` on the test ledger needs `ticks` and `shift_reports` truncated first (`target` does it).
And `curl` against `/ops/stream` needs `-N` and a `?limit`, or it sits there forever looking hung, which
is the stream doing its job.

---

## M5 is landed. What a session needs to know before touching chaos

**`CHAOS_ENABLED` defaults to `0` and should stay `0` unless you are driving a demo.** With it
off, `sweep()` costs one boolean and opens no connection, so a tick is byte-for-byte what it
was before M5. An overlay firing underneath a cost measurement turns the numbers into noise,
which is why it shipped off.

| Knob | Default | What it does |
| --- | --- | --- |
| `CHAOS_ENABLED` | `0` | the master switch. Off means the overlay is never read |
| `CHAOS_SEED` | `1` | same seed, same demo, forever. `plan()` is pure |
| `CHAOS_ALLOW_WRITES` | `0` | the only thing standing between a scenario and a real `PATCH` on the deployed Farm API |
| `CHAOS_ANIMAL_COHORT` | five `cow-090x` ids | every animal mutation is confined to this set. An empty cohort means no animal event can fire at all |
| `CHAOS_MAX_ACTIVE` | `6` | the ceiling. A correlated group that would cross it is deferred **whole**, never half-fired |

Two injection paths, and the asymmetry is deliberate: **sensor faults are a lie this repo
tells over a truthful upstream**, applied in `sweep()` and nowhere else; **animal events are
real writes** through two different APIs, because `herd_health` has to find them with its own
tools. Detail in `src/tools/CLAUDE.md`, including the two-API table that M5 got wrong first.

Driving a demo. **The store target is explicit on purpose**: `resolve_store` defaults to
prod, which is right for a tick and wrong for a copy-pasted experiment.

```bash
export SW_OPS_TARGET=test CHAOS_ENABLED=1        # drop SW_OPS_TARGET when you mean prod
python -m src.tools.chaos status                 # what is armed, and how far through its TTL
python -m src.tools.chaos plan --ticks 6         # what this seed will do, against the live catalog, touching nothing
python -m src.tools.chaos inject --tick 2        # arm tick 2's events, and heal anything expired
python -m src.tools.chaos expire                 # heal everything whose TTL is up
python -m src.tools.chaos restore                # put the cohort back to `active`. Needs CHAOS_ALLOW_WRITES=1
```

`restore` **exits 1 when it restored nothing**, which is what a guarded refusal looks like from
a shell: the command did not do its job, and a script chaining off it should stop. The other
four exit 0 whether or not they had anything to do, because "nothing was due" is a successful
answer. `plan` reads the live catalog and touches no database at all.

**A replay is not a re-run.** Event ids are derived from seed, tick, and index, so injecting
the same seed twice inserts nothing the second time. Re-driving a demo from the top means
truncating `chaos_events` first. `docs/cookbook.md` #19.

**Two sessions cannot run `pytest` at the same time.** `conftest.py` drops `sw_ops_test`
`CASCADE` and re-migrates from its own worktree's `alembic/`, and the local Postgres is
shared. This actually happened twice during M5's phase close and looked exactly like a
migration rolling itself back. `docs/cookbook.md` #18.

**There is one known orphan on the deployed Care API**: observation
`0328d7e2-d271-4410-907c-a84020c2c8c7` against the nonexistent animal `zz-does-not-exist-0000`,
created by a contract probe that returned 201 instead of the expected 422. Observations are
append-only upstream, so it cannot be deleted from this repo. It is invisible to every real
animal. Written down so it is a known artifact, not a mystery.

---

## Working preferences that cost context to rediscover

Short answers. No recap tables of things already agreed. Do not read whole files when a targeted search answers the question. Do not dump raw probe output into the transcript, summarize it. Batch independent reads and commands into one round.
