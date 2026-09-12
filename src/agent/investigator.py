"""The investigator: a bounded LangGraph tool loop on the local model. Lands at M10.

The third model job, and the one M2 deleted on purpose. `evidence.py` assembles the page in
code so a model judges instead of navigating, and that decision stands: every incident is
still judged once, on one page, with no tool loop. What M7 measured is that the local judge
sometimes cannot finish on that page and says so (`insufficient_information`), listing what it
lacked. At M7 that sent the packet to Opus, who wrote around the gap, and the cheap path paid
twice. This module is the other answer: hand the local model the read tools in its agent's
slice, through `langchain-mcp-adapters` and a LangGraph ReAct loop, let it fetch exactly what
the judge listed, and judge the enriched page once more at Tier 1.

**The loop navigates; it never judges.** Severity is `triage.py`'s before and after; the facts
block the loop produces is rendered by code from the raw tool results, never from the model's
summary of them; the brief forbids grading and the re-judge runs the same rails as the first.

**It is bounded three ways, and a thrashing loop is a log line, not a bill.** A tool-call
ceiling (`INVESTIGATOR_MAX_STEPS`) enforced in the interceptor before the call goes out, a
wall-clock deadline (`INVESTIGATOR_DEADLINE_S`) around the whole stream, and LangGraph's own
`recursion_limit` as the backstop. At any limit **everything fetched is discarded** and the
order escalates on the original page exactly as it did before M10. Scott's decision at the M10
check-in: a loop that did not finish is not trusted to have fetched the right things, and the
half-page it produced is not a fact a judge should see. What it fetched is still in the log.

**No write tool ever reaches the loop, twice over.** The tools the adapter loads are filtered
to `bound_tools_for(agent)`, which subtracts `WRITE_TOOLS`; and the interceptor calls
`assert_callable(name, agent=agent)` on every call anyway, so a write by name is refused before
it leaves this process. A discovered write is still a `proposed_write` in the work order and a
human still answers at the M6 gate. `gate.py` is untouched.

The MCP session is `mcp_client.ranch_session()`, the same one the catalog read uses; the Lambda
is stateless, so a session per investigation costs nothing to open. `ranch://sensors/map` is a
resource and the adapter surfaces tools only, which is fine here: the page already carries the
topology the loop needs.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, ToolException

from src.models.llm_client import PROVIDER_OLLAMA, build_tier1_client
from src.models.routing import TIER1
from src.prompts.agent_prompts import investigator_brief
from src.tools.allowlists import WRITE_TOOLS, WriteGateError, assert_callable, bound_tools_for
from src.tools.evidence import EvidencePacket
from src.tools.mcp_client import ranch_session
from src.utils.config import get_settings
from src.utils.logger import Stopwatch, current_tick, get_logger, log_agent_call, write_transcript

log = get_logger(__name__)

#: The outcomes, a closed vocabulary so a `jq` can count them off the tick line.
OUTCOME_ANSWERED = "answered"
OUTCOME_STEP_CEILING = "step_ceiling"
OUTCOME_DEADLINE = "deadline"
OUTCOME_NO_TOOL_CALLS = "no_tool_calls"
OUTCOME_ERROR = "error"
OUTCOMES = frozenset({OUTCOME_ANSWERED, OUTCOME_STEP_CEILING, OUTCOME_DEADLINE, OUTCOME_NO_TOOL_CALLS, OUTCOME_ERROR})

#: Per tool result, in characters. `list_sensors` is 160 rows and `list_animals` is 1,195; a loop
#: that fetches either whole would blow the page past `num_ctx` on one call. Cut here, in the
#: interceptor, so the model reads the same truncated text the page will carry.
RESULT_CHARS = 4_000
#: The whole facts block, in characters. About 3,500 gemma tokens, so page (4,200 tokens at most,
#: measured at M7) plus facts plus a 2,048-token answer stays well under `num_ctx` 16,384.
FACTS_CHARS = 12_000
#: The sentence `create_react_agent` (langgraph 0.2) puts in the last message when the graph ran
#: out of steps with a tool call pending. Matched so the outcome is `step_ceiling` rather than a
#: quiet `answered`, which is what it would otherwise read as.
_NEED_MORE_STEPS = "need more steps"

JOB = "investigate"


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call as the interceptor saw it: what was asked, what came back, and whether the belt refused it."""

    tool: str
    args: dict[str, Any]
    latency_ms: int
    chars: int
    text: str
    refused: bool = False
    error: bool = False


