"""The sub-agents that spend money. M2 builds exactly one of them: `water_feed`.

One agent, not five, and Tier 2 only. The point of M2 is a work order that reads well at
the sharpest finding on the ranch ("the tank at Alkali Flat is dry"), and one agent proves
the shape end to end. The other four arrive at M3 with their briefs, and the tier cascade
arrives at M7 with a rail and a ledger row per job moved.

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

import re
from typing import Any

from src.agent.agent import WATER_FEED
from src.agent.state import Incident, Severity, WorkOrder
from src.models.llm_client import ModelResponse, call_tier2
from src.prompts.system_prompts import WORK_ORDER_SCHEMA, WORK_ORDER_TOOL, WORK_ORDER_TOOL_DESCRIPTION, system_prompt
from src.tools.evidence import EvidencePacket
from src.utils.helpers import gather_bounded
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

#: An action that is not an action. Anchored at the start so "check the float and monitor
#: the level after the haul" survives, because that one has a person doing something first.
_NO_OP_ACTION = re.compile(r"^\W*(no action|none|nothing|no further|monitor|continue to monitor|keep monitoring|observe|await|wait and see|take no)\b", re.IGNORECASE)

#: Only in a headline, where an all-clear is stated rather than implied. Deliberately not
#: run over `assessment`.
_ALL_CLEAR_HEADLINE = re.compile(r"\b(all clear|no action (required|needed)|nothing to do|no issue|no problem|false alarm|within normal)\b", re.IGNORECASE)

BLOCKING_VIOLATIONS = frozenset({"severity_mismatch", "all_clear", "invented_rule", "no_payload", "schema_invalid"})


def citable_rules(sop_text: str) -> frozenset[str]:
    """Every rule id the packet actually contained. Empty when it carried no SOP."""
    return frozenset(_RULE_HEADING.findall(sop_text))


def _no_real_action(actions: tuple[str, ...]) -> bool:
    return not actions or all(_NO_OP_ACTION.match(a) for a in actions)


def _strings(raw: Any) -> tuple[str, ...]:
    """A list of non-empty strings, tolerantly. A forced tool call is not a guarantee."""
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    if isinstance(raw, list):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def check(payload: dict[str, Any], *, incident: Incident, packet: EvidencePacket) -> tuple[list[str], dict[str, Any]]:
    """`(violations, cleaned)`. The only place a model's answer is judged.

    Returns the violations rather than raising them, and returns the cleaned fields even
    when it is rejecting, because a rejected work order is a record worth keeping.
    """
    cleaned: dict[str, Any] = {
        "headline": str(payload.get("headline") or "").strip(),
        "assessment": str(payload.get("assessment") or "").strip(),
        "actions": _strings(payload.get("actions")),
        "rules_cited": _strings(payload.get("rules_cited")),
        "escalate": bool(payload.get("escalate")),
        "escalate_reason": str(payload.get("escalate_reason") or "").strip(),
        "unknowns": _strings(payload.get("unknowns")),
        "severity_echo": str(payload.get("severity_echo") or "").strip().lower(),
    }

    violations: list[str] = []
    if not cleaned["headline"] or not cleaned["assessment"]:
        violations.append("schema_invalid")
    if cleaned["severity_echo"] != incident.severity:
        violations.append("severity_mismatch")
    if _no_real_action(cleaned["actions"]) or _ALL_CLEAR_HEADLINE.search(cleaned["headline"]):
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

    if response.payload is None:
        detail = response.error or ("truncated before it answered" if response.truncated else "no tool call in the response")
        log.error("work_order_no_answer", incident=incident.key, agent=agent, finish_reason=response.finish_reason, detail=detail)
        return WorkOrder(incident_key=incident.key, agent=agent, severity=severity, status="no_answer", violations=("no_payload", response.finish_reason), assessment=detail, **receipt)

    violations, cleaned = check(response.payload, incident=incident, packet=packet)
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


async def run_water_feed(packets: tuple[EvidencePacket, ...] | list[EvidencePacket], *, reasoning_effort: str = "none", limit: int = AGENT_CONCURRENCY) -> tuple[WorkOrder, ...]:
    """Every water_feed packet this tick, bounded. One raising agent is not an outage.

    `gather_bounded` returns exceptions as values, and one packet blowing up has to leave
    the other ten alone: `src/agent/CLAUDE.md` requires a tick to survive one sub-agent
    raising, and eleven incidents behind one unhandled error is ten pastures nobody hears
    about.
    """
    if not packets:
        return ()

    results = await gather_bounded([judge_packet(p, reasoning_effort=reasoning_effort) for p in packets], limit=limit)

    orders: list[WorkOrder] = []
    for packet, outcome in zip(packets, results, strict=True):
        if isinstance(outcome, WorkOrder):
            orders.append(outcome)
            continue
        detail = f"{type(outcome).__name__}: {outcome}"
        log.error("worker_raised", incident=packet.incident.key, agent=WATER_FEED, error=detail)
        orders.append(WorkOrder(incident_key=packet.incident.key, agent=WATER_FEED, severity=packet.incident.severity, status="no_answer", violations=("worker_raised",), assessment=detail))

    shipped = sum(1 for o in orders if o.shippable)
    log.info(
        "water_feed_complete",
        packets=len(orders),
        shipped=shipped,
        rejected=sum(1 for o in orders if o.status == "rejected"),
        no_answer=sum(1 for o in orders if o.status == "no_answer"),
        escalated=sum(1 for o in orders if o.escalate),
        input_tokens=sum(o.input_tokens for o in orders),
        output_tokens=sum(o.output_tokens for o in orders),
    )
    return tuple(orders)


__all__ = ["AGENT_CONCURRENCY", "BLOCKING_VIOLATIONS", "MAX_OUTPUT_TOKENS", "check", "citable_rules", "judge_packet", "run_water_feed", "to_work_order"]
