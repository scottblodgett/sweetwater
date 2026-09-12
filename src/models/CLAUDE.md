# src/models - two tiers and the traps

The design and the ledger are `docs/model-routing.md`: why thinking is off when code owns severity, why local
models write well and navigate badly, the two tiers, the predicate, and every measured row. This file is the part
you must not get wrong while editing code.

## Ollama specifics that will bite otherwise

- **Use `ChatOllama` from `langchain-ollama`. Never `ChatOpenAI` at the compatibility
  shim.** `ChatOllama` speaks the native `/api/chat` endpoint where **`num_ctx` is
  actually honored**; the shim silently ignores it. That silence is what produced an
  entire investigation once: thinking models spent the whole 4,096-token default budget
  reasoning and returned a message with no tool call and no content, which looks exactly
  like "small models are too weak" and is not.
- **Set `num_ctx` explicitly** (`OLLAMA_NUM_CTX`, 16384). Never rely on a default.
- **`reasoning_effort` is an explicit per-call argument, never an ambient env var.** A
  knob that silently changes verdicts belongs where the actor is constructed. Reading it
  from the environment is how a landed fix once failed to reach the rung it was written
  for: a second actor construction had quietly stopped receiving it, and nothing failed
  loudly.
- **Every actor construction goes through `llm_client.py`.** One place, so the previous
  bullet cannot happen twice. If you find yourself building a chat model inline, that is
  the bug.

## Log `finish_reason` on every single call

A truncated call means the model never got to answer. A completed call means it answered
and answered badly. One is a config bug and one is a model-selection decision, and **in
the response text they are identical**. A log holding only the final text cannot tell
them apart, and telling them apart is worth more than any other field in `agent.jsonl`.

**The two providers do not share a vocabulary, and the query has to know that.** Anthropic
says `tool_use`, `end_turn`, `stop_sequence`, `max_tokens`. Ollama and the OpenAI-shaped
APIs say `stop`, `length`, `tool_calls`. M2 shipped with a documented `jq` that selected
`.finish_reason != "stop"`, which reported **every healthy Opus call** as a config bug,
because no Anthropic call has ever returned `stop`. So the query names the healthy set
rather than one healthy value:

```bash
jq -r 'select(.finish_reason | IN("stop","end_turn","tool_use","stop_sequence") | not)' logs/agent.jsonl
```

Empty is healthy. `max_tokens` or `length` in there is a config bug, not a weak model.
`ModelResponse.ok` and `.truncated` in `llm_client.py` hold the same two sets, and they
are the definition; if a third provider arrives, widen both together.

## Tier 1 exists from M7, and it is `call_tier1`

Same shape as `call_tier2`: one call, one receipt, no retry, no decision. Three things differ and
each is deliberate:

- **The schema goes in `format=`**, Ollama's grammar-constrained decoding, not a tool call. That
  is the native equivalent of the forced tool call: the shape is enforced, and `done_reason`
  (`stop` / `length`) stays honest independent of whether the text parsed. `content_types` is
  `["json"]` when it parsed and `["text"]` when it did not.
- **`reasoning_effort` becomes Ollama's `think` boolean.** `"none"` is off; anything else is on
  with the server's budget. Still an explicit argument at the call site.
- **`keep_alive` is `30m`.** Ollama's default is five minutes, which is exactly the tick cadence,
  so the weights would unload and reload on every tick (22s cold against 4s warm).

