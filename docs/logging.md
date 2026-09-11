# Logging: three streams, JSON Lines, and one field that matters most

Implementation: `src/utils/logger.py`. Wired at **M0**, before anything it measures
exists. Every rung of the project this grew out of was only legible because the
instrument was built first; a gauge added after the fact only measures what you already
suspected.

## Why structlog, and why over the stdlib

The files must be machine readable (the read API and the test rails both parse them)
while the console must be human readable during a thirty-minute watch. structlog gives
both from one call. Configured **over** the stdlib rather than beside it, so `httpx` and
`langchain` chatter lands in the same stream instead of a second, differently-formatted
one that has to be correlated by eye.

## Format

JSON Lines, one object per line. UTC ISO 8601 with **milliseconds**
(`2026-09-10T14:30:00.000Z`), matching the four upstream services exactly, because
lexicographic ordering is relied upon and sorting mixed precision misorders silently.

**`run_id` and `tick` appear on every line in all three files.** That is the whole
correlation story: three streams join on a grep, with no log aggregator required.

Console gets `ConsoleRenderer` when `LOG_CONSOLE_PRETTY=1`, JSON otherwise (for a
container). Files are **always** JSON regardless of environment.

## `logs/tick.jsonl` - the heartbeat, one line per tick

```jsonc
{ "ts":"2026-09-10T14:30:00.000Z","run_id":"9a10c5f00357","tick":42,"duration_ms":8140,
  "store":"prod","catalog_source":"mcp_resource",
  "sensors_read":160,"sensors_failed":0,
  "findings":21,"critical":6,"opened":2,"ongoing":8,"resolved":4,"pending":7,"dismissed":9,"held_unread":0,
  "agents_routed":["water_feed","infrastructure"],
  "work_orders":9,"work_orders_shipped":9,"work_orders_rejected":0,"escalated":1,
  "input_tokens":50385,"output_tokens":7954,"cost_usd":1.35,
  "worlds":["infrastructure","water_feed"],"shift_report":"model","shift_report_violations":[],
  "ledger":{"opened":9,"ongoing":8,"resolved":4},
  "held":0,"skipped_upstreams":[],
  "writes_proposed":1,"writes_duplicate":0,"writes_failed":0,"writes_pending":1,
  "chaos_fired":0,"chaos_healed":0,"chaos_missed":[] }
```

The M6 shape. A field is added to this line when the stage that produces it exists, not
before, so a `null` here always means the stage ran and had nothing to say.

**The gate's four fields, M6.** `writes_proposed` is how many proposals paused for a human this
tick; `writes_duplicate` is how many were suppressed because the same write for the same incident
was already waiting; `writes_failed` is how many the gate could not persist (their incidents are
held with reason `gate_unavailable`); `writes_pending` is everything waiting across the ledger after
this tick, **or `null` when the tick had nothing to propose and never opened the gate**, because a
count nobody measured must not read as zero.

**`cost_usd` arrived at M4, not M7 as planned**, because the loop's spend ceiling is
denominated in dollars and a ceiling in tokens is a multiplication somebody does wrong at 2am.
It is `input_tokens` and `output_tokens` at `llm_client.ASSUMED_RATE_USD_PER_M`, a stated
assumption in one place; M7's pricing table replaces the constant and not the field. Exactly
`0.0` on a tick that billed nothing. The loop sums it per run and halts at `SPEND_CEILING_USD`.

**`pending` and `dismissed` are the debounce** (migration 0003). `pending` was flagged this sweep
and not yet seen on `INCIDENT_CONFIRM_SWEEPS` consecutive sweeps; `dismissed` was pending and read
clean. Neither is routed or billed. A high `dismissed` beside a low `opened` is the simulator's
dice being filtered out, and it is the number that used to be the bill.

**The loop's five fields.** `held` is incidents carried unanswered into the next tick because
their agent raised, the model died in transport, or the spend stages were in backoff; they are
re-routed next tick, and a rail rejection is never among them. `skipped_upstreams` names who was
inside a backoff window; a line with `["sensor"]`, `error: null`, and `sensors_read: 0` is a
heartbeat during an outage, not a calm ranch. `chaos_fired` and `chaos_healed` are what the tick
armed and expired when chaos is on. **`chaos_missed` is the one to grep for**: an event this run
injected that healed without its sensor ever being read. Non-empty means a fault was born and
died between two sweeps and the ranch read calm the whole time.

**A tick in free-pass backoff writes a short line**, `skipped_upstreams`, `held`, `cost_usd`,
`error`, `failed_stage`, and nothing about sensors or the ledger, because nothing was attempted.
A tick that writes no line is indistinguishable from a dead loop, and that rule holds hardest
exactly when an upstream is down.

