"""The provider registry, and the receipts. Arrives at M2 with Tier 2; Tier 1 lands at M7.

Four things this module exists to get right, all of them paid for once already and all of
them written up in `src/models/CLAUDE.md`:

  * **`ChatOllama` from `langchain-ollama`, never `ChatOpenAI` pointed at the shim.** The
    native `/api/chat` endpoint is the only one that honors `num_ctx`; the shim ignores it
    silently, which presents as "small models are too weak."
  * **`num_ctx` set explicitly to 16384.** Never a default.
  * **`reasoning_effort` is an explicit per-call argument, never an ambient env var.** A
    knob that silently changes verdicts belongs where the actor is built.
  * **`finish_reason` is logged on every call, before validation runs.** `"length"` means
    the model never got to answer and `"stop"` means it answered badly. They present
    identically in the output text and one is a config bug.

Tier 1 arrived at M7, after four phases of Tier-2 baseline, and it is `call_tier1` below:
`ChatOllama` on the native endpoint, `num_ctx` explicit, `reasoning=False` passed per call,
`format=` carrying the JSON schema so the output is grammar-constrained rather than promised,
and `done_reason` logged as `finish_reason` before anything parses the text. Which tier a job
gets, and what escalates, is `src/models/routing.py`'s; this module only knows how to call.

## Two credentials, one call path

`ANTHROPIC_API_KEY` is one way to reach Opus and it is not the only one. AWS Bedrock serves
the same models to callers who already have AWS credentials, which in an enterprise is more
often true than an Anthropic key being provisioned. Both are served by the same SDK
(`AsyncAnthropic` and `AsyncAnthropicBedrock` expose an identical `messages.create`), so
provider selection is a constructor choice and nothing downstream changes shape.

Resolution order, and the resolved provider is logged on every call so a receipt never
leaves you guessing which credential answered:

  1. an explicit `api_key` argument,
  2. `ANTHROPIC_API_KEY` from the environment,
  3. ambient AWS credentials, via Bedrock.

Bedrock prefixes the model id by region scope (`us.anthropic.claude-opus-5`); the
first-party API does not (`claude-opus-5`). `TIER2_MODEL` stays the first-party spelling
and the prefix is applied here, so one setting is right for both providers.

**The Bedrock path is right for a supervised M2 and wrong for M4.** Those credentials are
temporary session credentials that expire, and an unattended loop that wakes at 3am to a
403 has no way to renew them. M4 wants a real key in `.env`.

## Why the raw SDK and not `ChatAnthropic`

M2 has no tool loop by design (`evidence.py` is the whole point: the model judges one page).
A single structured call needs no graph, and the SDK hands back `stop_reason` and a usage
receipt directly rather than buried in `response_metadata`. `langchain-anthropic` also has
no Bedrock path at all - that is a separate package - so routing both credentials through
one LangChain class is not available anyway. When M3's `create_react_agent` workers need a
LangChain actor, it gets built **here**, next to `resolve_provider`, for the reason in
`src/models/CLAUDE.md`: one place to construct an actor, or the `reasoning_effort` bug
happens a second time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from src.models.routing import TIER1, TIER2  # re-exported: M4 call sites and tests import it from here
from src.models.routing import cost_usd as cost_usd
from src.utils.config import get_settings
from src.utils.logger import Stopwatch, get_logger, log_agent_call

log = get_logger(__name__)

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_BEDROCK = "bedrock"

#: Bedrock's inference profiles are region-scoped and the model id carries the scope.
#: Cross-region is what exists for Opus, so a bare `anthropic.claude-opus-5` is rejected
#: with a validation error that reads like the model does not exist.
BEDROCK_SCOPE = "us"

#: `reasoning_effort` maps to an extended-thinking budget, in tokens. `"none"` omits the
#: `thinking` block entirely rather than sending a zero budget, which the API rejects.
#:
#: The default is `"none"` on purpose and it is the measured rule from
#: `src/models/CLAUDE.md`: turning thinking off is free exactly when the model is not the
#: one classifying. `triage.py` owns severity, so a water_feed judge is writing prose about
#: a verdict it was handed, and that is the case where reasoning off cost nothing. Turn it
#: up for a job that genuinely decides something, and say so at the call site.
THINKING_BUDGET: dict[str, int] = {"none": 0, "low": 2_048, "medium": 6_144, "high": 12_288}

DEFAULT_MAX_TOKENS = 2_048

#: How long Ollama keeps the Tier-1 weights resident after a call. The default is five
#: minutes, which is exactly the tick cadence, so the model would unload and reload on every
#: tick. Explicit and per call, like everything else that changes what a call costs.
OLLAMA_KEEP_ALIVE = "30m"

# M4 priced every call at one assumed rate, `ASSUMED_RATE_USD_PER_M`. M7 replaced it with
# `routing.PRICE_TABLE`, per model, and `cost_usd` is re-exported from here so the M4 call
# sites and their tests keep one import path.


class Tier2Unavailable(RuntimeError):
    """No usable credential. Raised at construction, never mid-tick from a call site."""


class Tier1Unavailable(RuntimeError):
    """`langchain-ollama` is missing or the base URL is empty. Raised at construction, never mid-tick."""


@dataclass(frozen=True)
class ModelResponse:
    """One call's outcome, including the failures. Errors are data here too.

    `finish_reason` is the field this whole module is arranged around. It is populated on
    the failure paths as well (`error` / `transport_error`), because a call that never
    reached the model still has to leave a line in `agent.jsonl` saying so: a missing line
    is indistinguishable from a tick that never ran the agent at all.
    """

    provider: str
    model: str
    finish_reason: str
    tier: int = TIER2
    text: str = ""
    payload: dict[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str = ""
    thinking: str = ""
    raw_content_types: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True only for a complete answer. `max_tokens` is a truncated one, not an answer."""
        return not self.error and self.finish_reason in {"stop", "end_turn", "tool_use", "stop_sequence"}

    @property
    def truncated(self) -> bool:
        return self.finish_reason in {"length", "max_tokens"}

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.input_tokens, self.output_tokens, model=self.model, tier=self.tier)


