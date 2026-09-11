"""The human gate. M6. A proposed write pauses here until a person answers, and the pause
outlives the process.

    propose  ->  [ ask: interrupt() ]  ...hours, a restart, a laptop lid...  ->  [ execute ]  ->  END

One tiny LangGraph per proposal, checkpointed in Postgres (`memory.checkpointer`). `ask` calls
`interrupt()` with the proposal and stops; the checkpointer writes the paused graph to
`sw_ops`; the tick that proposed it carries on, because the ranch does not stop being watched
while a human has not answered a question. Later, a human runs the CLI at the bottom of this
file, which resumes that one graph with `Command(resume={...decision...})`, and `execute`
performs the write (or records that it was refused) and writes the `decided` audit line.

Three rules, enforced here rather than described:

  * **Code owns everything but the pause.** The three return-path checks run in
    `workers.check` before a proposal gets near this module, the incident key is code's and
    checked again in `executor`, the audit lines are written by code, and the decision is a
    person's. LangGraph holds the pause. It decides nothing.
  * **Nothing with a side effect runs before `interrupt()`.** A node restarts from its first
    line when the graph resumes, so the `proposed` audit line is written by `propose()` outside
    the graph and `ask` does nothing but ask. The first spike measured `ask` running twice.
  * **A proposal that could not be persisted is decided, not dangled.** If the checkpointer is
    down the pair is completed with `decision="dropped"`, `decided_by="gate"`, and the tick
    holds the incident so the write is re-proposed. A `proposed` with no `decided` has exactly
    one meaning, a pause nobody answered, and that meaning is not for sale.

The audit rail (`tests/CLAUDE.md`): every `audit_id` appears exactly twice in `audit.jsonl`,
**or once while its graph is still paused.** `unpaired_audit_ids` is the check and the CLI's
`list` is what a person reads to answer the ones that are.

The CLI is the whole of what a human can do at M6, and `/ops/gate` at M8 wraps it and adds
nothing: if a decision cannot be expressed here it cannot be expressed there either.

    python -m src.agent.gate list
    python -m src.agent.gate approve <audit_id> [--by NAME]
    python -m src.agent.gate reject  <audit_id> --reason TEXT [--by NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from src.agent.memory import CheckpointerNotMigratedError, SchemaGuardError, checkpointer, resolve_store
from src.tools.allowlists import Approval, assert_callable
from src.utils.helpers import utc_now_iso
from src.utils.logger import configure_logging, get_logger, log_audit_decided, log_audit_proposed, new_audit_id

log = get_logger(__name__)

Decision = Literal["approve", "reject"]

#: The metadata every gate checkpoint carries, which is how `pending()` finds gate threads
#: among everything else the checkpointer might one day hold. Filtered on, so it is a constant.
GATE_MARKER = {"sweetwater": "gate"}


class GateState(TypedDict, total=False):
    """One proposal's whole life. Everything above `decision` is written by `propose()`; the
    rest by the human's resume and by `execute`."""

    audit_id: str
    incident_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    proposed_at: str
    tick: int
    run_id: str
    decision: str
    decided_by: str
    reason: str
    decided_at: str
    result: str
    upstream: str
    latency_to_decision_ms: int


@dataclass(frozen=True)
class WriteProposal:
    """What `executor` hands in: a checked proposal plus the code-owned facts around it."""

    incident_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    tick: int
    run_id: str


@dataclass(frozen=True)
class PendingWrite:
    """A paused graph, as the CLI lists it."""

    audit_id: str
    incident_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    proposed_at: str
    tick: int
    run_id: str

    def age_s(self, now: datetime | None = None) -> int:
        return int(((now or datetime.now(UTC)) - _parse(self.proposed_at)).total_seconds())


@dataclass(frozen=True)
class ProposeOutcome:
    audit_id: str = ""
    duplicate_of: str = ""
    dropped: bool = False

    @property
    def paused(self) -> bool:
        return bool(self.audit_id) and not self.dropped


class GateError(RuntimeError):
    """A decision that cannot be applied: no such pause, already decided, or a rejection without a reason."""


Performer = Callable[[str, dict[str, Any], Approval], Awaitable[tuple[str, str]]]