**`worlds` and `shift_report` are read as a pair, and that is the only reason both are here.**
`worlds` is the storm-front count, `shift_report` is `"model"` or `"code"`, and together they
say whether the supervisor paid to fuse the tick or assembled it for free. One world is always
`"code"` by construction (`FUSION_THRESHOLD`), so a two-world tick reading `"code"` is either a
free pass or a rail that fired, which is what `shift_report_violations` disambiguates. **A
non-empty violations list beside `"code"` is the fallback having shipped**, and it is how a
truncated supervisor was found at the M3 boundary.

**`input_tokens` and `output_tokens` include the supervisor's call from M3 onward**, not just
the responders'. The M2 flat-cost query below still reads correctly - the supervisor bills only
on a tick that already fanned out - but a per-work-order average taken off this line is now
slightly high, and `logs/agent.jsonl` is where to go for the split.

**`chaos_fired` was planned for M5 and landed at M4**, with the loop, because wiring an injector
into a tick is cadence work. `run_tick` heals, injects, and fires animal events right after the
catalog (the plan needs the topology), and its own failure never fails the tick: a
`chaos_step_failed` warning and honest readings. `SweepResult.overlay_observed` is the other
half, the event ids whose sensor the sweep actually read, and it is what `chaos_missed` is
computed against.

**Written at tick end, always, including when the tick failed** (with `error` and
`failed_stage`). A tick that produces no line is indistinguishable from a dead loop, and
that distinction is the entire product at 2am.

`failed_stage` is set **before** each stage is attempted. A line reading
`failed_stage: null` beside an error says a tick died without saying where, which is the
one question the field exists to answer. Caught during M0 exactly this way.

One line per tick means the cost curve is one `jq` away, which is the only way anyone
notices it stop being flat.

## `logs/agent.jsonl` - the instrument, one line per model call

```jsonc
{ "ts":"…","run_id":"…","tick":3,"agent":"water_feed","tier":2,
  "provider":"bedrock","model":"us.anthropic.claude-opus-5","reasoning_effort":"none",
  "max_tokens":2048,"tool_calls":1,"input_tokens":4816,"output_tokens":1102,
  "finish_reason":"tool_use","latency_ms":14095,
  "content_types":["tool_use"],"incident_key":"feed-bin-03:feed_low" }
```

That is a real M2 line. Tier 1 adds `num_ctx` and Tier 2 does not have one; the required
fields are the ones in `log_agent_call`'s signature and everything else is per-call
context. `content_types` is the block types the response actually contained, which is how
"answered with no tool call" reads differently from "never answered."

**`finish_reason` is the most valuable field in this whole scheme and it is required.**
Truncation means the model never got to answer; completion means it answered and answered
badly. One is a config bug, one is a model-selection decision, and **in the response
text they look identical**. Telling them apart cost a real investigation once.

**The vocabulary is per provider.** Anthropic returns `tool_use`, `end_turn`,
`stop_sequence`, `max_tokens`; Ollama and the OpenAI-shaped APIs return `stop`, `length`,
`tool_calls`. Any query over this field has to name the healthy **set**, not one healthy
value, or it flags an entire provider as broken. See below, and `src/models/CLAUDE.md`.

**Written immediately on return, before validation runs**, so a response that fails a
check still leaves a receipt of what was actually returned rather than vanishing into a
retry.

## `logs/audit.jsonl` - the receipt, one line per side effect

```jsonc
{ "ts":"…","run_id":"…","tick":42,"audit_id":"2f7c…","phase":"proposed",
  "tool":"restock_feed","args":{"sku":"alkali-flat-water-2","quantity":16.7},
  "proposed_by":"water_feed","incident_key":"alkali-flat-water:water_low" }
{ "ts":"…","run_id":"…","tick":0,"audit_id":"2f7c…","phase":"decided",
  "decision":"approve","decided_by":"scooter","result":"written","latency_to_decision_ms":73991,
  "tool":"restock_feed","incident_key":"alkali-flat-water:water_low","reason":"","upstream":"{…first 200 chars of the upstream body…}" }
```

**Confirmed against real lines on 2026-09-11**, M6, with two corrections to what this file used
to promise. The `decided` line repeats `tool` and `incident_key`, because it is written by a
different process (the CLI) hours later and a reader grepping one id should not have to join two
lines to know what was decided; and it carries `reason` (required on a reject, empty on an
approve) and `upstream` (the first 200 characters of what the ranch answered, or the exception).
Its `tick` is the deciding process's tick, which for the CLI is `0`; the proposing tick is on the
`proposed` line. `result` is a short code, not a status number: `written`,
`upstream_error_<category>`, `transport_<ExceptionName>`, `not_executed` on a reject, and
`checkpointer_unavailable` on a `dropped`.