@dataclass(frozen=True)
class Investigation:
    """The receipt of one loop. `facts` is empty for every outcome but `answered`, by decision, and
    empty for `answered` too when the loop made no useful call. Everything else is for the log."""

    outcome: str
    steps: int = 0
    tools_called: tuple[str, ...] = ()
    write_attempts: int = 0
    facts: str = ""
    facts_truncated: bool = False
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    turns: tuple[dict[str, Any], ...] = ()
    calls: tuple[ToolCallRecord, ...] = ()
    closing: str = ""
    error: str = ""

    @property
    def receipt(self) -> dict[str, Any]:
        """The four fields that land on the stored `WorkOrder`."""
        return {"investigated": True, "investigation_outcome": self.outcome, "investigation_steps": self.steps, "investigation_tools": self.tools_called}


class Belt:
    """The tool-call interceptor: the allowlist belt, the step ceiling, the truncation, the record.

    `langchain-mcp-adapters` calls it with the request and the next handler and it decides whether
    the call goes out at all. Three refusals, each a `ToolException` so LangGraph's `ToolNode`
    turns it into an error message the model reads and the loop continues rather than crashing:
    a write tool by name (`assert_callable`, which is what stands between a model and the ranch
    in every other path too), a call past the ceiling, and nothing else. Every call, refused or
    not, is one `ToolCallRecord`.
    """

    def __init__(self, *, agent: str, max_steps: int) -> None:
        self.agent = agent
        self.max_steps = max_steps
        self.records: list[ToolCallRecord] = []
        self.ceiling_hit = False

    @property
    def steps(self) -> int:
        return sum(1 for r in self.records if not r.refused)

    @property
    def write_attempts(self) -> int:
        return sum(1 for r in self.records if r.refused and r.tool in WRITE_TOOLS)

    async def __call__(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        name, args = str(request.name), dict(request.args or {})
        watch = Stopwatch()
        try:
            assert_callable(name, agent=self.agent)
        except WriteGateError as exc:
            self.records.append(ToolCallRecord(tool=name, args=args, latency_ms=watch.ms, chars=0, text="", refused=True))
            log.warning("investigator_write_refused", agent=self.agent, tool=name, hint="a write is proposed in the work order and a human answers at the gate; the loop never performs one")
            raise ToolException(f"{name} changes the ranch and is not available here. Do not call it again; a change is proposed in the work order, never performed by you.") from exc
        if self.steps >= self.max_steps:
            self.ceiling_hit = True
            self.records.append(ToolCallRecord(tool=name, args=args, latency_ms=watch.ms, chars=0, text="", refused=True))
            log.warning("investigator_step_ceiling", agent=self.agent, tool=name, max_steps=self.max_steps)
            raise ToolException("step ceiling reached. Stop calling tools and reply with your receipt now.")

        result = await handler(request)
        text = _result_text(result)
        chars = len(text)
        is_error = bool(getattr(result, "isError", False))
        if chars > RESULT_CHARS:
            result = _truncate_result(result, text[:RESULT_CHARS] + f"\n... [truncated by the investigator at {RESULT_CHARS} of {chars} characters; ask for less]")
            text = _result_text(result)
        self.records.append(ToolCallRecord(tool=name, args=args, latency_ms=watch.ms, chars=chars, text=text, error=is_error))
        log.info("investigator_tool_call", agent=self.agent, tool=name, args=sorted(args), latency_ms=watch.ms, chars=chars, error=is_error, step=self.steps, max_steps=self.max_steps)
        return result


def _result_text(result: Any) -> str:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        return "\n".join(str(getattr(c, "text", "")) for c in content if getattr(c, "text", None) is not None)
    return str(content or "")


def _truncate_result(result: Any, text: str) -> Any:
    """A copy of the MCP `CallToolResult` with its text content replaced. Pydantic on the MCP side,
    so `model_copy`; anything else (an interceptor already returned a `ToolMessage`) is left alone."""
    from mcp.types import TextContent

    if hasattr(result, "model_copy"):
        return result.model_copy(update={"content": [TextContent(type="text", text=text)]})
    return result


def render_facts(records: list[ToolCallRecord] | tuple[ToolCallRecord, ...]) -> tuple[str, bool]:
    """The block appended to the page, rendered by code from the raw tool results. `(text, truncated)`.

    Only successful, unrefused calls with content contribute. The model's own closing text never
    does: a model's summary of a tool result is prose, and the tool result is the fact.
    """
    useful = [r for r in records if not r.refused and not r.error and r.text.strip()]
    if not useful:
        return "", False
    lines: list[str] = [f"## What the investigator fetched, on the judge's request ({len(useful)} tool call{'s' if len(useful) != 1 else ''}, ranch records, not opinion)", ""]
    lines.append("  (The first judgment listed facts this page lacked. A bounded tool loop fetched what follows from the ranch's own APIs, in this same tick. Severity above is unchanged and is not yours to change. Each result is one read at the moment of the fetch.)")
    used, truncated = len("\n".join(lines)), False
    for r in useful:
        block = f"\n### {r.tool} {json.dumps(r.args, sort_keys=True, default=str)}\n{r.text.strip()}"
        if used + len(block) > FACTS_CHARS:
            truncated = True
            lines.append(f"\n  (further results dropped: the facts block is capped at {FACTS_CHARS} characters so the page still fits the model)")
            break
        lines.append(block)
        used += len(block)
    return "\n".join(lines), truncated


def build_model() -> Any:
    """The seam. One place a Tier-1 actor is constructed for the loop, through `llm_client` like every
    other actor (`src/models/CLAUDE.md`). No `format=`: the loop navigates and the judge judges, and a
    grammar-constrained model cannot emit a tool call. Tests replace this with a fake."""
    return build_tier1_client(reasoning=False)


def _turn_receipt(message: AIMessage) -> dict[str, Any]:
    meta: dict[str, Any] = dict(getattr(message, "response_metadata", None) or {})
    usage: dict[str, Any] = dict(getattr(message, "usage_metadata", None) or {})
    return {
        "finish_reason": str(meta.get("done_reason") or meta.get("finish_reason") or "unknown"),
        "tool_calls": len(message.tool_calls or []),
        "input_tokens": int(usage.get("input_tokens") or meta.get("prompt_eval_count") or 0),
        "output_tokens": int(usage.get("output_tokens") or meta.get("eval_count") or 0),
        "model": str(meta.get("model") or ""),
        "text": message.content if isinstance(message.content, str) else "".join(str(p) for p in message.content),
    }


def _messages_in(update: Any) -> list[BaseMessage]:
    """The messages inside one `stream_mode="updates"` chunk, whichever node produced them."""
    out: list[BaseMessage] = []
    if isinstance(update, dict):
        for node_update in update.values():
            if isinstance(node_update, dict):
                for m in node_update.get("messages") or []:
                    if isinstance(m, BaseMessage):
                        out.append(m)
    return out


async def investigate(packet: EvidencePacket, *, agent: str, unknowns: tuple[str, ...] | list[str], page: str) -> Investigation:
    """One bounded loop for one thin page. Never raises; every failure is an `Investigation` with its outcome.

    Steps, in order: open the ranch session, load the tools through the adapter and keep only the
    agent's read slice, build the graph on the Tier-1 model, stream it under the deadline collecting
    every model turn and every tool result, then decide the outcome. The facts block exists only
    for `answered`.
    """
    settings = get_settings()
    max_steps, deadline = int(settings.investigator_max_steps), float(settings.investigator_deadline_s)
    belt = Belt(agent=agent, max_steps=max_steps)
    turns: list[dict[str, Any]] = []
    watch = Stopwatch()
    outcome, error, closing = OUTCOME_ANSWERED, "", ""
    tools_offered: list[str] = []
    try:
        from langchain_mcp_adapters.tools import load_mcp_tools
        from langgraph.errors import GraphRecursionError
        from langgraph.prebuilt import create_react_agent

        allowed = bound_tools_for(agent)
        async with ranch_session() as session:
            loaded: list[BaseTool] = await load_mcp_tools(session, tool_interceptors=[belt])
            tools = [t for t in loaded if t.name in allowed]
            tools_offered = sorted(t.name for t in tools)
            withheld = sorted(t.name for t in loaded if t.name not in allowed)
            if any(t.name in WRITE_TOOLS for t in tools):  # pragma: no cover - `bound_tools_for` subtracts WRITE_TOOLS; this is the rail saying so
                raise RuntimeError(f"a write tool reached the investigator's tool list: {sorted(t.name for t in tools if t.name in WRITE_TOOLS)}")
            log.info("investigator_tools", agent=agent, offered=tools_offered, withheld=len(withheld), unknowns=len(unknowns))
            if not tools:
                raise RuntimeError(f"no read tool in {agent}'s slice was offered by the server")

            # The brief carries the judge's list; the user turn is the page, verbatim, the same string
            # the judge read. One rendering, as everywhere: what the loop saw is what a human can print.
            graph = create_react_agent(build_model(), tools, prompt=investigator_brief(agent, unknowns, tools_offered))
            try:
                async with asyncio.timeout(deadline):
                    async for update in graph.astream({"messages": [HumanMessage(content=page)]}, config={"recursion_limit": 2 * max_steps + 3}, stream_mode="updates"):
                        for message in _messages_in(update):
                            if isinstance(message, AIMessage):
                                turn = _turn_receipt(message)
                                turns.append(turn)
                                closing = turn["text"] if not turn["tool_calls"] else closing
                                log_agent_call(agent=agent, tier=TIER1, provider=PROVIDER_OLLAMA, model=turn["model"] or settings.tier1_model, finish_reason=turn["finish_reason"], latency_ms=0, input_tokens=turn["input_tokens"], output_tokens=turn["output_tokens"], tool_calls=turn["tool_calls"], job=JOB, turn=len(turns), incident_key=packet.incident.key)
                            elif isinstance(message, ToolMessage) and getattr(message, "status", "") == "error":
                                log.info("investigator_tool_error_seen", agent=agent, tool=message.name, detail=str(message.content)[:200])
            except TimeoutError:
                outcome = OUTCOME_DEADLINE
            except GraphRecursionError:
                outcome = OUTCOME_STEP_CEILING
    except Exception as exc:
        outcome, error = OUTCOME_ERROR, f"{type(exc).__name__}: {exc}"
        log.error("investigator_failed", agent=agent, incident=packet.incident.key, error=error)

    if outcome == OUTCOME_ANSWERED:
        if belt.ceiling_hit or _NEED_MORE_STEPS in closing.lower():
            outcome = OUTCOME_STEP_CEILING
        elif belt.steps == 0:
            outcome = OUTCOME_NO_TOOL_CALLS

    # Scott's decision at the M10 check-in: a loop that did not finish on its own leaves nothing on
    # the page. What it fetched is in the records and the transcript; the judge never sees it.
    facts, facts_truncated = render_facts(belt.records) if outcome == OUTCOME_ANSWERED else ("", False)
    result = Investigation(
        outcome=outcome,
        steps=belt.steps,
        tools_called=tuple(r.tool for r in belt.records if not r.refused),
        write_attempts=belt.write_attempts,
        facts=facts,
        facts_truncated=facts_truncated,
        latency_ms=watch.ms,
        input_tokens=sum(int(t["input_tokens"]) for t in turns),
        output_tokens=sum(int(t["output_tokens"]) for t in turns),
        turns=tuple(turns),
        calls=tuple(belt.records),
        closing=closing,
        error=error,
    )
    log.info(
        "investigation",
        agent=agent,
        incident=packet.incident.key,
        outcome=result.outcome,
        steps=result.steps,
        tools=list(result.tools_called),
        write_attempts=result.write_attempts,
        turns=len(turns),
        facts_chars=len(facts),
        facts_truncated=facts_truncated,
        latency_ms=result.latency_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        error=error or None,
    )
    write_transcript(tick=current_tick(), agent=agent, name=f"{packet.incident.key}.investigation", payload={"incident_key": packet.incident.key, "agent": agent, "unknowns": list(unknowns), "tools_offered": tools_offered, "outcome": outcome, "turns": turns, "calls": [{"tool": r.tool, "args": r.args, "latency_ms": r.latency_ms, "chars": r.chars, "refused": r.refused, "error": r.error, "text": r.text} for r in belt.records], "closing": closing, "facts": facts, "error": error})
    return result


__all__ = [
    "FACTS_CHARS",
    "JOB",
    "OUTCOMES",
    "OUTCOME_ANSWERED",
    "OUTCOME_DEADLINE",
    "OUTCOME_ERROR",
    "OUTCOME_NO_TOOL_CALLS",
    "OUTCOME_STEP_CEILING",
    "RESULT_CHARS",
    "Belt",
    "Investigation",
    "ToolCallRecord",
    "build_model",
    "investigate",
    "render_facts",
]