# --------------------------------------------------------------------------- #
# the graph
# --------------------------------------------------------------------------- #
def _ask(state: GateState) -> GateState:
    """Stop and wait. Nothing else: this node runs again from the top on resume."""
    answer = interrupt({"audit_id": state["audit_id"], "incident_key": state["incident_key"], "agent": state["agent"], "tool": state["tool"], "args": state["args"], "proposed_at": state["proposed_at"], "tick": state.get("tick", 0), "run_id": state.get("run_id", "")})
    return {"decision": str(answer.get("decision", "")), "decided_by": str(answer.get("decided_by", "")), "reason": str(answer.get("reason", ""))}


def _make_execute(perform: Performer) -> Callable[[GateState], Awaitable[GateState]]:
    async def _execute(state: GateState) -> GateState:
        decided_at = utc_now_iso()
        latency = int((_parse(decided_at) - _parse(state["proposed_at"])).total_seconds() * 1000)
        decision = state.get("decision", "")
        upstream = ""
        if decision == "approve":
            approval = Approval(audit_id=state["audit_id"], decided_by=state.get("decided_by", ""))
            try:
                result, upstream = await perform(state["tool"], state["args"], approval)
            except Exception as exc:  # the receipt is written whatever the wire did; never retried
                result = f"transport_{type(exc).__name__}"
                upstream = str(exc)[:200]
        else:
            result = "not_executed"
        # The second line of the pair, written here so a decision path that skipped this node
        # is a decision path that skipped its log line, which is what the audit rail catches.
        log_audit_decided(audit_id=state["audit_id"], decision=decision, decided_by=state.get("decided_by", ""), result=result, latency_to_decision_ms=latency, tool=state["tool"], incident_key=state["incident_key"], reason=state.get("reason", ""), upstream=upstream)
        return {"decided_at": decided_at, "result": result, "upstream": upstream, "latency_to_decision_ms": latency}

    return _execute


async def perform_write(tool: str, args: dict[str, Any], approval: Approval) -> tuple[str, str]:
    """The one place in this repo that performs an agent's write on the ranch. Over MCP,
    through `call_tool`, which is where `assert_callable` asks for the approval."""
    from src.tools.mcp_client import call_tool, ranch_session  # local: the CLI and the tick both reach here, tests never do

    # The belt, before a connection is opened: `ranch_session` wraps anything raised inside it
    # as an MCP outage, and a refused write is not an outage. The first planted approve, run
    # before the flip, came back `transport_McpUnavailableError` for exactly this reason.
    assert_callable(tool, approval=approval)
    async with ranch_session() as session:
        parsed = await call_tool(session, tool, args, approval=approval)
    envelope = parsed.get("error", parsed) if isinstance(parsed, dict) else None
    if isinstance(envelope, dict) and "category" in envelope:
        return f"upstream_error_{envelope.get('category')}", json.dumps(parsed)[:200]
    return "written", json.dumps(parsed)[:200]


def build_gate(saver: BaseCheckpointSaver[Any], *, perform: Performer | None = None) -> CompiledStateGraph:
    """One proposal's graph, compiled against the checkpointer that will hold its pause.
    `perform` is injected so a rail can approve a planted write without a ranch on the wire;
    resolved at call time rather than as a default so a test may patch `perform_write` too."""
    graph: StateGraph = StateGraph(GateState)
    graph.add_node("ask", _ask)
    graph.add_node("execute", _make_execute(perform or perform_write))
    graph.add_edge(START, "ask")
    graph.add_edge("ask", "execute")
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=saver)


def _config(audit_id: str, *, incident_key: str = "", tool: str = "", agent: str = "") -> RunnableConfig:
    metadata: dict[str, Any] = dict(GATE_MARKER)
    if incident_key:
        metadata.update(incident_key=incident_key, tool=tool, agent=agent)
    return {"configurable": {"thread_id": audit_id}, "metadata": metadata}


