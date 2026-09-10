"""The supervisor. In M1 that is the routing table and nothing else.

The LangGraph supervisor and the five worker factories arrive at M3 and land here, next
to the table that decides which of them owns a finding. Routing lives with the supervisor
rather than in its own module because deciding who works an incident is the supervisor's
whole job, and `src/models/routing.py` is a different question (which model tier runs a
job) that should not share a name with this one.

No model is involved in routing, ever. Which of five agents owns a dry tank is a fact
about the ranch's org chart, not a judgment call, and asking a model to make it costs
tokens to get a lookup wrong.

Fan-out lives in the graph at M4. This module answers one question: who owns this.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from src.agent.state import Finding, Incident
from src.tools.triage import ALL_CATEGORIES
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
