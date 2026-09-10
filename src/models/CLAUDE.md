# src/models - two tiers and the traps

Full reasoning and the running ledger live in `docs/model-routing.md`. This file is
the part you must not get wrong while editing code.

## The one rule about turning thinking off

> **Turning thinking off is free exactly when the model is not the one classifying.**

Measured, not assumed: with severity decided in code and the model only writing prose,
reasoning off cost nothing. With the model handed the verdict and asked to justify it,
reasoning off took the same setup from **94/97 correct to 2/100**, fabricating
justifications for labels it had already been given. Since `triage.py` owns severity
here, no sub-agent is classifying, which is what makes a local default viable at all.

## The job that breaks local models is navigating, not writing

At full context budget a 9b model on an open-ended tool loop made **16 `list_sensors`
calls, zero `read_sensor` calls, and never answered**. Handed the map instead, it read
8 of 28 water sensors and returned a **confident false all-clear** where Opus found 4
tanks below the floor. The same size model, handed an assembled evidence packet and
asked to write, produced 72 advisories with 0 retries and 0 check failures.

So: **local models write well and navigate badly.** Give Tier 1 a page to judge, never
a ranch to explore. That is `evidence.py`'s entire reason to exist.

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

## Two credentials, one call path

`resolve_provider()` picks first-party Anthropic when a key exists and **Bedrock**
otherwise, scoping the model id to `us.anthropic.…` for the Bedrock path only, so
`TIER2_MODEL` stays the first-party spelling in `.env`. `AsyncAnthropic` and
`AsyncAnthropicBedrock` expose an identical `messages.create`, so provider choice is a
constructor decision and nothing downstream changes shape.

**The Bedrock path is right for a supervised M2 and wrong for M4.** It rides this
session's temporary AWS credentials, which expire; a continuous loop needs a real
`ANTHROPIC_API_KEY`. When they expire, the failure is data (`ExpiredTokenException` in
`ModelResponse.error`, a `no_answer` work order), not an exception.

## `THINKING_BUDGET`, and the two ways to misconfigure it

`{"none": 0, "low": 2048, "medium": 6144, "high": 12288}`, keyed by `reasoning_effort`.
`none` **omits the thinking block entirely** rather than sending a zero budget, which the
API rejects. Two traps beyond that: `temperature` may not be set at all while thinking is
on, so the knob is only offered when it is off; and `max_tokens` has to be raised **above**
`budget_tokens`, or the answer is all reasoning and no content. `call_tier2` adds the
budget to `max_tokens` rather than sharing it.

## embeddings.py is a seam, not a feature

Each sensing world's SOP set is a handful of rules. Loading the whole SOP file into the
prompt beats retrieving over it, and pretending otherwise is the cleverer option rather
than the simpler one. The file exists so retrieval has an obvious home when the corpus
outgrows a prompt. Do not fill it in before that happens.
