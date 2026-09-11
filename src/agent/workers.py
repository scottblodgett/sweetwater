"""The sub-agents that spend money. Four responders as of M3, and Tier 2 for all of them.

M2 built one, `water_feed`, to prove the shape end to end against the sharpest finding on the
ranch ("the tank at Alkali Flat is dry"). M3 generalizes it: `run_agent` is that function with
the agent name as an argument, and `fan_out` runs the four of them under one ceiling. The
tier cascade still arrives at M7, with a rail and a ledger row per job moved.

**Four agents, one function.** What differs between them is their brief, their SOP set, and
which sensor types reach them, and all three are data rather than behaviour: the brief comes
from `agent_prompts.MANDATES`, the SOP from the packet, and the routing from `agent.ROUTES`.
Four near-identical bodies would be four places for a rail to be applied in three of them.

`herd_health` is handed nothing on every tick and returns empty, because it cannot read a
sensor and nothing writes animal events until chaos does at M5. That is the routing table
working correctly rather than a gap, so it is not logged as one.

## There is no tool loop here, and that is the architecture

A worker gets `evidence.py`'s packet and answers in one call. No `create_react_agent`, no
tools bound, nothing to navigate. Measured, from `src/models/CLAUDE.md`: a 9b model on an
open-ended loop made 16 catalog calls, zero sensor reads, and never answered; handed the
map it returned a confident false all-clear where Opus found four tanks below the floor;
handed an assembled packet it produced 72 clean advisories. Local models write well and
navigate badly, so the job is arranged as writing. That the job is arranged as writing is
also what makes M7's move down a tier plausible at all.

## The rails are code, and they are checked after the answer, never prompted around

`triage.py` owns severity, so the model is asked to echo it and the echo is then verified
against the incident and discarded. Three checks run on every answer:

  * **`severity_mismatch`** - the echo disagrees with triage. Blocking. The stored severity
    is triage's either way, so the mismatch costs no correctness; it is a signal that this
    model, on this page, is arguing with the verdict, which is the leading indicator for the
    fabrication mode this whole design exists to avoid.
  * **`all_clear`** - the work order has no real action in it. Blocking, per
    `src/agent/CLAUDE.md`: code already flagged this incident, so "nothing to do here" is a
    contradiction rather than a finding. Checked against the **actions list**, not by
    scanning the prose for the phrase. Prose matching would fire on "the second tank at
    29.7 gal is fine," which is a correct and useful sentence on a page about a different
    tank, and a rail that punishes accurate writing gets turned off within a week.
  * **`invented_rule`** - a cited rule id that does not appear in the SOP the packet
    carried. Blocking, and it is the cheapest real check in this repo: the standing orders
    are in the packet, so the set of citable ids is known exactly. An agent citing
    `WATER-07` sends the next person looking for a rule that does not exist.

Two more are recorded and do not block, because they are quality signals rather than safety
ones: `no_rule_cited` and `sensor_not_named`.

**No retry.** A rejected answer is logged as rejected and kept. Retrying until the rail
passes turns the rail into a sampler and destroys the only measurement M7 will have.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from src.agent.agent import WATER_FEED
from src.agent.state import Incident, Severity, WorkOrder
from src.models.llm_client import ModelResponse, call_tier2
from src.prompts.system_prompts import WORK_ORDER_SCHEMA, WORK_ORDER_TOOL, WORK_ORDER_TOOL_DESCRIPTION, system_prompt
from src.tools.allowlists import WRITE_TOOL_ARGS, proposable_tools_for
from src.tools.evidence import EvidencePacket
from src.utils.helpers import as_strings, gather_bounded, has_no_real_instruction
from src.utils.logger import get_logger

log = get_logger(__name__)

#: Well under `SWEEP_CONCURRENCY`. A sweep's 160 requests are cheap reads against four
#: Lambdas; eleven concurrent Opus calls are neither cheap nor rate-limit-free, and the
#: whole tick has a five-minute cadence to fit inside rather than a latency target to hit.
AGENT_CONCURRENCY = 4

#: Measured, not estimated. The Alkali Flat packet with seven siblings and the whole water
#: SOP bills **5,555 tokens in**, and a complete work order came back at **1,137 out** - both
#: several times the guess made before the first call, and the input is dominated by the SOP
#: rather than by the evidence. 2,048 leaves real headroom above the observed output so
#: truncation is never the normal case, and a truncation still logs `max_tokens` loudly
#: instead of arriving as a failed parse.
MAX_OUTPUT_TOKENS = 2_048

#: Rule ids as the SOP files spell them, so a citation can be checked against the page the
#: model actually received. Anchored to the heading, not matched anywhere in the file, so a
#: rule mentioned inside another rule's body ("treat it as WATER-01") does not become a
#: citable id in its own right by accident. It already is one; the anchor is what keeps that
#: true by definition instead of by luck.
_RULE_HEADING = re.compile(r"^#+\s*([A-Z][A-Z-]*-\d+)\b", re.MULTILINE)

#: Only in a headline, where an all-clear is stated rather than implied. Deliberately not
#: run over `assessment`.
_ALL_CLEAR_HEADLINE = re.compile(r"\b(all clear|no action (required|needed)|nothing to do|no issue|no problem|false alarm|within normal)\b", re.IGNORECASE)

BLOCKING_VIOLATIONS = frozenset({"severity_mismatch", "all_clear", "invented_rule", "no_payload", "schema_invalid"})

#: The three return-path checks on a `proposed_write`, M6. **None of them blocks the work
#: order.** The prose is still a defensible answer about the incident; what fails is the one
#: part of it that would have changed the ranch, so that part is stripped and its code is
#: recorded, and the proposal never reaches the gate. Mapped onto the plan's three names:
#:
#:   * shape:     `write_shape_invalid`     not `{tool, args}`, an unknown or missing argument,
#:                                          a bad enum, an unparseable timestamp
#:   * key:       `write_tool_not_allowed`  a tool outside this agent's proposable set. (The
#:                                          incident key itself is code's: a proposal lives
#:                                          inside the work order for one packet and never
#:                                          names an incident, so the model has no index to
#:                                          get wrong. `executor` re-checks the code-attached
#:                                          key against the routed set as `write_key_unknown`.)
#:   * grounding: `ungrounded_write_arg`    an id or a quantity that is not on the page
WRITE_VIOLATIONS = frozenset({"write_shape_invalid", "write_tool_not_allowed", "ungrounded_write_arg"})

_NUMBER_TOKEN = re.compile(r"-?\d+(?:\.\d+)?")
_ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$")


def citable_rules(sop_text: str) -> frozenset[str]:
    """Every rule id the packet actually contained. Empty when it carried no SOP."""
    return frozenset(_RULE_HEADING.findall(sop_text))


def page_numbers(page: str) -> frozenset[float]:
    """Every number token on the page, as floats, so `12` grounds `12.0` and not `120`."""
    return frozenset(float(tok) for tok in _NUMBER_TOKEN.findall(page))


def check_write_proposal(raw: object, *, agent: str, page: str) -> tuple[list[str], dict[str, Any] | None]:
    """`(violations, proposal)`. The proposal comes back cleaned, or `None` when there is none
    or when it failed a check. The order of the checks is the order of the plan's three names,
    and each one fires on its own so the planted suite can assert WHICH one did.

    `{"tool": "", "args": {}}` is the model saying "nothing", and it is the usual answer. It
    is not a violation and it produces no proposal.
    """
    if raw is None or raw == {} or raw == "":
        return [], None
    if not isinstance(raw, dict):
        return ["write_shape_invalid"], None
    tool = str(raw.get("tool") or "").strip()
    args = raw.get("args")
    if not tool and not args:
        return [], None
    if not tool or not isinstance(args, dict):
        return ["write_shape_invalid"], None

    if tool not in proposable_tools_for(agent):
        return ["write_tool_not_allowed"], None

    spec = {a.name: a for a in WRITE_TOOL_ARGS.get(tool, ())}
    unknown = sorted(set(args) - set(spec))
    missing = sorted(name for name, a in spec.items() if a.required and name not in args)
    if unknown or missing:
        log.warning("write_proposal_shape", agent=agent, tool=tool, unknown_args=unknown, missing_args=missing)
        return ["write_shape_invalid"], None

    cleaned: dict[str, Any] = {}
    ungrounded: list[str] = []
    numbers = page_numbers(page)
    lowered = page.lower()
    for name, value in args.items():
        arg = spec[name]
        if arg.kind == "text":
            cleaned[name] = str(value)
            continue
        if arg.kind == "enum":
            if str(value) not in arg.choices:
                log.warning("write_proposal_shape", agent=agent, tool=tool, arg=name, value=value, choices=list(arg.choices))
                return ["write_shape_invalid"], None
            cleaned[name] = str(value)
            continue
        if arg.kind == "timestamp":
            if not isinstance(value, str) or not _ISO_TIMESTAMP.match(value):
                log.warning("write_proposal_shape", agent=agent, tool=tool, arg=name, value=value, expected="ISO 8601")
                return ["write_shape_invalid"], None
            cleaned[name] = value
            continue
        if arg.kind == "number":
            if isinstance(value, bool) or not isinstance(value, int | float):
                return ["write_shape_invalid"], None
            if float(value) not in numbers:
                ungrounded.append(name)
            cleaned[name] = value
            continue
        # id: a string that appears on the page, case-insensitively, because the page spells a
        # sensor id the way the map does and a model may not preserve case.
        text = str(value).strip()
        if not text or text.lower() not in lowered:
            ungrounded.append(name)
        cleaned[name] = text

    if ungrounded:
        log.warning("write_proposal_ungrounded", agent=agent, tool=tool, args=ungrounded, hint="an id or a quantity the page never printed; the proposal is dropped and the prose ships")
        return ["ungrounded_write_arg"], None
    return [], {"tool": tool, "args": cleaned}


def check(payload: dict[str, Any], *, incident: Incident, packet: EvidencePacket, agent: str = WATER_FEED) -> tuple[list[str], dict[str, Any]]:
    """`(violations, cleaned)`. The only place a model's answer is judged.

    Returns the violations rather than raising them, and returns the cleaned fields even
    when it is rejecting, because a rejected work order is a record worth keeping.
    """
    write_violations, proposal = check_write_proposal(payload.get("proposed_write"), agent=agent, page=packet.render())
    cleaned: dict[str, Any] = {
        "headline": str(payload.get("headline") or "").strip(),
        "assessment": str(payload.get("assessment") or "").strip(),
        "actions": as_strings(payload.get("actions")),
        "rules_cited": as_strings(payload.get("rules_cited")),
        "escalate": bool(payload.get("escalate")),
        "escalate_reason": str(payload.get("escalate_reason") or "").strip(),
        "unknowns": as_strings(payload.get("unknowns")),
        "severity_echo": str(payload.get("severity_echo") or "").strip().lower(),
        "proposed_write": proposal,
    }

    violations: list[str] = []
    if not cleaned["headline"] or not cleaned["assessment"]:
        violations.append("schema_invalid")
    if cleaned["severity_echo"] != incident.severity:
        violations.append("severity_mismatch")
    if has_no_real_instruction(cleaned["actions"]) or _ALL_CLEAR_HEADLINE.search(cleaned["headline"]):
        violations.append("all_clear")

    citable = citable_rules(packet.sop_text)
    invented = [rule for rule in cleaned["rules_cited"] if rule.upper() not in citable]
    if invented:
        violations.append("invented_rule")
    if not cleaned["rules_cited"] and citable:
        violations.append("no_rule_cited")
    if incident.sensor_id not in f"{cleaned['headline']} {cleaned['assessment']}":
        violations.append("sensor_not_named")

    if invented:
        log.warning("invented_rule_ids", incident=incident.key, cited=cleaned["rules_cited"], citable=sorted(citable), sop=packet.sop_name)
    violations.extend(write_violations)
    return violations, cleaned


def to_work_order(*, packet: EvidencePacket, agent: str, response: ModelResponse) -> WorkOrder:
    """Fold one model response into the stored shape, rails included.

    Every path through here produces a `WorkOrder`. A call that never reached the model
    produces one too, carrying `status="no_answer"` and the transport detail in
    `violations`, because a tick that silently drops an incident nobody was paged about is
    the failure this repo is built to make impossible.
    """
    incident = packet.incident
    severity: Severity = incident.severity
    receipt = {
        "provider": response.provider,
        "model": response.model,
        "finish_reason": response.finish_reason,
        "latency_ms": response.latency_ms,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }

    # `or response.truncated`: a tool call cut off at `max_tokens` still arrives with an
    # `input` dict on it, partially filled. Checking only for `None` sends that half-answer
    # through the rails, where it fails as `all_clear` or `schema_invalid` and blames the model
    # for a budget bug. Measured at M3, on the supervisor rather than here, but the hole is the
    # same shape in both places. `ok` already says a truncated answer is not an answer.
    if response.payload is None or response.truncated:
        detail = response.error or ("truncated before it answered" if response.truncated else "no tool call in the response")
        log.error("work_order_no_answer", incident=incident.key, agent=agent, finish_reason=response.finish_reason, detail=detail)
        return WorkOrder(incident_key=incident.key, agent=agent, severity=severity, status="no_answer", violations=("no_payload", response.finish_reason), assessment=detail, **receipt)

    violations, cleaned = check(response.payload, incident=incident, packet=packet, agent=agent)
    blocking = [v for v in violations if v in BLOCKING_VIOLATIONS]
    if violations:
        log.warning("work_order_violations", incident=incident.key, agent=agent, violations=violations, blocking=bool(blocking))

    return WorkOrder(
        incident_key=incident.key,
        agent=agent,
        severity=severity,
        status="rejected" if blocking else "ok",
        violations=tuple(violations),
        **cleaned,
        **receipt,
    )


async def judge_packet(packet: EvidencePacket, *, agent: str = WATER_FEED, reasoning_effort: str = "none") -> WorkOrder:
    """One incident, one call, one work order.

    The user turn is `packet.render()` verbatim, the same string a human reads when the
    packet is printed. One rendering, not two: a prompt whose text differs from the page you
    inspected makes every debugging session a guess about which version the model saw.
    """
    response = await call_tier2(
        agent=agent,
        system=system_prompt(agent),
        user=packet.render(),
        schema=WORK_ORDER_SCHEMA,
        schema_name=WORK_ORDER_TOOL,
        schema_description=WORK_ORDER_TOOL_DESCRIPTION,
        reasoning_effort=reasoning_effort,
        max_tokens=MAX_OUTPUT_TOKENS,
        incident_key=packet.incident.key,
    )
    return to_work_order(packet=packet, agent=agent, response=response)


async def run_agent(
    agent: str,
    packets: tuple[EvidencePacket, ...] | list[EvidencePacket],
    *,
    reasoning_effort: str = "none",
    limit: int = AGENT_CONCURRENCY,
    sem: asyncio.Semaphore | None = None,
) -> tuple[WorkOrder, ...]:
    """Every packet this agent was routed this tick, bounded. One raising packet is not an outage.

    `gather_bounded` returns exceptions as values, and one packet blowing up has to leave the
    other ten alone: `src/agent/CLAUDE.md` requires a tick to survive one sub-agent raising,
    and eleven incidents behind one unhandled error is ten pastures nobody hears about.

    **One function for four agents, not four functions.** The agents differ in their brief,
    their SOP set, and which sensor types reach them - all three of which are data this
    function is handed rather than behaviour it contains. Four near-identical bodies would be
    four places for a rail to be applied in three of them.

    **An empty list returns empty and does not log.** `herd_health` cannot read a sensor, so
    no sensor incident routes to it and it is handed nothing on every tick until chaos writes
    real animal events at M5. That is the routing table working, not a fault, and a warning
    line per tick per idle agent trains everyone to ignore the log.

    `sem` is the fan-out's shared ceiling. Absent, this bounds itself, which is right for a
    single agent called directly.
    """
    if not packets:
        return ()

    results = await gather_bounded([judge_packet(p, agent=agent, reasoning_effort=reasoning_effort) for p in packets], limit=limit, sem=sem)

    orders: list[WorkOrder] = []
    for packet, outcome in zip(packets, results, strict=True):
        if isinstance(outcome, WorkOrder):
            orders.append(outcome)
            continue
        detail = f"{type(outcome).__name__}: {outcome}"
        log.error("worker_raised", incident=packet.incident.key, agent=agent, error=detail)
        orders.append(WorkOrder(incident_key=packet.incident.key, agent=agent, severity=packet.incident.severity, status="no_answer", violations=("worker_raised",), assessment=detail))

    log.info(
        "agent_complete",
        agent=agent,
        packets=len(orders),
        shipped=sum(1 for o in orders if o.shippable),
        rejected=sum(1 for o in orders if o.status == "rejected"),
        no_answer=sum(1 for o in orders if o.status == "no_answer"),
        escalated=sum(1 for o in orders if o.escalate),
        sops=sorted({p.sop_name for p in packets if p.sop_name}),
        input_tokens=sum(o.input_tokens for o in orders),
        output_tokens=sum(o.output_tokens for o in orders),
    )
    return tuple(orders)


async def run_water_feed(packets: tuple[EvidencePacket, ...] | list[EvidencePacket], *, reasoning_effort: str = "none", limit: int = AGENT_CONCURRENCY) -> tuple[WorkOrder, ...]:
    """M2's entry point, kept as one line over `run_agent`.

    Not deleted, because `water_feed` is the only agent whose output has been measured against
    a real ranch (19 orders, 19 shipped, 0 rejected) and the calibration fixture in
    `tests/test_agent.py` is a recording of this call. A wrapper costs nothing and keeps the
    named thing those measurements refer to.
    """
    return await run_agent(WATER_FEED, packets, reasoning_effort=reasoning_effort, limit=limit)


async def fan_out(
    packets_by_agent: dict[str, tuple[EvidencePacket, ...]],
    *,
    reasoning_effort: str = "none",
    limit: int = AGENT_CONCURRENCY,
) -> dict[str, tuple[WorkOrder, ...]]:
    """All four responders at once, under **one** ceiling. The bounded fan-out.

    Two things this function exists to get right, both of which a plain
    `asyncio.gather(*[run_agent(a, p) for ...])` gets wrong:

      * **The ceiling is global.** `AGENT_CONCURRENCY = 4` is a statement about how many Opus
        calls this repo will have in flight, and four agents each bounding themselves at four
        is sixteen. The semaphore is built here, once, and handed down. This is the only place
        that number means what it says.
      * **An agent failing is not the tick failing.** `run_agent` already contains a raising
        *packet*, but the call to `run_agent` itself can fail before any packet does - an
        agent with no brief, a bad slice, an import-time error in a new worker - and that
        would take the other three agents' work orders down with it. So each agent is a task
        whose exception becomes a `no_answer` order per packet it was carrying, which keeps
        the invariant that matters: **every routed incident produces a work order, always.**

    Returns a dict keyed by agent, including agents that returned nothing, so the shift report
    can tell "asked, found nothing" apart from "never asked."
    """
    live = {agent: tuple(packets) for agent, packets in packets_by_agent.items() if packets}
    if not live:
        return {agent: () for agent in packets_by_agent}

    sem = asyncio.Semaphore(limit)
    agents = sorted(live)
    log.info("fan_out_start", agents=agents, packets={a: len(live[a]) for a in agents}, ceiling=limit, worlds=len(agents))

    results = await asyncio.gather(*(run_agent(agent, live[agent], reasoning_effort=reasoning_effort, sem=sem) for agent in agents), return_exceptions=True)

    orders: dict[str, tuple[WorkOrder, ...]] = {agent: () for agent in packets_by_agent}
    for agent, outcome in zip(agents, results, strict=True):
        if isinstance(outcome, BaseException):
            detail = f"{type(outcome).__name__}: {outcome}"
            log.error("agent_raised", agent=agent, packets=len(live[agent]), error=detail)
            orders[agent] = tuple(
                WorkOrder(incident_key=p.incident.key, agent=agent, severity=p.incident.severity, status="no_answer", violations=("agent_raised",), assessment=detail) for p in live[agent]
            )
            continue
        orders[agent] = outcome

    flat = [o for group in orders.values() for o in group]
    log.info(
        "fan_out_complete",
        agents=agents,
        work_orders=len(flat),
        shipped=sum(1 for o in flat if o.shippable),
        rejected=sum(1 for o in flat if o.status == "rejected"),
        no_answer=sum(1 for o in flat if o.status == "no_answer"),
        input_tokens=sum(o.input_tokens for o in flat),
        output_tokens=sum(o.output_tokens for o in flat),
    )
    return orders


__all__ = ["AGENT_CONCURRENCY", "BLOCKING_VIOLATIONS", "MAX_OUTPUT_TOKENS", "WRITE_VIOLATIONS", "check", "check_write_proposal", "citable_rules", "fan_out", "judge_packet", "page_numbers", "run_agent", "run_water_feed", "to_work_order"]
