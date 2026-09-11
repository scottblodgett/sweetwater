# STATE

The session-start briefing. **Read this one file, then the nested `CLAUDE.md` for the package you are about to touch, then stop and ask.** It exists so a new session does not have to read the doc set, probe the live ranch, or re-derive a decision that is already made.

Refreshed at every milestone boundary, same step as `JOURNEY.md`. If it disagrees with the code, the code wins and this file is stale, say so.

**Last refreshed:** 2026-09-11, in the post-M9 review that consolidated the docs (`docs/open-issues.md` #24). The M phases are closed. M3 and M5 were built in parallel sessions on one `master` and M5 closed first, so the phase numbers are not the commit order; `git log` is the authority on what landed when.

---

## Where the build stands

| | |
| --- | --- |
| Done | **M0** tree, three log streams, live handshake. **M1** the free pass (`catalog -> sweep -> triage -> reconcile -> route`), the `sw_ops` store, Alembic. **M2** the first phase that spends: `evidence.py`, `llm_client.py`, one agent writing a real work order per newly-opened incident. **M3** the four responders, the allowlists, the briefs, the bounded fan-out, the supervisor's shift report. **M5** chaos: the seeded overlay, the guarded animal write path, the CLI. **M4** the continuous loop: cadence, spend ceiling (exit 4), per-upstream backoff, the held set, Windows shutdown. **The debounce** (`INCIDENT_CONFIRM_SWEEPS`, migration `0003`). **M6** the gate: `proposed_write`, three code checks, one LangGraph `interrupt()` per proposal checkpointed in Postgres (`0004`), the held column (`0005`), `GATE_LANDED` flipped last. **M7** model routing: the price table, the cascade on Ollama, measured and **shipped off**. **M7A** the herd sweep: `herd.py`, four animal categories, `subject_id` / `subject_type` (`0006`), `herd.md`, the coyote gap closed with one live `create_observation` pause. **M8** the read API: six routes, `ticks` / `shift_reports` / `audit_receipts` (`0007`), `OPS_API_TOKEN`. **M9** the window: `web/`, the server-side proxy, five panels and a map placeholder, verified from a browser on three planted pauses. Full account per phase: `docs/JOURNEY.md`. |
| Gate | **433 tests**, `ruff check .` clean, `mypy` clean on 31 files, one alembic head at `0007` (applied to Supabase 2026-09-11 with Scott's yes). `web/`: `tsc --noEmit` clean, `eslint` zero problems, `next build` clean. Every command in root `CLAUDE.md` re-run on the test ledger in the post-M9 review: all green. One warning in `pytest`: `langgraph` and `langgraph-checkpoint-postgres` versions marked incompatible (`docs/open-issues.md` #27). |
| Next | **The M phases are closed.** Everything open, with what each would take and who decides, is `docs/open-issues.md`. In order of leverage: **#12** the forecast on the feed page, then the cascade re-measure it unblocks; the decisions waiting on Scott (**#5** where the loop and API run, which unblocks #23 and FUTURE-1; #6 to #8; **#21** the catalog snapshot migration with its DDL written); the small fixes ready to pick up (#22, #20, #4, #18). |
| HEAD | The post-M9 review on `master`. A hash here is stale by one commit by construction, so trust `git log` over this cell. |

`docs/Plan.md` is the layout, the architecture, the log schemas, and the milestone order, and the tree in it is the authority the repo matches. What happened and what diverged: `docs/JOURNEY.md`. Phase-close ritual: root `CLAUDE.md`.

---

## Where the code lives

`docs/Plan.md`'s tree is the authoritative layout, and the code was conformed to it rather than the reverse. There is **no `tick.py` and no `src/agent/routing.py`**; a search for either finds nothing, and that is the current state, not a missing file.

| Module | Holds | Grows |
| --- | --- | --- |
| `src/agent/executor.py` | `run_tick` (eleven stages, the `spend` flag, `held` / `skip` / chaos inputs, the one `tick.jsonl` line), `_gate_step` (the key check, the hand-off to the gate, `writes_*`), `_record_tick` (the `ticks` and `shift_reports` rows, M8), `_write_held` / `restore_held`, `run_loop` (cadence, ceiling, shutdown, the held restore), `Backoff`, the exit codes, `STAGE_UPSTREAM`, `RETRIABLE_VIOLATIONS` | nothing structural. The tick is deliberately **not** a graph: one waiting proposal must not stop the watch, and `interrupt()` blocks the graph it is in |
| `src/agent/gate.py` | **M6.** `GateState`, `build_gate` (ask -> execute, `interrupt()` in `ask`), `propose` / `pending` / `decide`, `perform_write` (the one path that performs an agent's write), `unpaired_audit_ids` (the rail), and the CLI `python -m src.agent.gate list / approve / reject`. **M8**: `record_receipt` (the row before the line, strict on `proposed`), `GateNotFound` / `GateAlreadyDecided` / `GateInvalidDecision` | nothing structural; `/ops/gate` wraps `pending` and `decide` and adds nothing. `docs/open-issues.md` #22 is ten lines here |
| `src/tools/herd.py` | **M7A.** `sweep_herd` (the roster, then the Farm list in waves of `HERD_PAGE_CONCURRENCY = 6` stopping at the first short page, the pending care tasks, observations for the changed set), `HerdSweepResult` (`animals`, `observations`, `care_tasks`, `answered`, `failure`), `PastureRoster` / `PastureContext` / `parse_pastures` / `slugify` (moved here from `evidence.py`, re-exported there), `parse_timestamp` | nothing structural. A pastureless-animal filter or a ranch-wide observation read upstream would each remove a wave |
| `src/agent/agent.py` | `ROUTES` (all 22 categories to an owner), `AGENTS` / `RESPONDERS`, and the supervisor's own stage: `render_shift_page`, `assemble_shift_report`, `check_shift_report`, `synthesize` | nothing structural |
| `src/agent/workers.py` | the rails (`check`, `check_write_proposal` with the three write checks, `normalize_citations`), `to_work_order`, `judge_packet` (the cascade lives here), `run_agent` for any of the four, `fan_out` under one shared semaphore | nothing structural |
| `src/tools/allowlists.py` | `DEPLOYED_TOOLS` (all 19), `WRITE_TOOLS` (all 8), `WRITE_TOOL_ARGS` (per-argument kinds, read off the wire 2026-09-11), `SLICES`, `tools_for` / `bound_tools_for` / `proposable_tools_for` / `assert_callable(approval=)`, `Approval`, and `GATE_LANDED` (**True since M6**) | a new deployed tool, which is a conversation with the upstream first |
| `src/agent/state.py` | `RanchState`, `Finding`, `Incident`, `WorkOrder`, `ShiftReport` | |
| `src/agent/memory.py` | `sw_ops` only: reconcile (`read_subject_ids` from M7A), the engine allowlist, the chaos-event store, the held column (`held_incident_keys`, `record_held`), `live_animal_subjects` (M7A, the herd sweep's watch list), `insert_tick` / `insert_shift_report` (M8), and the checkpointer (`checkpointer()`, `ThreadedPostgresSaver`, `assert_checkpointer_migrated`, `psycopg_url`) | nothing structural |
| `src/tools/chaos.py` | the scenario catalog, the pure seeded `plan()`, `inject_for_tick`, `apply_overlay`, the guarded animal write path, and the CLI | more scenarios; nothing structural |
| `src/tools/evidence.py` | `assemble` (a sensor page or, from M7A, an animal page built from `HerdSweepResult` with zero HTTP), `EvidencePacket.render`, `AnimalContext`, `SOP_FOR_CATEGORY` covering all 22 categories | `docs/open-issues.md` #12, the weather on the feed page |
| `src/models/llm_client.py` | `resolve_provider`, `build_client`, `call_tier2`, `ModelResponse` (with `tier` and `.cost_usd`), `THINKING_BUDGET`; **M7**: `build_tier1_client`, `call_tier1`, `Tier1Unavailable`, `OLLAMA_KEEP_ALIVE`. `cost_usd` is re-exported from `routing` | a third provider, which widens `ok` / `truncated` and the price table together |
| `src/models/routing.py` | **M7.** `PRICE_TABLE` and `rate_for` / `cost_usd` (Tier 1 free by tier, unknown paid model at the most expensive known rate with one warning), `tier_for` (critical or cascade-off is Tier 2), `escalation_reason` (the post-call predicate: `no_answer`, `rejected`, `insufficient_information`, `proposed_write`), the `ESCALATE_*` codes | a new paid model (one row). **A different question than `agent.py`'s routing**, which is why the two do not share a name |
| `src/prompts/system_prompts.py` | what a **machine** consumes: `WORK_ORDER_SCHEMA`, `SHIFT_REPORT_SCHEMA`, the tool names and descriptions, `system_prompt()` | more schemas |
| `src/prompts/agent_prompts.py` | what a **model** reads: `INHERITED_RULES`, `MANDATES` (the four responders only, `chaos` absent and a test asserts it), `SUPERVISOR_MANDATE` | chaos's brief, if chaos ever needs one |
| `src/api/routes.py` | **M8.** `create_app` (the factory: lifespan builds the one engine, `RequestIdMiddleware`, CORS from `API_CORS_ORIGINS`, the four exception handlers that keep every error in the envelope), `approver` (the bearer-token dependency, `compare_digest`, the name is `decided_by`), the six routes, `tick_events` (the hand-rolled SSE generator: latest on connect, `Last-Event-ID`, `?limit`, a ping comment) | routes the window asks for (`GET /ops/catalog` is #21); nothing that reaches the ranch or a model, and a rail greps for it |
| `src/api/schemas.py` | **M8.** `IncidentOut`, `ShiftReportOut`, `TickOut`, `PendingWriteOut`, `GateDecisionIn` (`extra="forbid"`, no `decided_by`), `GateDecisionOut`, `Meta`, `ErrorBody`, `AgentName` | fields the window needs |
| `web/lib/proxy.ts` | **M9.** `forward` (the whole proxy: `OPS_API_URL`, the token on POST only, `X-Request-ID` forwarded or minted and echoed, `Last-Event-ID`, streamed bodies, 502 / 503 in the API's envelope), `apiBase`. Server only | a seventh path when #21 lands |
| `web/app/api/ops/[...path]/route.ts`, `web/app/api/health/route.ts` | **M9.** The route handlers: four GET paths and one POST, anything else 404 here | `catalog` (#21) |
| `web/app/page.tsx` | **M9.** The one page: the `EventSource` on `/api/ops/stream` as the clock, the three loaders, the gate poll, the six panels | nothing structural |
| `web/components/` | **M9.** `Gauge` (two Recharts line charts, one x-axis), `Rails` (`railsFor`: eight chips off the tick line plus the pending count), `Summary`, `Gate` (`Card`, the confirm on approve, the answered-this-session list), `Feed` (filters), `MapPlaceholder` | a real map when #21 lands |
| `web/lib/types.ts`, `web/lib/client.ts`, `web/lib/format.ts` | **M9.** The wire shapes mirroring `schemas.py` by hand; `getJson` / `postJson` and `OpsError.human()`; times, dollars, counts | fields the panels need |
| `tests/` | three files: `test_agent.py`, `test_tools.py`, `test_api.py`. The window has no test file: its gate is `tsc`, `eslint`, `next build`, and the browser check at the close | |

`src/models/embeddings.py` and `src/utils/helpers.py` are docstring-only seams. A stub never claims to be implemented, and the marker column in `docs/Plan.md`'s tree is how you tell unbuilt from missing.

`data/knowledge_base/` holds **seven** files: `water.md` (`WATER-01` to `WATER-06`), `feed.md` (`FEED-01` to `FEED-04`), `infrastructure.md` (`INFRA-01` to `INFRA-06`), `sensors.md` (`SENSOR-01` to `SENSOR-05`), `wellhead.md` (`WELL-01` to `WELL-04`), `compliance.md` (`COMP-01` to `COMP-05`), and from M7A `herd.md` (`HERD-01` to `HERD-07`, the four animal categories). Infrastructure splits into three because a fence, a broken probe, and a gas wellhead are three unrelated bodies of knowledge, and `SOP_FOR_CATEGORY` is what maps each of the 22 categories onto one of them. **All seven are derived from `docs/sweetwater-ranch.md`, one file per sensing world, and nothing else in this repo may source them.** A rule id is citable only if it is a heading in the file the packet carried, so an SOP file is the definition of what a work order is allowed to cite. The SOP is the majority of every packet's input tokens, so it is also the cost lever.

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
17. **A `no_answer` for a retriable reason is held and re-routed; a rail rejection never is.** Durable since M6 (decision 26).
18. **`cost_usd` is on the tick line from M4**, and from M7 it is computed per order at that order's model from `routing.PRICE_TABLE`, Tier 1 at $0.00. The M4 assumed rate ($15/$75) was **3x the Opus 5 list price** ($5/$25): every dollar figure quoted before M7 is three times the real bill and the tokens beside it were always the measurement. Bedrock's row is set at first-party parity and marked unverified (`docs/open-issues.md` #14).
19. **Shutdown is written for Windows.** No `add_signal_handler`; the Runner's Ctrl+C handling plus `signal.signal` for SIGTERM/SIGBREAK, and the in-flight tick drains.
20. **The gate is one LangGraph `interrupt()` graph per proposal, checkpointed in Postgres, and the tick is not a graph.** Scott's default was a pending-writes table; the library was chosen because learning it is part of the project's purpose, and per-proposal graphs keep both requirements: the pause outlives the process, and one waiting question does not stop the watch. Code owns the checks, the key, the audit lines, and the duplicate suppression. LangGraph holds the pause and decides nothing.
21. **The checkpointer is the sync `PostgresSaver` in a worker thread.** The async saver refuses Windows' default event loop, and changing the loop policy for one driver is a change every component and test inherits. Its tables are created by alembic (`0004`) from the library's own `MIGRATIONS` list, never by `setup()`, and `memory.checkpointer()` refuses a database behind the installed library rather than upgrading it silently.
22. **A proposal is checked in code before it may pause, and none of the three checks blocks the work order.** Shape (`WRITE_TOOL_ARGS`, read off the wire), tool (`proposable_tools_for`), grounding (every id and quantity on the page, numbers compared as numbers). The failed part is stripped and recorded; the prose ships. The incident key is code's and re-checked in `executor` as `write_key_unknown`.
23. **The same write for the same incident is asked once.** A held incident is re-judged every tick until answered, so `propose()` looks for an open pause on the same key and tool and suppresses the duplicate, with no audit line. Two ticks, one pending write, and a test says so.
24. **The gate never fails the tick.** A checkpointer that cannot be opened counts `writes_failed`, holds the incident with reason `gate_unavailable`, and the shift page ships. A pause that could not be written after its `proposed` line is completed with `decision="dropped"`, so a dangling `proposed` keeps its one meaning.
25. **The audit rail is twice, or once while pending.** `gate.unpaired_audit_ids` minus `gate.pending` is empty. Only lines whose `phase` is `proposed` or `decided` count; a console line that mentions an id is commentary. From M8 the pair is a row in `sw_ops.audit_receipts` first, primary key `(audit_id, phase)`, and the file is the projection.
26. **The held set is durable** (`0005`, `incidents.held_reason`). Written and cleared by `run_tick`, read by `run_loop` before its first tick.
27. **The proposal field is described neutrally and the brief paragraph about it is rendered from the allowlist.** None is the usual answer and the description says so. Two paid ticks proposed nothing and the prompt was not steered to make the demo pause; that is the recorded result, not a defect.
28. **There are two model jobs, and "two or more worlds" gates only one of them.** The per-incident work order and the fused shift report. The plan's Tier-1 list had four jobs; chaos prose and shift-report assembly are code. The world count decides whether a tick needs someone reading across the ranch, which is the shift report, already Tier 2; it is **not** a per-incident trigger, because it says nothing about what one packet contains and would send every order on every storm tick to Opus. Scott's call, derived from the readings up in the M7 design conversation.
29. **Escalation is a rewrite from the identical page, never a review and never a retry at the same tier.** `routing.escalation_reason` reads the Tier-1 order after the rails: `no_answer`, `rejected`, `insufficient_information`, `proposed_write`. Critical is the one pre-call trigger. Opus's order is stored with `escalation=<reason>` and the Tier-1 attempt on it as `tier1_*`; a Tier-2 rejection is stored as rejected. Only a Tier-2 proposal may reach the gate.
30. **`TIER1_ENABLED` is a config knob, ships `False`, and stays so until a ledger row says the local model earned the job.** A cost and quality knob, not a safety one: the rails and the gate do not read it. Measured at M7 and not adopted; the row says why. `TIER_COMPARE` is the measurement mode and SPENDS at Tier-2 prices on every Tier-1 packet.
31. **A citation that carries the heading's title is trimmed to its id in code and recorded, not rejected.** `"FEED-02 - A bin at the warning line…"` on four of five local answers, id right every time. The `invented_rule` rail runs on the trimmed id and is unchanged; `rule_citation_trimmed` is a non-blocking note so a row can count it.
32. **An incident is about a subject, and a subject is a sensor or an animal** (migration `0006`, on Supabase 2026-09-11 with Scott's yes). `subject_id` / `subject_type`, where `subject_type` is one of the 13 sensor types or the literal `animal`; that literal is how the rows are told apart. A pure rename, no data rewrite. Chosen over writing cow ids into a column called `sensor_id`, which works today and lies to everyone who reads the table later.
33. **The herd catalog is the whole Farm list, paged in waves of 6, never the `status` filter and never the roster.** The filter is validated and still cannot find a non-active animal (cow-0905 deceased, `status=deceased` empty); the Farm API returns HTTP 500 on 2 of 12 pages at 12 or 20 in flight and 12 of 12 at 6; a deceased PATCH nulls `pastureId` so the roster loses her. The list is what vouches for an animal. About 18s a tick, measured.
34. **The herd stage never fails the tick.** A Farm or Care failure is `herd_error` on the line and an empty `answered` set: findings from what did come back still open, nothing about an animal resolves, and the tanks are still watched. An empty list is a failed stage, not an empty herd.
35. **Observations are read only for the changed set**: non-active status, pending care task, or a live incident. The one thing that leaves invisible is written down (`docs/open-issues.md` #16). A `high` observation opens only inside a 24-hour window, because the ranch has history and the debounce does nothing against a stable note.
36. **`sold` is not a finding, in code.** One non-active status is a ranch running normally.
37. **The one observation a model may propose is the flag itself** (`HERD-07`): a `general` note that the finding was made and a person sent, every word from the page, `observedAt` from the page. Opus proposed nothing on seven herd orders under `HERD-05` alone; it took `HERD-07` on the first tick it was offered. Decision 27 holds: the prompt was not steered, the SOP gained a legitimate case.
38. **The read API reads `sw_ops` and nothing else; the window reads the API through its own server-side proxy and nothing else.** One process talks to the ranch. A browser refresh cannot spend a token or fire a sweep, and the API token never reaches a browser. The API grew its sixth route (`GET /ops/gate`) by asking first; a seventh (`GET /ops/catalog`, #21) goes the same way.

## The boundary rule, sharpened

`scott-jasper/mcp-farm` is the frozen upstream. A local clone exists at `C:\temp\MCP-Farm`. **Do not read it.** The 19 tools, the `ranch://sensors/map` resource, and the four REST APIs are the contract; that repo's source is not. Constants copied out of its internals are values nothing here can verify and that fail silently when they drift. Everything needed is reachable over the wire or already written down in this repo. Learned the hard way on 2026-09-10 by doing exactly that and retracting it: `docs/cookbook.md` #5. When a fact about the upstream is genuinely unknown, learn it from a 422 against a nonexistent id (cookbook #17), never from the other repo.

---

## Environment, verified 2026-09-11

- **Interpreter is `.venv/Scripts/python.exe`.** System `python` is 3.11.9 with none of the dependencies installed. Every command in the docs assumes the venv. Node 22 for `web/` only.
- **`.env` is filled in and working.** All five upstreams, both database URLs, chaos cohort, `OPS_API_TOKEN=scooter:m8-local-dev-token-change-me-0001` (a local development token; replace it before any box other than this one serves the API). **It also has `CHAOS_ENABLED=1`**, which contradicts the rule below that it belongs at 0 (`docs/open-issues.md` #7). The tests disarm it themselves; a measurement run has to set `CHAOS_ENABLED=0` explicitly.
- **`ANTHROPIC_API_KEY` is empty, and every paid run so far shipped on Bedrock anyway.** This machine authenticates to **AWS Bedrock** (`CLAUDE_CODE_USE_BEDROCK=1`, `us-east-1`, session-scoped temporary credentials), and `resolve_provider()` picks first-party Anthropic when a key exists and Bedrock otherwise. Session credentials expire, so a loop meant to run for days needs a real key (#6). An expiry mid-run is survivable: `ExpiredTokenException` becomes a `transport_error` no-answer, the incident is **held**, and the `model` upstream backs off. `anthropic[bedrock]` is a first-order dependency; `llm_client.py` calls the raw SDK, not `ChatAnthropic`, because `langchain-anthropic` has no Bedrock path.
- **Ollama 0.33.2 is up** on `localhost:11434`, `gemma4:e4b` (8B, Q4_K_M) pulled, RTX 5070 with 12 GB. Measured at M7: 3,000 to 4,200 tokens in per packet against `OLLAMA_NUM_CTX=16384`, 4 to 13s a call warm, 22s cold, roughly two calls served at once. `keep_alive` is `30m` per call so the cadence does not unload the weights.
- **Supabase**: PostgreSQL 17.6, connects as `postgres`, can create schemas. **`sw_ops` exists**, migrated to `0007` (2026-09-11), with `alembic_version` inside it. The connection also has write access to `farm`, `feed`, `animal_care`, and `sensor`, and must never use it. The engine pins `search_path` to the target schema, and a test fails if any SQL in this repo names a ranch schema.
- **Local Postgres**: 16.4, database **`farm_systems_test`**, connects as `postgres` with no password. It already holds the upstream project's `farm`, `feed`, `animal_care`, and `sensor` schemas, so **`sw_ops_test` is the only schema this repo may create or drop.** A two-value allowlist in `memory.py` refuses anything else, and the store tests skip rather than fail when no local Postgres answers. This is the exact accident that cost three answer keys in a previous life of the project.
- Both database URLs in `.env` use the bare `postgresql://` scheme. `config.py` upgrades them to `postgresql+asyncpg://` in a validator, since SQLAlchemy otherwise reaches for psycopg2, which is not installed. Supabase's pooler additionally needs `statement_cache_size=0`, set once in the engine factory with the reason in a comment. Do not clean either of those up.
- **`SW_OPS_TARGET`** picks the store: unset or `prod` is Supabase, `test` is the local `sw_ops_test`. Prod is the default on purpose, because a default that quietly writes somewhere harmless is a default that ships. Every command in this file that says `test` means it.

## The knobs, all in `config.py` with the reasons

| Knob | Default | What it does |
| --- | --- | --- |
| `TICK_INTERVAL_SECONDS` | 300 | start-to-start cadence. Chaos TTLs are in ticks and multiply by this at injection, so the two rescale together. A free tick is about **23s** since M7A (the herd sweep is 18 to 20s of it), so a demo cadence of 20s overruns every tick and says so with `tick_overran`; that is honest, not broken |
| `SPEND_CEILING_USD` | 10.00 | per-run ceiling, summed from `cost_usd` on the tick lines, checked after every tick, **halts with exit 4.** Must be positive; no unlimited value. Not applied to `--once` |
| `INCIDENT_CONFIRM_SWEEPS` | 2 | the debounce. `1` opens on first sight, for a demo at a keyboard (`docs/open-issues.md` #8) |
| `BACKOFF_BASE_SECONDS` / `BACKOFF_CAP_SECONDS` | 60 / 900 | per-upstream exponential window (`mcp`, `sensor`, `sw_ops`, `evidence`, `model`). The herd stage has no backoff on purpose. The loop itself never sleeps past one cadence |
| `SWEEP_CONCURRENCY` / `HERD_PAGE_CONCURRENCY` / `AGENT_CONCURRENCY` | 20 / 6 / 4 | sensor reads in flight; Farm pages in flight (the Farm API 500s above 6); model calls in flight across **all** agents and both tiers |
| `TIER1_ENABLED` / `TIER_COMPARE` | 0 / 0 | the cascade, measured and not adopted; the measurement mode, which SPENDS |
| `LOG_ROTATE_BYTES` | 10 MB | size cap on `tick.jsonl` and `agent.jsonl`; set small to force a rotation inside a verification run |
| `LOG_TRANSCRIPTS` | 0 | full page and answer per work order and per fused report under `logs/transcripts/{run_id}/`. Real since M7A |
| `CHAOS_ENABLED` | `0` | the master switch. Off means the overlay is never read and a tick is byte-for-byte what it was before M5 |
| `CHAOS_SEED` | `1` | same seed, same demo, forever. `plan()` is pure |
| `CHAOS_ALLOW_WRITES` | `0` | the only thing standing between a scenario and a real `PATCH` on the deployed Farm API |
| `CHAOS_ANIMAL_COHORT` | five `cow-090x` ids | every animal mutation is confined to this set. An empty cohort means no animal event can fire at all |
| `CHAOS_MAX_ACTIVE` | `6` | the ceiling. A correlated group that would cross it is deferred **whole**, never half-fired |
| `OPS_API_TOKEN` / `API_HOST` / `API_PORT` / `API_CORS_ORIGINS` | required / `127.0.0.1` / `8000` / empty | the read API. `name:secret` pairs, 16-character floor, exit 2 without one. CORS stays empty because the window is same-origin |

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
- 160 bounded reads at concurrency 20 complete in about 3s, all 200s.

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
- **A deceased PATCH nulls `pastureId`.** The dead cow leaves every pasture roster. `chaos restore` puts the status back and leaves her pastureless (`docs/open-issues.md` #17).
- `GET /pastures?limit=50` returns all 18 pastures with `animalIds` inline, 1,195 ids, in 1.2s. Context and a second source, not the catalog.
- **Observations list per animal only.** `GET /observations` is a 404; `GET /animals/:id/observations` takes `limit` / `offset` and silently ignores `since`, `from`, `observedAfter`, `after`, `start`, and `severity`. The MCP `list_observations` requires `animalId`. ~0.35s a read, clean at 6 concurrent.
- **Care tasks are `GET /care-tasks`** (not `/tasks`), `status` and `animalId` honoured and validated. Five exist, all `pending`, three overdue on 2026-09-11 (cow-0777 since 08-10, cow-0512 since 08-18, sheep-0001 since 09-05). Their titles carry em dashes (#19). **Those three open `care_overdue` on every fresh ledger's second tick and stay `ongoing`; that is the ranch, not a defect.**
- **The ranch has history.** cow-0777 carries a `high` `mobility` note from 2026-08-09; cow-0001 carries four notes, the newest 2026-09-09 saying "Overdue for contact per SOP-02", so something else writes into this Care API too. There is one orphan observation against a nonexistent animal, ours, from an M5 probe (`docs/open-issues.md` #9).

The seven sensors that misbehave on purpose are listed at the bottom of `docs/sweetwater-ranch.md`. They read badly on every call, which makes them the only findings guaranteed to persist across ticks. Healthy sensors also draw extreme values a fraction of the time, so **18 to 23 findings a tick with chaos off is normal**, and the debounce is what keeps them from becoming work orders. That churn is the feed working, not a bug to suppress.

---

## What a tick costs, so nobody re-measures it to orient

A free tick (`--no-spend`, or a tick that opens nothing) is **$0.00 exactly** and about 23s. A paid tick costs per **newly-opened** incident, not per open one and not per sensor: about **$0.05 a sensor work order and $0.06 a herd order** at the Opus 5 list price, 5,000 to 7,000 tokens in of which the SOP is the majority, 14 to 16s a call at 4 in flight. The fused shift report is one more call on a tick where two or more worlds opened, about 13% of that tick's bill. At the debounced steady state of one to three new incidents a tick the loop runs **$0.05 to $0.20 a tick, $0.60 to $2.40 an hour**, all Opus, and the ceiling halts it. Every measured tick, with the tokens: `docs/model-routing.md`.

---

## Driving a demo

Everything below runs against the **test ledger** unless you mean prod. `resolve_store` defaults to prod, which is right for a tick and wrong for a copy-pasted experiment.

```bash
export SW_OPS_TARGET=test CHAOS_ENABLED=1        # drop SW_OPS_TARGET when you mean prod
python -m src.tools.chaos status                 # what is armed, and how far through its TTL
python -m src.tools.chaos plan --ticks 6         # what this seed will do, against the live catalog, touching nothing
python -m src.tools.chaos inject --tick 2        # arm tick 2's events, and heal anything expired
python -m src.tools.chaos expire                 # heal everything whose TTL is up
python -m src.tools.chaos restore                # put the cohort back to `active`. Needs CHAOS_ALLOW_WRITES=1

python -m src.agent.gate list                                  # every proposal still waiting, oldest first
python -m src.agent.gate approve <audit_id> --by scooter       # performs the write over MCP with an Approval; exit 1 if the ranch refused it
python -m src.agent.gate reject  <audit_id> --by scooter --reason "tank 2 is fine"   # --reason is required

SW_OPS_TARGET=test python main.py --api          # the read API on the same ledger; then `cd web && npm run dev` for the window
```

`restore` **exits 1 when it restored nothing**, which is what a guarded refusal looks like from a shell. The other four exit 0 whether or not they had anything to do, because "nothing was due" is a successful answer. `plan` reads the live catalog and touches no database at all. `write_paused` in the console stream, with the audit id and the CLI hint, is the line that says a human is needed; a kill on the cohort produces one under `HERD-07`.

**The gate-demo rule (cookbook #47).** The gate has no belt of its own: a human's approve *is* the belt for agent writes, and `CHAOS_ALLOW_WRITES` is a different switch for a different actor. So a pause planted for a demo and approved from the window or the CLI **writes to the deployed ranch** if the tool is real. When the point is the approve path, plant a tool the ranch does not have (`add_care_note` is not one of the 19; the ranch refuses it at the end and every other line runs). When the pause names a real tool, reject it with a reason. Plant through `propose()` from a process that has called `configure_logging()`, or the `proposed` line reaches only the console (cookbook #33).

Two injection paths, and the asymmetry is deliberate: **sensor faults are a lie this repo tells over a truthful upstream**, applied in `sweep()` and nowhere else; **animal events are real writes** through two different APIs, because `herd_health` has to find them with its own tools. Detail in `src/tools/CLAUDE.md`.

---

## Traps a new session will hit. Each one cost somebody an afternoon.

1. **`pytest` drops `sw_ops_test`** and re-migrates it, on the first store-backed test, including under a `-k`. A demo on the test ledger and a `pytest` run cannot interleave, and two sessions cannot run `pytest` at once on this machine (cookbook #18, #41). **Check for stray `main.py` processes before either**: the M9 close found the M8 close's loop and API still running thirty minutes later.
2. **A replay is not a re-run.** Chaos event ids are derived from seed, tick, and index, so injecting the same seed twice inserts nothing the second time. Re-driving a demo from the top means truncating `chaos_events` first (cookbook #19).
3. **`AsyncPostgresSaver` does not run on Windows' default event loop.** Use `memory.checkpointer()`, the sync saver in a thread. **Never call `saver.setup()`**: the checkpointer's tables are alembic's (`0004`), and a newer library is a new revision, which `memory.checkpointer()` tells you by refusing to open (cookbook #30, #31).
4. **Code above `interrupt()` runs twice.** The node does nothing but ask; every side effect is outside the graph (cookbook #29).
5. **`AGENT_CONCURRENCY = 4` is the ceiling for both tiers**, and Ollama serves roughly two at once, so a 14-incident local tick is about 60s of local calls, not 15s.
6. **`THINKING_BUDGET` sends `budget_tokens`, which Opus 5 rejects with a 400.** Nothing uses it; the first job that turns thinking on fixes `call_tier2` first (`docs/open-issues.md` #13).
7. **`logs/compare.jsonl` carries two work orders of model prose per line**, on purpose and only when `TIER_COMPARE` is on. It is not one of the three operational streams.
8. **Every test in a process shares one `run_id`**, so any rail that runs `run_tick` on the test ledger needs `ticks` and `shift_reports` truncated first (`target` does it).
9. **`curl` against `/ops/stream` needs `-N` and a `?limit`**, or it sits there forever looking hung, which is the stream doing its job.
10. **`writes_pending` on a tick line is `null` on a tick that proposed nothing.** The ledger's answer is `GET /ops/gate` (cookbook #48).
11. **`create-next-app` refuses a non-empty directory**, so a rescaffold of `web/` goes through a temp directory.
12. **`HERD-07` makes `herd_health` propose a note on every `deceased` and critical `observation_high`**, so a kill on the cohort is a pause waiting at `python -m src.agent.gate list` until somebody answers it.
13. **The Vercel deploy is not done** (`docs/open-issues.md` #23). The window is verified against `next dev` on this machine only.
14. **Do not write source through a shell heredoc.** The escapes do not survive (cookbook #46).

---

## Working preferences that cost context to rediscover

Short answers. No recap tables of things already agreed. Do not read whole files when a targeted search answers the question. Do not dump raw probe output into the transcript, summarize it. Batch independent reads and commands into one round.