# --------------------------------------------------------------------------- #
# provider resolution
# --------------------------------------------------------------------------- #
def resolve_provider(api_key: str = "") -> tuple[str, str]:
    """`(provider, model_id)`, without constructing anything or touching the network.

    Separate from `build_client` so a test and a `--handshake` can both ask "which
    credential would answer?" without paying for a client, and so the answer appears in a
    log line as a value rather than as a side effect of a successful call.
    """
    settings = get_settings()
    model = settings.tier2_model
    if api_key or settings.anthropic_api_key:
        return PROVIDER_ANTHROPIC, model
    return PROVIDER_BEDROCK, model if model.startswith(f"{BEDROCK_SCOPE}.") else f"{BEDROCK_SCOPE}.anthropic.{model}"


@lru_cache(maxsize=2)
def _client_for(provider: str, api_key: str) -> Any:
    """Cached per (provider, key). The SDK client holds a connection pool worth reusing.

    Imported inside the function, not at module scope. `anthropic[bedrock]` pulls botocore,
    and an import error for a credential path this run is not using should not stop the
    module from loading for the path it is.
    """
    if provider == PROVIDER_ANTHROPIC:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=api_key)

    try:
        from anthropic import AsyncAnthropicBedrock
    except ImportError as exc:  # pragma: no cover - dependency, not logic
        raise Tier2Unavailable("Bedrock is the resolved provider but the SDK's bedrock extra is missing. `pip install 'anthropic[bedrock]'`.") from exc
    import os

    return AsyncAnthropicBedrock(aws_region=os.environ.get("AWS_REGION") or "us-east-1")


def build_client(api_key: str = "") -> tuple[Any, str, str]:
    """`(client, provider, model_id)`. The single place a Tier-2 actor is constructed."""
    provider, model = resolve_provider(api_key)
    key = api_key or get_settings().anthropic_api_key
    if provider == PROVIDER_ANTHROPIC and not key:  # pragma: no cover - resolve_provider guarantees it
        raise Tier2Unavailable("no ANTHROPIC_API_KEY and no AWS credentials")
    return _client_for(provider, key), provider, model