**Which tier a job gets is `routing.py`'s, never this module's.** `workers.judge_packet` and
`agent.synthesize` ask `tier_for(text, max_tokens=)`, call one tier, run the rails, ask
`escalation_reason` (or the report's equivalent), and rewrite at Tier 2 when told. The two tiers see
the identical string, which is what makes an escalation a fair rewrite and a `TIER_COMPARE` pair a
fair comparison.

**From M10 `call_tier1` has three jobs, and it is asked first for two of them at every severity.**
The per-incident work order and the fused shift report go to Tier 1 whenever the cascade is on and
the prompt fits `num_ctx` (`routing.fits_tier1`, the one pre-call reason, `page_too_long`); the
third job is the investigator's loop in `src/agent/investigator.py`, which builds its actor through
`build_tier1_client(reasoning=False)` with **no `format=`**, because a grammar-constrained model
cannot emit a tool call: the loop navigates, the judge judges. Critical is not a pre-call reason any
more; `ESCALATE_CRITICAL` left the vocabulary. The predicate is `docs/model-routing.md`'s and
`docs/state.md` decision 29.

**Measured at M7, `gemma4:e4b` on this box**: 3,000 to 4,200 tokens in per packet against
`num_ctx` 16,384, so the SOP is never truncated and `tier1_context_full` never fired; 4 to 13s a
call warm. The model copied whole SOP headings into `rules_cited` on four of five answers
(`"FEED-02 - A bin at the warning line…"`), id right every time, so `workers.check` trims a citation
to the id at its front and records `rule_citation_trimmed` before `invented_rule` runs.

**Measured at M10, same model**: the 18-order fused page is 10,630 tokens with the mandate (Opus
counts the same text at 15,131), so it fits with about 3,700 to spare after a 2,048-token answer and
`fits_tier1` at 3.0 chars per token is the guard for the day it does not. In a tool loop the model
emitted its tool calls **as text** on 9 of 15 loops (`get_animal{animalId:<|"|>cow-0777<|"|>}`), which
Ollama did not parse into `tool_calls`, so the loop ended with `no_tool_calls`; and where it did call
tools it reached for `list_sensors` whole (36k chars, truncated by the interceptor) before its own
sensor. As the supervisor it put sentences into `linked` on 4 of 4 reports, which `invented_incident`
caught every time and the keyed schema (`shift_report_schema_for`) now forbids by grammar. The rows in
`docs/model-routing.md` carry the numbers and the verdict.

## Two credentials, one call path

`resolve_provider()` picks first-party Anthropic when a key exists and **Bedrock**
otherwise, scoping the model id to `us.anthropic.…` for the Bedrock path only, so
`TIER2_MODEL` stays the first-party spelling in `.env`. `AsyncAnthropic` and
`AsyncAnthropicBedrock` expose an identical `messages.create`, so provider choice is a
constructor decision and nothing downstream changes shape.

**The Bedrock path rides this session's temporary AWS credentials, which expire.** A loop
meant to run for days needs a real `ANTHROPIC_API_KEY`. When they expire mid-run the failure is
data, not an exception: `ExpiredTokenException` in `ModelResponse.error`, a `no_answer` order
with `transport_error`, which from M4 puts the incident in the loop's **held** set and, when
every order on the tick died that way, backs the `model` upstream off. The incidents are
re-routed when the credential is back; nothing is lost, and nothing is retried on a cadence
against a dead credential. M4's own paid run was four minutes on Bedrock and never hit it.

## `THINKING_BUDGET`, and the two ways to misconfigure it

`{"none": 0, "low": 2048, "medium": 6144, "high": 12288}`, keyed by `reasoning_effort`.
`none` **omits the thinking block entirely** rather than sending a zero budget, which the
API rejects. Two traps beyond that: `temperature` may not be set at all while thinking is
on, so the knob is only offered when it is off; and `max_tokens` has to be raised **above**
`budget_tokens`, or the answer is all reasoning and no content. `call_tier2` adds the
budget to `max_tokens` rather than sharing it.

**A third trap, found at M7 and not yet fixed:** Claude Opus 5 rejects `budget_tokens` with a 400 and
wants `{"type": "adaptive"}` plus `output_config.effort`. Nothing here sends anything but `"none"`, which
omits the block, so every measured call was unaffected; the first job that wants thinking on fixes
this function first (`docs/open-issues.md` #13).

## embeddings.py is a seam, not a feature

Each sensing world's SOP set is a handful of rules. Loading the whole SOP file into the
prompt beats retrieving over it, and pretending otherwise is the cleverer option rather
than the simpler one. The file exists so retrieval has an obvious home when the corpus
outgrows a prompt. Do not fill it in before that happens.
