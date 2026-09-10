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
  "findings":21,"critical":6,"opened":9,"ongoing":8,"resolved":4,"held_unread":0,
  "agents_routed":["water_feed","infrastructure"],
  "work_orders":9,"work_orders_shipped":9,"work_orders_rejected":0,"escalated":1,
  "input_tokens":50385,"output_tokens":7954,
  "ledger":{"opened":9,"ongoing":8,"resolved":4} }
```

The M2 shape. `chaos_fired` joins at M5 and `cost_usd` at M7 with the pricing table; a
field is added to this line when the stage that produces it exists, not before, so a
`null` here always means the stage ran and had nothing to say.

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
{ "ts":"…","run_id":"…","tick":42,"audit_id":"7f3c…","phase":"proposed",
  "tool":"create_observation","args":{"animalId":"cow-0777"},
  "proposed_by":"herd_health","incident_key":"cow-0777:down" }
{ "ts":"…","run_id":"…","tick":42,"audit_id":"7f3c…","phase":"decided",
  "decision":"approve","decided_by":"scott","result":"201","latency_to_decision_ms":94000 }
```

**Two lines per side effect, correlated by `audit_id`.** Deliberately two rather than
one: a `proposed` with no matching `decided` is a pause nobody ever answered, and that
should read as a dangling record you can grep for, not as an absence you have to already
suspect.

This is the file that makes "we watch your ranch" a defensible claim rather than a
pitch.

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
jq -r '.audit_id' logs/audit.jsonl | sort | uniq -c | awk '$1!=2'   # should be empty
```

Cost flat on calm ticks, spiking only where an escalation is logged beside it. Anything
in the second query is a config bug, not a weak model. Anything in the third is a pause
nobody answered.

The first query's field names are `input_tokens` and `output_tokens`, matching the model
API and `agent.jsonl` rather than the `tokens_in` / `tokens_out` this file used to print,
because two spellings for one number is a query that returns `null` and looks like a calm
ranch. `cost_usd` arrives with the pricing table at M7; until then the token counts are the
cost signal.

`logs/*.jsonl` is gitignored; `logs/.gitkeep` is not. A captured run worth keeping goes
into `docs/` next to the finding it supports.