`decision` takes five values across two writers. From the gate: `approve` and `reject` (a human,
`decided_by` is their name), and `dropped` (`decided_by:"gate"`, the pause could not be persisted
after the `proposed` line was written; the incident is held and the write is proposed again).
From chaos, below: `blocked` and `auto_allowed`.

**Two lines per side effect, correlated by `audit_id`.** Deliberately two rather than
one: a `proposed` with no matching `decided` is a pause nobody ever answered, and that
should read as a dangling record you can grep for, not as an absence you have to already
suspect. From M6 that dangling record is the **normal** shape of an open pause: the gate writes
`proposed` when the work order proposes and `decided` only when a person answers, and
`python -m src.agent.gate list` is how a person finds the ones still waiting. A duplicate
proposal (same incident, same tool, already waiting) writes no line at all, because it is not a
new side effect.

This is the file that makes "we watch your ranch" a defensible claim rather than a
pitch.

**A blocked side effect writes both lines too.** When `CHAOS_ALLOW_WRITES=0` stops an animal
mutation, chaos still emits `proposed` and then `decided` with `decision:"blocked"`,
`decided_by:"chaos_guard"`, `result:"writes_disabled"`. The receipt that nothing was mutated
is worth exactly as much as the receipt that something was, and it keeps the
`uniq -c | awk '$1!=2'` query below meaningful: a guard that logged only the refusal would
show up in that query as a dangling proposal.

**`tool` is the MCP tool name when one was used, and the HTTP route when one was not.**
Chaos writes go direct over REST rather than through MCP, so its lines read
`"tool":"PATCH /animals/:animalId"`. Naming a tool it never called would be a tidier field
and a false receipt.

## `chaos_*` on the console stream - the overlay says so out loud

**There are three JSONL files and there is no fourth.** `chaos_*` lines go to the console
stream, like every other application event, and only the animal write path reaches
`audit.jsonl`. An overlay is a development and demo instrument rather than a side effect on
the ranch, and a fourth rotating file for it would be a stream nobody greps.

The events, and what each one is for:

| Event | Says |
| --- | --- |
| `chaos_injected` | one event armed, with `scenario`, `mode`, `target_id`, `group_id`, `expires_at` |
| `chaos_expired` | a TTL ran out and a sensor is honest again. This is what produces `resolved` incidents |
| `chaos_overlay_applied` | **carries `honest_value` beside `faked_value`.** The single most useful line in the stream |
| `chaos_overlay_unavailable` | the store could not be read, so the sweep stayed truthful. A degraded overlay must never be a failed tick |
| `chaos_at_ceiling` | `CHAOS_MAX_ACTIVE` reached, nothing new armed |
| `chaos_group_deferred` | a correlated group would have crossed the ceiling, so **none** of it fired. Half a storm front is a worse fixture than no storm front |
| `chaos_insert_deduplicated` | `offered` vs `inserted`. A replayed seed or an already-active fault on the same target, both skips rather than errors. See `docs/cookbook.md` #19 |
| `chaos_write_blocked` | a guard refused an animal mutation, with the reason |
| `chaos_write_partial` | the `PATCH` landed and the observation did not. **Not retried**, because the status is already changed and a retry would double-write the observation |
| `chaos_animal_written` | a real mutation went through, which only happens with `CHAOS_ALLOW_WRITES=1` |
| `chaos_unknown_fault_mode`, `chaos_fault_unusable`, `chaos_no_target_for_type`, `chaos_no_cohort_target` | four `warn_once` lines for a catalog that asks for something the ranch cannot supply. A scenario that silently does nothing is the failure mode here, same disease as an unrecognized sensor type reading as nominal |
| `chaos_cli_catalog` | the CLI resolved the live topology, with `source` and `sensors` |

`chaos_overlay_applied` is the line that makes a faulted demo legible instead of
mysterious. Without `honest_value` beside `faked_value`, a reader of the log cannot tell a
ranch that is being lied to from a ranch that is actually broken, which is the same
indistinguishability triage is **supposed** to have and the operator is not.

Because the stream is the console, a query means redirecting a run rather than reading a
file, and three things about that are easy to get wrong. **`LOG_CONSOLE_PRETTY=0` is
required**, or the renderer emits aligned text and `jq` gets nothing it can parse. **The
event name is `msg`, not `event`**, in every mode: a processor renames structlog's
positional field once, before any renderer, so there is exactly one spelling. And **the
stream is not pure JSON** - a CLI prints its own human-readable summary to the same place,
and a traceback is not JSON either, so a `jq` filter over it has to tolerate lines that do
not parse:

