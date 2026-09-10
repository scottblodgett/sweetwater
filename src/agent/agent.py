"""The supervisor: who owns a finding, and what the shift is told at the end of the tick.

Two halves, and the line between them is the cost line. Above it, routing: pure table
lookup, no model, ever. Which of five agents owns a dry tank is a fact about the ranch's
org chart, not a judgment call, and asking a model to make it costs tokens to get a lookup
wrong. Below it, `synthesize`: the one place the supervisor itself spends, at most one call
per tick and usually none.

Routing lives with the supervisor rather than in its own module because deciding who works
an incident is the supervisor's whole job, and `src/models/routing.py` is a different
question (which model tier runs a job) that should not share a name with this one.

The fan-out itself is `workers.fan_out`, not here, because it is the thing being
supervised. Import direction is `workers -> agent`, never back, which is also why the two
rails both halves need (`as_strings`, `has_no_real_instruction`) live in `src/utils`.
The LangGraph wiring around all of it arrives at M4.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from src.agent.state import SEVERITY_ORDER, Finding, Incident, RanchState, ShiftReport, WorkOrder
from src.tools.triage import ALL_CATEGORIES
from src.utils.helpers import as_strings, has_no_real_instruction
from src.utils.logger import get_logger

log = get_logger(__name__)

WATER_FEED = "water_feed"
HERD_HEALTH = "herd_health"
INFRASTRUCTURE = "infrastructure"
COMPLIANCE = "compliance"
CHAOS = "chaos"

AGENTS = (WATER_FEED, HERD_HEALTH, INFRASTRUCTURE, COMPLIANCE, CHAOS)

#: The four the supervisor fans out to. `chaos` is an agent and not a responder: it breaks
#: the ranch rather than answering for it, it is never a route target, and it stays out of
#: the graph until M5. Two names because the distinction is real and collapsing it would
#: mean either the fan-out invokes the saboteur or the allowlists lose a slice.
RESPONDERS = (WATER_FEED, HERD_HEALTH, INFRASTRUCTURE, COMPLIANCE)

#: Fallback owner. Never a silent drop: an unrouted incident is one nobody is paged
#: about, which is indistinguishable from a ranch with nothing wrong.
DEFAULT_OWNER = INFRASTRUCTURE

ROUTES: dict[str, str] = {
    # Nobody dies of thirst or hunger. Freeze and heat live here rather than with
    # `herd_health` because what they threaten first is water: a frozen tank cuts cattle
    # off from it, and heat roughly doubles what they drink.
    "water_low": WATER_FEED,
    "feed_low": WATER_FEED,
    "freeze_risk": WATER_FEED,
    "heat_stress": WATER_FEED,
    "deep_snow": WATER_FEED,
    # Containment, power, reserves, and the physical plant. Wellhead pressure is here and
    # not with `compliance` because the fix is a human turning a valve; the regulatory
    # consequence is what happens if nobody does.
    "fence_down": INFRASTRUCTURE,
    "gate_open": INFRASTRUCTURE,
    "power_low": INFRASTRUCTURE,
    "fuel_low": INFRASTRUCTURE,
    "high_wind": INFRASTRUCTURE,
    "wellhead_overpressure": INFRASTRUCTURE,
    "wellhead_underpressure": INFRASTRUCTURE,
    # A broken sensor is a maintenance job, not a ranch problem. Routing it to the agent
    # that owns the pasture would produce a work order about cattle that are fine.
    "sensor_offline": INFRASTRUCTURE,
    "sensor_degraded": INFRASTRUCTURE,
    "sensor_fault": INFRASTRUCTURE,
    "unknown_sensor_type": INFRASTRUCTURE,
    # Keep the payments, prove the stewardship.
    "range_dry": COMPLIANCE,
    "stream_flow_low": COMPLIANCE,
}

_warned_categories: set[str] = set()


def reset_warn_once() -> None:
    """Tests only. Warn-once state is per process."""
    _warned_categories.clear()


def owner_for(category: str) -> str:
    """The one agent that owns this category.

    `herd_health` deliberately owns nothing here. It cannot read a sensor: its tools are
    the Care API, and every category in this table comes from a sensor. It stays idle
    until chaos writes real animal events at M5, and that is correct rather than a gap.
    """
    owner = ROUTES.get(category)
    if owner is None:
        if category not in _warned_categories:
            _warned_categories.add(category)
            log.warning("unrouted_category", category=category, owner=DEFAULT_OWNER, hint="add it to ROUTES in src/agent/agent.py")
        return DEFAULT_OWNER
    return owner


def owners_for(findings: Sequence[Finding]) -> dict[str, str]:
    """`{incident_key: agent}`, for stamping an owner on a row as it is written."""
    return {f.key: owner_for(f.category) for f in findings}


def route(incidents: Iterable[Incident]) -> dict[str, tuple[str, ...]]:
    """`{agent: (incident_key, ...)}` for the incidents handed in.

    Called with **newly-opened incidents only**. An `ongoing` incident has already been
    worked and re-waking an agent for it every five minutes is how a service teaches its
    client to ignore it. This is also the whole cost story: the stages before this one are
    free, and only what comes out of here reaches a model.
    """
    grouped: dict[str, list[str]] = {}
    for incident in incidents:
        grouped.setdefault(incident.owner or owner_for(incident.category), []).append(incident.key)
    return {agent: tuple(sorted(keys)) for agent, keys in sorted(grouped.items())}


def unrouted_categories() -> frozenset[str]:
    """Every category triage can emit that this table does not name. Must stay empty."""
    return frozenset(ALL_CATEGORIES) - frozenset(ROUTES)


# --------------------------------------------------------------------------- #
# The shift report: the supervisor's own output, and the last stage of a tick.
#
# Everything above this line is free. This is the only place in the repo where the
# supervisor itself spends a token, and it spends at most one call per tick.
# --------------------------------------------------------------------------- #

#: A page of finished work orders is roughly 1,100 output tokens each, so ten of them is a
#: 12k-token read. The supervisor's answer is one page and does not need room to think.
#:
#: **1,024 was the first value here and the first live tick truncated it.** 15 work orders across
#: three worlds is a 32.5k-char page, and the supervisor spent the whole budget on the situation
#: paragraph and the first few priorities before `max_tokens` cut the tool call mid-object. That is
#: the M2 lesson repeating verbatim (`docs/model-routing.md`: print the page before sizing the
#: budget).
#:
#: So this one is sized off a measurement instead: **a complete answer to a 14-order page came back
#: at 1,646 output tokens.** 2,048 would have shipped with 400 tokens of headroom, which is how you
#: get to do this twice. `SHIFT_REPORT_SCHEMA` caps the answer at 6 priorities and 4 escalations,
#: and `linked` is the only unbounded field (deliberately: it must be able to name every key on the
#: page), which puts a long-but-legal answer near 2,200.
SHIFT_REPORT_MAX_TOKENS = 3_072

#: Below this, there is nothing to fuse. **One world is a concatenation of length one**, and
#: paying Opus to reformat a single agent's work orders into a shift report buys a header.
#: Two worlds is the storm front, which is exactly the condition `src/agent/CLAUDE.md` already
#: names as an escalation trigger, and it is the first tick where an incident in one world can
#: explain an incident in another.
FUSION_THRESHOLD = 2

#: An all-clear stated outright, in a headline. Not the same list `workers.py` uses: that one
#: is about one sensor ("no problem"), this one is about a whole ranch ("quiet night"), and a
#: shared regex would mean widening one of them widens the other by accident.
_ALL_CLEAR = re.compile(r"\b(all clear|no action (required|needed)|nothing to do|no issues|no problems|quiet (night|shift|day)|ranch is (fine|healthy|normal))\b", re.IGNORECASE)


def render_shift_page(orders: Sequence[WorkOrder]) -> str:
    """The page the supervisor reads: every work order this tick, grouped by world.

    Grouped rather than interleaved because the supervisor's job is to cross the groups, and a
    page that has already blended them hands over the answer and measures nothing.

    **A work order that could not be written is listed, not dropped.** An incident nobody could
    judge is a fact about the shift, and it is the one thing the supervisor can say that no
    responder could: somebody has to go look at this because the system could not.
    """
    lines: list[str] = []
    for agent in RESPONDERS:
        mine = [o for o in orders if o.agent == agent]
        if not mine:
            continue
        lines.append(f"=== {agent} ({len(mine)} work order{'s' if len(mine) != 1 else ''}) ===")
        for order in mine:
            lines.append(f"\n-- incident key: {order.incident_key}")
            if order.shippable:
                lines.append(order.render())
            else:
                lines.append(f"[{order.severity.upper()}] NO USABLE WORK ORDER for this incident ({order.status}: {', '.join(order.violations) or 'no detail'}).")
                lines.append("Nothing was judged here. Treat it as unworked and send somebody to look.")
        lines.append("")
    return "\n".join(lines).strip()


def assemble_shift_report(orders: Sequence[WorkOrder], *, worlds: Sequence[str], source: Literal["model", "code"] = "code", violations: Sequence[str] = ()) -> ShiftReport:
    """The shift report built in code. Both the calm-tick answer and the fallback.

    One function for two jobs on purpose. A fallback that is only ever exercised when something
    has already gone wrong is a fallback nobody has read; this one runs on every single-world
    tick, which is most of them, so it is the best-tested path in the stage.

    It fuses nothing, and says nothing that implies it did. Ordering is by triage severity and
    then by agent, which is a defensible ranking rather than an insightful one, and the
    headline counts rather than narrates. **What it will not do is write an all-clear**: with no
    work orders at all it says the shift was not reported on, which is a different claim from
    the ranch being fine.
    """
    shippable = [o for o in orders if o.shippable]
    ranked = sorted(shippable, key=lambda o: (-SEVERITY_ORDER[o.severity], RESPONDERS.index(o.agent) if o.agent in RESPONDERS else len(RESPONDERS), o.incident_key))
    unworked = [o for o in orders if not o.shippable]

    if not orders:
        headline = "No work orders this tick, so nothing here reports on the shift"
        situation = "Nothing reached a sub-agent this tick. That is not a statement about the ranch: it means no incident was newly opened, and anything already open is being carried as ongoing."
    else:
        criticals = sum(1 for o in ranked if o.severity == "critical")
        headline = f"{len(shippable)} work order{'s' if len(shippable) != 1 else ''} across {len(worlds)} world{'s' if len(worlds) != 1 else ''}" + (f", {criticals} critical" if criticals else "")
        situation = f"Assembled without a model: {'one world' if len(worlds) < FUSION_THRESHOLD else f'{len(worlds)} worlds'} reported this tick, so the work orders below are listed by severity rather than read against each other. Each one is correct about its own sensor; nothing here claims a connection between them."
        if unworked:
            situation += f" {len(unworked)} incident{'s' if len(unworked) != 1 else ''} produced no usable work order and should be treated as unworked."

    priorities = [f"{o.severity}: {o.headline or o.incident_key} ({o.agent})" for o in ranked[:6]]
    priorities += [f"{o.severity}: {o.incident_key} was not judged ({o.status}); send somebody to look" for o in unworked[: max(0, 6 - len(priorities))]]

    escalations = [f"{o.incident_key}: {o.escalate_reason or 'the work order asked to escalate and gave no reason'}" for o in shippable if o.escalate][:4]
    if len(worlds) >= FUSION_THRESHOLD:
        escalations = [f"{len(worlds)} sensing worlds opened incidents in the same tick ({', '.join(worlds)}), which is the storm-front trigger", *escalations][:4]

    return ShiftReport(
        headline=headline,
        situation=situation,
        priorities=tuple(priorities),
        linked=(),  # code claims nothing about causation, ever
        escalations=tuple(escalations),
        source=source,
        worlds=tuple(worlds),
        work_orders=len(orders),
        violations=tuple(violations),
    )


def check_shift_report(payload: dict[str, Any], *, keys: frozenset[str]) -> tuple[list[str], dict[str, Any]]:
    """`(violations, cleaned)`. The rails on the supervisor's answer.

    Two of them, and they are the `invented_rule` and `all_clear` rails pointed at a different
    output, for the same reasons:

      * **`invented_incident`** blocks. `linked` is the report's causal claim, and code knows
        exactly which keys were handed over, so a key that was not is checkable and cheap. A
        report linking an incident that does not exist sends somebody looking for it.
      * **`all_clear`** blocks. Code found every one of these before the supervisor was called,
        so a page saying the ranch is quiet is a contradiction. Read off the priorities list and
        the headline, never the `situation` prose, for the reason `workers.py` gives: "the tank
        at Alkali Flat is the only real problem tonight" is accurate, useful writing and a
        prose matcher rejects it.
    """
    cleaned: dict[str, Any] = {
        "headline": str(payload.get("headline") or "").strip(),
        "situation": str(payload.get("situation") or "").strip(),
        "priorities": as_strings(payload.get("priorities")),
        "linked": as_strings(payload.get("linked")),
        "escalations": as_strings(payload.get("escalations")),
    }

    violations: list[str] = []
    if not cleaned["headline"] or not cleaned["situation"]:
        violations.append("schema_invalid")

    invented = [key for key in cleaned["linked"] if key not in keys]
    if invented:
        violations.append("invented_incident")
        log.warning("shift_report_invented_incident", cited=cleaned["linked"], known=sorted(keys))

    priorities = cleaned["priorities"]
    if has_no_real_instruction(priorities) or _ALL_CLEAR.search(cleaned["headline"]):
        violations.append("all_clear")

    return violations, cleaned


async def synthesize(state: RanchState, *, spend: bool = True) -> ShiftReport:
    """One shift report per tick. **At most one model call, and often none.**

    The whole cost decision in this stage is the `if`. A tick where one sensing world opened
    incidents has nothing to fuse, so it is assembled in code for free, and that is most ticks.
    A tick where two or more worlds opened incidents is the storm front, which is the only shape
    where an incident in one world can explain an incident in another, and it is the one worth
    an Opus call. The free pass already produces that shape most days on this ranch, because
    `infrastructure` and `water_feed` open together whenever a solar water site starts failing.

    Rails run after the answer and are never prompted around, same as `workers.check`. A
    rejected report is **replaced** by the code-assembled one rather than retried, carrying the
    violations, because the person coming on shift needs a page and a retry loop turns the rail
    into a sampler.
    """
    orders = state.work_orders
    worlds = state.worlds or tuple(agent for agent in RESPONDERS if any(o.agent == agent for o in orders))

    if not spend or len(worlds) < FUSION_THRESHOLD:
        log.info("shift_report_assembled_in_code", worlds=list(worlds), work_orders=len(orders), reason="nothing to fuse" if spend else "spend=False")
        return assemble_shift_report(orders, worlds=worlds)

    from src.models.llm_client import call_tier2  # local: llm_client is only reachable on a path that spends
    from src.prompts.agent_prompts import SUPERVISOR_MANDATE
    from src.prompts.system_prompts import SHIFT_REPORT_SCHEMA, SHIFT_REPORT_TOOL, SHIFT_REPORT_TOOL_DESCRIPTION

    page = render_shift_page(orders)
    log.info("shift_report_fusing", worlds=list(worlds), work_orders=len(orders), page_chars=len(page))
    response = await call_tier2(
        agent="supervisor",
        # The supervisor does NOT inherit `INHERITED_RULES`. Those rules describe judging one
        # evidence packet and writing a work order, which is not this job, and a brief whose
        # first paragraph describes somebody else's task is worse than no brief at all.
        system=SUPERVISOR_MANDATE,
        user=page,
        schema=SHIFT_REPORT_SCHEMA,
        schema_name=SHIFT_REPORT_TOOL,
        schema_description=SHIFT_REPORT_TOOL_DESCRIPTION,
        reasoning_effort="none",
        max_tokens=SHIFT_REPORT_MAX_TOKENS,
        incident_key=f"tick:{state.tick}",
    )
    receipt = {
        "provider": response.provider,
        "model": response.model,
        "finish_reason": response.finish_reason,
        "latency_ms": response.latency_ms,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
    }

    if response.payload is None or response.truncated:  # see `workers.to_work_order`: a truncated tool call still carries a half-filled dict
        detail = response.error or ("truncated before it answered" if response.truncated else "no tool call in the response")
        log.error("shift_report_no_answer", tick=state.tick, finish_reason=response.finish_reason, detail=detail)
        fallback = assemble_shift_report(orders, worlds=worlds, violations=("no_payload", response.finish_reason))
        return fallback.model_copy(update=receipt)

    violations, cleaned = check_shift_report(response.payload, keys=frozenset(o.incident_key for o in orders))
    if violations:
        log.warning("shift_report_violations", tick=state.tick, violations=violations)
        fallback = assemble_shift_report(orders, worlds=worlds, violations=tuple(violations))
        return fallback.model_copy(update=receipt)

    return ShiftReport(**cleaned, source="model", worlds=tuple(worlds), work_orders=len(orders), **receipt)