# --------------------------------------------------------------------------- #
# the one call
# --------------------------------------------------------------------------- #
def _extract(message: Any, schema_name: str) -> tuple[str, dict[str, Any] | None, str, tuple[str, ...]]:
    """`(text, payload, thinking, content_types)` out of a Messages response.

    A response is a list of blocks, not a string. Reaching for `content[0].text` works
    until the day thinking is on and block zero is a `thinking` block, at which point it
    raises an AttributeError that reads like an SDK problem.
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    payload: dict[str, Any] | None = None
    kinds: list[str] = []

    for block in getattr(message, "content", []) or []:
        kind = getattr(block, "type", "")
        kinds.append(str(kind))
        if kind == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif kind == "thinking":
            thinking_parts.append(getattr(block, "thinking", "") or "")
        elif kind == "tool_use" and getattr(block, "name", "") == schema_name:
            raw = getattr(block, "input", None)
            if isinstance(raw, dict):
                payload = raw
            elif isinstance(raw, str):  # a truncated tool_use arrives as a partial JSON string
                try:
                    parsed = json.loads(raw)
                    payload = parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    payload = None

    return "\n".join(p for p in text_parts if p).strip(), payload, "\n".join(thinking_parts).strip(), tuple(kinds)


async def call_tier2(
    *,
    agent: str,
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
    schema_name: str = "emit",
    schema_description: str = "Return the result in this shape.",
    reasoning_effort: str = "none",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
    api_key: str = "",
    incident_key: str | None = None,
) -> ModelResponse:
    """One Tier-2 call. No retry, no fallback, one log line, always.

    `schema` forces a tool call, which is what makes the output parseable without asking a
    model to promise it will emit clean JSON. The forced-tool path keeps `finish_reason`
    honest: a normal answer stops at `tool_use`, and a schema too big for `max_tokens`
    stops at `max_tokens` with a half-written argument object. Those are a fine answer and
    a config bug respectively, and without this field they are the same failed parse.

    **No retry, on purpose.** A retry that succeeds on the second attempt hides the first,
    and the first is the measurement. Retry policy belongs to the caller with the deadline,
    the same rule the tool layer follows.
    """
    client, provider, model = build_client(api_key)

    effort = reasoning_effort if reasoning_effort in THINKING_BUDGET else "none"
    if effort != reasoning_effort:
        log.warning("unknown_reasoning_effort", requested=reasoning_effort, using="none", allowed=sorted(THINKING_BUDGET))
    budget = THINKING_BUDGET[effort]

    kwargs: dict[str, Any] = {
        "model": model,
        # Room for the answer on top of the thinking budget, not shared with it. Sizing
        # `max_tokens` at or below `budget_tokens` is the classic way to get a response
        # that is pure reasoning and no content.
        "max_tokens": max_tokens + budget,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if budget:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
    elif temperature is not None:
        # Extended thinking requires temperature 1, so the knob is only offered when
        # thinking is off. Sending both is a 400 that reads like an unrelated parameter.
        kwargs["temperature"] = temperature
    if schema is not None:
        kwargs["tools"] = [{"name": schema_name, "description": schema_description, "input_schema": schema}]
        kwargs["tool_choice"] = {"type": "tool", "name": schema_name}

    watch = Stopwatch()
    try:
        message = await client.messages.create(**kwargs)
    except Exception as exc:
        # Broad on purpose: an auth failure, an expired session token, a rate limit, and a
        # DNS failure are all the same thing to this layer, which is "no answer, say so in
        # one line." Classification without a retry policy to feed is decoration.
        detail = f"{type(exc).__name__}: {exc}"
        response = ModelResponse(provider=provider, model=model, finish_reason="transport_error", latency_ms=watch.ms, error=detail)
        log_agent_call(
            agent=agent,
            tier=TIER2,
            provider=provider,
            model=model,
            finish_reason=response.finish_reason,
            latency_ms=response.latency_ms,
            error=detail,
            reasoning_effort=effort,
            incident_key=incident_key,
        )
        return response

    latency_ms = watch.ms
    text, payload, thinking, kinds = _extract(message, schema_name)
    usage = getattr(message, "usage", None)
    response = ModelResponse(
        provider=provider,
        model=model,
        finish_reason=str(getattr(message, "stop_reason", "") or "unknown"),
        text=text,
        payload=payload,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        latency_ms=latency_ms,
        thinking=thinking,
        raw_content_types=kinds,
    )

    # HERE, before anything validates `payload`. A response that fails its schema check
    # still has to leave a receipt of what actually came back, or the only trace of a
    # rejected answer is the absence of a good one.
    log_agent_call(
        agent=agent,
        tier=TIER2,
        provider=provider,
        model=model,
        finish_reason=response.finish_reason,
        latency_ms=latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        tool_calls=1 if payload is not None else 0,
        reasoning_effort=effort,
        max_tokens=kwargs["max_tokens"],
        content_types=list(kinds),
        incident_key=incident_key,
    )
    if response.truncated:
        log.error("tier2_truncated", agent=agent, model=model, max_tokens=kwargs["max_tokens"], hint="raise max_tokens; this is a config bug, not a weak model")
    return response


# --------------------------------------------------------------------------- #
# Tier 1: the local model, M7
# --------------------------------------------------------------------------- #
PROVIDER_OLLAMA = "ollama"


def build_tier1_client(*, model: str = "", num_ctx: int | None = None, max_tokens: int = DEFAULT_MAX_TOKENS, schema: dict[str, Any] | None = None, reasoning: bool = False, temperature: float | None = None) -> Any:
    """The single place a Tier-1 actor is constructed. Every trap from `src/models/CLAUDE.md`, in one constructor.

    `ChatOllama`, never `ChatOpenAI` at the compatibility shim: the shim silently drops
    `num_ctx`, which once produced an entire investigation that looked like "small models are
    too weak" and was a 4,096-token default. `num_ctx` is always passed, from `OLLAMA_NUM_CTX`,
    never left to the server. `reasoning` is an explicit argument here and an explicit argument
    at the call site above it, never read from the environment. `format=` carries the JSON
    schema so the server constrains decoding to it, which is the Ollama-side equivalent of the
    forced tool call: the shape is enforced rather than requested, and `done_reason` stays
    honest (`stop` answered, `length` ran out) independent of whether the text parses.

    Imported inside the function so a machine without `langchain-ollama` still loads this
    module for the Tier-2 path it is using.
    """
    settings = get_settings()
    if not settings.ollama_base_url:
        raise Tier1Unavailable("OLLAMA_BASE_URL is empty")
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:  # pragma: no cover - dependency, not logic
        raise Tier1Unavailable("Tier 1 needs `langchain-ollama`; `pip install langchain-ollama`") from exc

    kwargs: dict[str, Any] = {
        "model": model or settings.tier1_model,
        "base_url": settings.ollama_base_url,
        "num_ctx": int(num_ctx or settings.ollama_num_ctx),
        "num_predict": max_tokens,
        "reasoning": reasoning,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }
    if schema is not None:
        kwargs["format"] = schema
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatOllama(**kwargs)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """The constrained output as a dict, or `None`. A grammar-constrained answer that still fails
    to parse is a truncation in practice, and `finish_reason` says so separately."""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def call_tier1(
    *,
    agent: str,
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
    reasoning_effort: str = "none",
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
    incident_key: str | None = None,
) -> ModelResponse:
    """One Tier-1 call against Ollama. No retry, no fallback here, one log line, always.

    The escalation to Tier 2 is the caller's (`workers.judge_packet`), on the reasons in
    `routing.escalation_reason`, so this function stays the same shape as `call_tier2`: it
    reports what came back and never decides what to do about it.

    `reasoning_effort` is honoured as a boolean here: Ollama's `think` is on or off, so anything
    other than `"none"` turns thinking on and the budget is the server's. The rule from the top
    of `src/models/CLAUDE.md` says it stays `"none"` for the work order, and the argument is
    explicit so that a different job can say otherwise at its own call site.

    **The one config bug this function can catch itself is the context window.** Ollama does not
    fail a prompt longer than `num_ctx`; it truncates from the front and answers about the rest,
    which for this repo's packets means the model sees a page whose SOP is missing and cites
    rules it never read. `prompt_eval_count` comes back on every answer, so it is compared to
    `num_ctx` and logged as `tier1_context_full` when it is within a hundred tokens of the ceiling.
    """
    settings = get_settings()
    model = settings.tier1_model
    num_ctx = int(settings.ollama_num_ctx)
    think = reasoning_effort != "none"

    try:
        client = build_tier1_client(model=model, num_ctx=num_ctx, max_tokens=max_tokens, schema=schema, reasoning=think, temperature=temperature)
    except Tier1Unavailable as exc:
        response = ModelResponse(provider=PROVIDER_OLLAMA, model=model, finish_reason="transport_error", tier=TIER1, error=f"{type(exc).__name__}: {exc}")
        log_agent_call(agent=agent, tier=TIER1, provider=PROVIDER_OLLAMA, model=model, finish_reason=response.finish_reason, latency_ms=0, error=response.error, reasoning_effort=reasoning_effort, num_ctx=num_ctx, incident_key=incident_key)
        return response

    from langchain_core.messages import HumanMessage, SystemMessage

    watch = Stopwatch()
    try:
        message = await client.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    except Exception as exc:
        # Same breadth as Tier 2, same reason: a dead Ollama, a missing model, and a socket
        # timeout are all "no answer, say so in one line" to this layer.
        detail = f"{type(exc).__name__}: {exc}"
        response = ModelResponse(provider=PROVIDER_OLLAMA, model=model, finish_reason="transport_error", tier=TIER1, latency_ms=watch.ms, error=detail)
        log_agent_call(agent=agent, tier=TIER1, provider=PROVIDER_OLLAMA, model=model, finish_reason=response.finish_reason, latency_ms=response.latency_ms, error=detail, reasoning_effort=reasoning_effort, num_ctx=num_ctx, incident_key=incident_key)
        return response

    latency_ms = watch.ms
    meta: dict[str, Any] = dict(getattr(message, "response_metadata", None) or {})
    usage: dict[str, Any] = dict(getattr(message, "usage_metadata", None) or {})
    text = message.content if isinstance(message.content, str) else "".join(str(part) for part in message.content)
    finish_reason = str(meta.get("done_reason") or "unknown")
    payload = _parse_json_object(text) if schema is not None else None
    input_tokens = int(usage.get("input_tokens") or meta.get("prompt_eval_count") or 0)
    output_tokens = int(usage.get("output_tokens") or meta.get("eval_count") or 0)
    thinking = str((getattr(message, "additional_kwargs", None) or {}).get("reasoning_content") or "")

    response = ModelResponse(
        provider=PROVIDER_OLLAMA,
        model=str(meta.get("model") or model),
        finish_reason=finish_reason,
        tier=TIER1,
        text=text,
        payload=payload,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        thinking=thinking,
        raw_content_types=("json",) if payload is not None else ("text",),
    )
    # Before validation, same as Tier 2: the receipt of what came back exists whether or not
    # the rails like it.
    log_agent_call(
        agent=agent,
        tier=TIER1,
        provider=PROVIDER_OLLAMA,
        model=response.model,
        finish_reason=finish_reason,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=1 if payload is not None else 0,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        num_ctx=num_ctx,
        content_types=list(response.raw_content_types),
        incident_key=incident_key,
    )
    if response.truncated:
        log.error("tier1_truncated", agent=agent, model=response.model, max_tokens=max_tokens, hint="raise max_tokens; this is a config bug, not a weak model")
    if input_tokens and input_tokens >= num_ctx - 100:
        log.error("tier1_context_full", agent=agent, model=response.model, input_tokens=input_tokens, num_ctx=num_ctx, hint="the page is at or over num_ctx; Ollama truncates from the front and the model never saw the whole SOP. Raise OLLAMA_NUM_CTX; do not trim the SOP")
    return response