```bash
LOG_CONSOLE_PRETTY=0 SW_OPS_TARGET=test CHAOS_ENABLED=1 python -m src.tools.chaos inject --tick 2 2>&1 | jq -Rrc 'fromjson? | select((.msg//"")|startswith("chaos_")) | [.msg,.scenario//"-",.target_id//"-"]|@tsv'
```

`-R` with `fromjson?` is what makes that tolerance work: without it, the first non-JSON line
kills the query with a parse error and the exit code blames the data. This exact command was
documented in its naive form first, and it failed on all three counts at once.

## Rotation differs by stream, and the difference is the point

| Stream | Rotation | Why |
| --- | --- | --- |
| `tick.jsonl` | 10 MB, 5 back | a diagnostic; oldest is discardable |
| `agent.jsonl` | 10 MB, 5 back | same |
| `audit.jsonl` | daily, **no size cap** | a receipt. A size cap on an audit trail means the trail ends exactly when the ranch got busiest. |

## Never logged

`ANTHROPIC_API_KEY`, `DATABASE_URL`, or any full prompt or response body. Secrets are
replaced with `[redacted]`; bulk bodies with `[omitted: set LOG_TRANSCRIPTS=1]` rather
than deleted, so a reader can tell "there was a prompt we chose not to store" from
"there was no prompt."

`LOG_TRANSCRIPTS=1` writes full bodies to `logs/transcripts/{run_id}/{tick}-{agent}.json`.
Off by default because prompts dwarf everything else on disk. Invaluable for exactly one
job: a finding that reads wrong and a log that cannot say why.

## Reading the logs is the real test of whether the instrument works

```bash
jq -r '[.tick,(.input_tokens//0),(.output_tokens//0),(.work_orders_shipped//0),(.escalated//0)]|@tsv' logs/tick.jsonl
jq -r 'select(.finish_reason | IN("stop","end_turn","tool_use","stop_sequence") | not)' logs/agent.jsonl
jq -r '.audit_id' logs/audit.jsonl | sort | uniq -c | awk '$1!=2'   # every id here must be in `python -m src.agent.gate list`
```

Cost flat on calm ticks, spiking only where an escalation is logged beside it. Anything
in the second query is a config bug, not a weak model. Anything in the third is a pause
nobody answered: from M6 that is a legitimate open pause **only if** the same id is in the gate
CLI's `list`, and anything else in that query is a decision path that skipped its log line
(`gate.unpaired_audit_ids` is the same check in code, and the rail in `tests/`).

The first query's field names are `input_tokens` and `output_tokens`, matching the model
API and `agent.jsonl` rather than the `tokens_in` / `tokens_out` this file used to print,
because two spellings for one number is a query that returns `null` and looks like a calm
ranch. `cost_usd` is on the line from M4 at an assumed rate; the token counts stay the
measurement and the dollars are arithmetic on top of them.

The loop's own lines, in the main stream rather than `tick.jsonl`: `loop_start`, `loop_stopped`
(clean drain, exit 0), `loop_halted` (`reason=spend_ceiling`, exit 4), `loop_unrecoverable`
(exit 1), `loop_draining` on the first interrupt, `stop_requested` with the signal name,
`upstream_backoff` / `upstream_recovered` per upstream, `tick_overran`, `incidents_held`,
`held_rerouted`, and `chaos_event_missed`. M6 adds `held_restored` / `held_restore_failed` at loop
start and `held_not_persisted` when the column could not be written; and the gate's own:
`write_paused` (the one to watch for, with the audit id and the CLI hint), `write_decided`,
`write_proposal_duplicate`, `write_proposal_dropped`, `write_key_unknown`, `write_proposal_shape` /
`write_proposal_ungrounded` when a check fired, and `gate_unavailable` when the checkpointer could
not be opened at all.

**Two processes append to `audit.jsonl`**: the loop writes `proposed`, the CLI writes `decided`,
each through its own handler on the same file. A one-line append is atomic enough for a receipt
on Windows in practice, and both lines were read back intact on the first live run. What is not
safe is the daily rotation firing in both at once: the loop holds the file open across midnight
and the CLI is short-lived, so the collision is unlikely but not impossible. The checkpointer
row is the durable truth and the line is the receipt; `docs/issues.md` carries it.

`logs/*.jsonl` is gitignored; `logs/.gitkeep` is not. A captured run worth keeping goes
into `docs/` next to the finding it supports.
