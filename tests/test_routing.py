"""Routing: a table, and the two ways a table goes wrong.

A category triage can emit that routing does not name is an incident nobody is paged
about, which downstream is indistinguishable from a ranch with nothing wrong. That is the
rail this file exists for. The second rail is the other direction: routing must not invent
work for `herd_health`, which cannot read a sensor.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import structlog

from src.agent import routing
from src.agent.routing import AGENTS, DEFAULT_OWNER, HERD_HEALTH, ROUTES, owner_for, owners_for, route, unrouted_categories
from src.agent.state import Finding, Incident
from src.tools.triage import ALL_CATEGORIES

T0 = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_warn_once() -> None:
    routing.reset_warn_once()


def incident(key: str, *, category: str, owner: str | None = None) -> Incident:
    return Incident(
        key=key,
        sensor_id=key.split(":")[0],
        sensor_type="water-level",
        location="Alkali Flat",
        category=category,
        severity="critical",
        status="opened",
        summary="x",
        first_seen_at=T0,
        last_seen_at=T0,
        owner=owner,
    )


# --------------------------------------------------------------------------- #
# completeness, in both directions
# --------------------------------------------------------------------------- #
def test_every_category_triage_can_emit_has_an_owner() -> None:
    """The rail. An unrouted category is an incident nobody is paged about, and a silent
    fallback would make that look identical to a calm ranch. Adding a band to
    `triage.py` without a row here fails right here."""
    assert unrouted_categories() == frozenset(), f"unrouted: {sorted(unrouted_categories())}"


def test_routing_names_no_category_triage_cannot_emit() -> None:
    """From the other side: a stale row is a threshold that was renamed or deleted, and it
    reads as coverage that no longer exists."""
    assert frozenset(ROUTES) - frozenset(ALL_CATEGORIES) == frozenset()


def test_every_owner_is_a_real_agent() -> None:
    assert set(ROUTES.values()) <= set(AGENTS)


def test_herd_health_owns_nothing_in_m1_and_that_is_deliberate() -> None:
    """Its tools are the Care API and every category here comes from a sensor. Handing it
    a dry tank would produce a work order about cattle that are fine. It wakes up at M5,
    when chaos writes real animal events."""
    assert HERD_HEALTH not in set(ROUTES.values())


# --------------------------------------------------------------------------- #
# the fallback, which must be loud
# --------------------------------------------------------------------------- #
def test_an_unrouted_category_falls_back_loudly_and_only_warns_once() -> None:
    """Never dropped: an incident with no owner is worse than one with the wrong owner.
    Warned once because 160 sensors sharing a new category would otherwise bury the tick
    line under 160 identical warnings.

    `capture_logs` rather than `caplog`: these lines go through structlog, and the test
    process never calls `configure_logging`, so a `caplog` assertion here passes on an
    empty list and proves nothing.
    """
    with structlog.testing.capture_logs() as logs:
        first = owner_for("meteor_strike")
        second = owner_for("meteor_strike")

    assert first == second == DEFAULT_OWNER
    warnings = [line for line in logs if line["event"] == "unrouted_category"]
    assert len(warnings) == 1
    assert warnings[0]["category"] == "meteor_strike"


def test_a_known_category_warns_about_nothing() -> None:
    with structlog.testing.capture_logs() as logs:
        assert owner_for("water_low") == "water_feed"
    assert [line for line in logs if line["event"] == "unrouted_category"] == []


# --------------------------------------------------------------------------- #
# grouping
# --------------------------------------------------------------------------- #
def test_owners_for_keys_on_incident_key_not_sensor_id() -> None:
    """A degraded water sensor produces two findings on one sensor, and they belong to two
    different agents. Keying the owner map on `sensor_id` would silently drop one."""
    findings = (
        Finding(sensor_id="alkali-flat-water", sensor_type="water-level", location="Alkali Flat", category="water_low", severity="critical", summary="a"),
        Finding(sensor_id="alkali-flat-water", sensor_type="water-level", location="Alkali Flat", category="sensor_degraded", severity="warning", summary="b"),
    )
    assert owners_for(findings) == {"alkali-flat-water:water_low": "water_feed", "alkali-flat-water:sensor_degraded": "infrastructure"}


def test_route_groups_by_agent_and_is_stable() -> None:
    """Sorted, deterministically: the fan-out order shows up in `tick.jsonl` and in the
    demo, and a set-ordered dict makes two identical ticks look like different ones."""
    incidents = [
        incident("z-tank:water_low", category="water_low"),
        incident("a-tank:water_low", category="water_low"),
        incident("north-fence:fence_down", category="fence_down"),
    ]
    got = route(incidents)
    assert got == {"infrastructure": ("north-fence:fence_down",), "water_feed": ("a-tank:water_low", "z-tank:water_low")}
    assert list(got) == sorted(got), "agent order must not depend on insertion order"
    assert route(list(reversed(incidents))) == got


def test_route_honors_the_owner_already_stored_on_the_row() -> None:
    """The store stamps an owner when the incident is written. Re-deriving it here would
    let a routing-table edit silently reassign an incident that a human is already
    working, so the stored value wins."""
    assert route([incident("a:water_low", category="water_low", owner="compliance")]) == {"compliance": ("a:water_low",)}


def test_route_of_nothing_is_nothing_not_five_empty_agents() -> None:
    """A calm tick must fan out to zero agents. Pre-seeding the dict with every agent
    would spend five model calls on nothing, every five minutes, forever."""
    assert route([]) == {}