# --------------------------------------------------------------------------- #
# propose, list, decide
# --------------------------------------------------------------------------- #
async def propose(gate: CompiledStateGraph, proposal: WriteProposal) -> ProposeOutcome:
    """Pause one checked proposal for a human. Returns the audit id, or names the duplicate.

    **Re-proposing the same write for the same incident is a duplicate to suppress, not a
    second question to ask.** An `ongoing` incident is re-judged only when held, but a held one
    can be re-judged on every tick until answered, and each judgment could propose the same
    restock again. The person at the keyboard gets asked once.

    Order inside: the `proposed` line first, then the graph. If the checkpointer fails after
    the line is written, the pair is completed with `dropped` rather than left dangling, and
    the caller holds the incident so the proposal comes back next tick.
    """
    already = await pending(gate, incident_key=proposal.incident_key, tool=proposal.tool)
    if already:
        log.info("write_proposal_duplicate", incident=proposal.incident_key, tool=proposal.tool, pending_audit_id=already[0].audit_id, hint="the same write is already waiting for a human; not asked twice")
        return ProposeOutcome(duplicate_of=already[0].audit_id)

    audit_id = new_audit_id()
    proposed_at = utc_now_iso()
    log_audit_proposed(audit_id=audit_id, tool=proposal.tool, args=proposal.args, proposed_by=proposal.agent, incident_key=proposal.incident_key, tick=proposal.tick, run_id=proposal.run_id)
    state: GateState = {"audit_id": audit_id, "incident_key": proposal.incident_key, "agent": proposal.agent, "tool": proposal.tool, "args": dict(proposal.args), "proposed_at": proposed_at, "tick": proposal.tick, "run_id": proposal.run_id}
    try:
        await gate.ainvoke(state, _config(audit_id, incident_key=proposal.incident_key, tool=proposal.tool, agent=proposal.agent))
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        log.error("write_proposal_dropped", audit_id=audit_id, incident=proposal.incident_key, tool=proposal.tool, error=detail, hint="the checkpointer could not persist the pause; the incident is held and the write will be proposed again")
        log_audit_decided(audit_id=audit_id, decision="dropped", decided_by="gate", result="checkpointer_unavailable", latency_to_decision_ms=0, tool=proposal.tool, incident_key=proposal.incident_key, upstream=detail[:200])
        return ProposeOutcome(audit_id=audit_id, dropped=True)
    log.warning("write_paused", audit_id=audit_id, incident=proposal.incident_key, agent=proposal.agent, tool=proposal.tool, args=proposal.args, hint="waiting for a human: python -m src.agent.gate list")
    return ProposeOutcome(audit_id=audit_id)


async def pending(gate: CompiledStateGraph, *, incident_key: str | None = None, tool: str | None = None) -> tuple[PendingWrite, ...]:
    """Every proposal still paused, oldest first. Optionally narrowed to one incident and tool.

    Found by the metadata every gate run carries, then confirmed by asking each thread whether
    it is actually sitting on an interrupt: a decided thread's early checkpoints carry the same
    metadata, so the filter alone would list history as pending.
    """
    saver = cast(BaseCheckpointSaver[Any], gate.checkpointer)
    criteria: dict[str, Any] = dict(GATE_MARKER)
    if incident_key:
        criteria["incident_key"] = incident_key
    if tool:
        criteria["tool"] = tool
    threads: list[str] = []
    async for cp in saver.alist(None, filter=criteria):
        thread = str(cp.config["configurable"]["thread_id"])
        if thread not in threads:
            threads.append(thread)

    found: list[PendingWrite] = []
    for thread in threads:
        snapshot = await gate.aget_state({"configurable": {"thread_id": thread}})
        interrupts = [i for task in snapshot.tasks for i in task.interrupts]
        if not interrupts:
            continue
        value = cast(dict[str, Any], interrupts[0].value)
        found.append(PendingWrite(audit_id=thread, incident_key=str(value.get("incident_key", "")), agent=str(value.get("agent", "")), tool=str(value.get("tool", "")), args=dict(value.get("args") or {}), proposed_at=str(value.get("proposed_at", "")), tick=int(value.get("tick") or 0), run_id=str(value.get("run_id", ""))))
    return tuple(sorted(found, key=lambda p: (p.proposed_at, p.audit_id)))


async def decide(gate: CompiledStateGraph, *, audit_id: str, decision: Decision, decided_by: str, reason: str = "") -> GateState:
    """Resume one paused graph with a human's answer. Returns the finished state.

    Refuses rather than guesses: an unknown id, an already-decided id, and a rejection with no
    reason are three different `GateError`s. A resume on a finished thread would otherwise be a
    silent no-op that looks exactly like a decision.
    """
    if decision == "reject" and not reason.strip():
        raise GateError("a rejection needs a reason; the crew reads it and so does the auditor")
    if not decided_by.strip():
        raise GateError("a decision needs a name in decided_by")
    config: RunnableConfig = {"configurable": {"thread_id": audit_id}}
    snapshot = await gate.aget_state(config)
    if not snapshot.values:
        raise GateError(f"no proposal with audit_id {audit_id}")
    if not any(task.interrupts for task in snapshot.tasks):
        prior = cast(GateState, snapshot.values)
        raise GateError(f"{audit_id} was already decided: {prior.get('decision', '?')} by {prior.get('decided_by', '?')} at {prior.get('decided_at', '?')} ({prior.get('result', '?')})")
    values = cast(GateState, snapshot.values)
    final = await gate.ainvoke(Command(resume={"decision": decision, "decided_by": decided_by.strip(), "reason": reason.strip()}), _config(audit_id, incident_key=values.get("incident_key", ""), tool=values.get("tool", ""), agent=values.get("agent", "")))
    log.info("write_decided", audit_id=audit_id, decision=decision, decided_by=decided_by, result=final.get("result"), tool=values.get("tool"), incident=values.get("incident_key"))
    return cast(GateState, final)


# --------------------------------------------------------------------------- #
# the rail
# --------------------------------------------------------------------------- #
def unpaired_audit_ids(lines: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """`{audit_id: count}` for every id that does not appear exactly twice.

    The rail is: this dict's keys, minus the ids `pending()` still lists, is empty. An id here
    that is not pending is a decision path that skipped its log line, or a proposal that was
    logged twice.
    """
    counts: dict[str, int] = {}
    for line in lines:
        audit_id = line.get("audit_id")
        # Only the two audit phases count. A console line that mentions an audit id (`write_paused`,
        # `write_decided`) is commentary, not a receipt, and the rail is about receipts.
        if audit_id and line.get("phase") in ("proposed", "decided"):
            counts[str(audit_id)] = counts.get(str(audit_id), 0) + 1
    return {k: n for k, n in counts.items() if n != 2}


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #
def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


async def _cmd_list(args: argparse.Namespace) -> int:
    target = resolve_store()
    async with checkpointer(target) as saver:
        gate = build_gate(saver)
        items = await pending(gate)
    print(f"{len(items)} write{'s' if len(items) != 1 else ''} waiting for a human ({target.name})")
    for p in items:
        rendered = ", ".join(f"{k}={v!r}" for k, v in sorted(p.args.items()))
        print(f"  {p.audit_id}  {p.age_s() // 60:>4} min  {p.agent:<14} {p.incident_key:<40} {p.tool}({rendered})")
    return 0


async def _cmd_decide(args: argparse.Namespace, decision: Decision) -> int:
    target = resolve_store()
    async with checkpointer(target) as saver:
        gate = build_gate(saver)
        final = await decide(gate, audit_id=args.audit_id, decision=decision, decided_by=args.by, reason=getattr(args, "reason", "") or "")
    print(f"  {args.audit_id}: {decision} by {args.by} -> {final.get('result')} after {final.get('latency_to_decision_ms', 0) // 1000}s" + (f" ({final.get('upstream')})" if final.get("upstream") else ""))
    return 0 if decision == "reject" or final.get("result") == "written" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.agent.gate", description="List, approve, and reject the writes agents have proposed. The human half of the gate.")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("list", help="every proposal still waiting for a person").set_defaults(func=_cmd_list)
    who = getpass.getuser()
    p_approve = subs.add_parser("approve", help="perform one proposed write on the ranch")
    p_approve.add_argument("audit_id")
    p_approve.add_argument("--by", default=who, help=f"who decided (default: {who})")
    p_approve.set_defaults(func=lambda a: _cmd_decide(a, "approve"))
    p_reject = subs.add_parser("reject", help="refuse one proposed write, with a reason")
    p_reject.add_argument("audit_id")
    p_reject.add_argument("--reason", required=True)
    p_reject.add_argument("--by", default=who, help=f"who decided (default: {who})")
    p_reject.set_defaults(func=lambda a: _cmd_decide(a, "reject"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        return int(asyncio.run(args.func(args)))
    except (GateError, CheckpointerNotMigratedError, SchemaGuardError) as exc:
        print(f"refused: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover - the CLI entry point
    raise SystemExit(main())
