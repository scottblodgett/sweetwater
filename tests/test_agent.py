"""The agent layer: the tick, the routing table, the store, the guards, the instrument.

Five suites in one file, because `docs/Plan.md` says this package gets one test module and
the rails matter more than the filenames. In order:

  1. **the tick, end to end** - catalog, sweep, triage, reconcile, route, agreeing on the
     same objects, writing exactly one line, reporting a failed stage rather than raising
  2. **routing** - every category triage can emit has an owner, and the fallback is loud
  3. **the store** - the three reconciliation buckets, against a real local Postgres and
     the real migration
  4. **the schema guards** - the things that keep this repo out of the ranch's schemas.
     No database required: the accident they prevent is not a failing test, it is a
     `DROP SCHEMA` that already ran
  5. **config and logging** - the instrument is built before the thing it measures, so the
     instrument gets tests first
  6. **the work order** - the rails on what a model is allowed to have said
  7. **the loop** - held incidents, per-upstream backoff, the spend ceiling, cadence, and
     a shutdown that drains rather than cancels. M4.

Never Supabase. The upstream is respx against a fake host and the ledger is the local
`sw_ops_test` schema, so `pytest` passes on a plane.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
import structlog
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from src.agent import agent
from src.agent.agent import (
    AGENTS,
    DEFAULT_OWNER,
    FUSION_THRESHOLD,
    HERD_HEALTH,
    RESPONDERS,
    ROUTES,
    SHIFT_REPORT_MAX_TOKENS,
    assemble_shift_report,
    check_shift_report,
    owner_for,
    owners_for,
    render_shift_page,
    route,
    synthesize,
    unrouted_categories,
)
from src.agent.executor import (
    EXIT_OK,
    EXIT_SPEND_CEILING,
    EXIT_UNRECOVERABLE,
    Backoff,
    _gate_step,
    restore_held,
    run_loop,
    run_tick,
    summarize,
)
from src.agent.gate import GateError, WriteProposal, build_gate, decide, pending, propose, unpaired_audit_ids
from src.agent.gate import main as gate_main
from src.agent.memory import (
    ALLOWED_SCHEMAS,
    RANCH_SCHEMAS,
    SCHEMA,
    SCHEMA_TEST,
    CheckpointerNotMigratedError,
    SchemaGuardError,
    StoreTarget,
    assert_agent_schema,
    assert_droppable_schema,
    assert_local_test_url,
    build_engine,
    checkpointer,
    connect_args_for,
    counts_by_status,
    incidents,
    metadata,
    open_incidents,
    psycopg_url,
    reconcile,
    store_session,
)
from src.agent.state import Finding, Incident, RanchState, WorkOrder
from src.agent.workers import (
    AGENT_CONCURRENCY,
    BLOCKING_VIOLATIONS,
    check_write_proposal,
    citable_rules,
    fan_out,
    run_agent,
    run_water_feed,
    to_work_order,
)
from src.models.llm_client import ASSUMED_RATE_USD_PER_M, THINKING_BUDGET, ModelResponse, call_tier2, cost_usd, resolve_provider
from src.prompts.agent_prompts import MANDATES, SUPERVISOR_MANDATE
from src.prompts.system_prompts import SHIFT_REPORT_SCHEMA, WORK_ORDER_SCHEMA, system_prompt
from src.tools.allowlists import DEPLOYED_TOOLS, GATE_LANDED, WRITE_TOOL_ARGS, WRITE_TOOLS, Approval
from src.tools.chaos import KIND_ANIMAL, KIND_SENSOR, ChaosEvent
from src.tools.evidence import EvidencePacket, HistoryPoint, PastureContext, SiblingReading
from src.tools.mcp_client import RanchMap, SensorRef, flatten_exception, parse_ranch_map
from src.tools.triage import ALL_CATEGORIES
from src.utils.config import Settings, get_settings
from src.utils.helpers import backoff_delay, utc_now_iso
from src.utils.logger import _foreign_chain, _redact

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE = "https://sensor.test"

#: One frozen clock for every suite below. Fixtures use literal timestamps and no test
#: asserts on a value it got from the clock.
T0 = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 10, 14, 5, tzinfo=UTC)
T2 = datetime(2026, 9, 10, 14, 10, tzinfo=UTC)

TANK = SensorRef(sensor_id="alkali-flat-water", sensor_type="water-level", location="Alkali Flat", status="online")
FENCE = SensorRef(sensor_id="east-allotment-fence", sensor_type="fence-voltage", location="East Allotment", status="online")
BIN = SensorRef(sensor_id="home-place-bin", sensor_type="feed-bin-weight", location="Home Place", status="online")
CATALOG = RanchMap(sensors=(TANK, FENCE, BIN))

ALL_SENSORS = ("alkali-flat-water", "east-allotment-fence", "home-place-diesel")


@pytest.fixture(autouse=True)
def _fake_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake SENSOR_API, so a stray unmocked request fails loudly instead of quietly
    reaching the deployed ranch. Autouse across all five suites: only the tick suite reads
    it, and the others never call into `sensors` or `helpers`, so it costs them nothing.
    """
    settings = Settings(sensor_api=BASE, sweep_concurrency=4, upstream_timeout_ms=500, _env_file=None)
    monkeypatch.setattr("src.tools.sensors.get_settings", lambda: settings)
    monkeypatch.setattr("src.utils.helpers.get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def _fresh_warn_once() -> None:
    agent.reset_warn_once()


@pytest.fixture(autouse=True)
def _no_held_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """From M6 `run_loop` reads the held set off the ledger before its first tick. The loop rails
    hand it a fake tick and a bogus URL on purpose, so the default restore is stubbed to empty
    here; the one rail about restoring passes its own `restore=`, and the tick-level rail calls
    `restore_held` directly against `sw_ops_test`."""

    async def _empty(_target: object) -> frozenset[str]:
        return frozenset()

    monkeypatch.setattr("src.agent.executor.restore_held", _empty)


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> RanchMap:
    """The catalog read is stubbed; the value reads are not.

    Deliberate split: `fetch_catalog` talks MCP over stdio-free transport that respx does
    not model, while the 160 value reads are plain HTTP and are the part with the
    interesting failure modes.
    """

    async def _catalog() -> tuple[RanchMap, str]:
        return CATALOG, "mcp_resource"

    monkeypatch.setattr("src.agent.executor.fetch_catalog", _catalog)
    return CATALOG


@pytest.fixture
async def target(migrated_store: str) -> AsyncIterator[StoreTarget]:
    """A `StoreTarget` on an empty `sw_ops_test`, which is what `main.py` hands `run_tick`."""
    engine = build_engine(migrated_store, schema=SCHEMA_TEST)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("truncate table incidents restart identity"))
    finally:
        await engine.dispose()
    yield StoreTarget(name="test", url=migrated_store, schema=SCHEMA_TEST)


def payload_for(ref: SensorRef, value: object, *, status: str = "online") -> dict[str, object]:
    return {"data": {"id": ref.sensor_id, "type": ref.sensor_type, "locationName": ref.location, "status": status, "latestReading": {"value": value, "recordedAt": "2026-09-10T14:00:00.000Z"}}}


def serve(mock: respx.MockRouter, values: dict[str, object], *, broken: set[str] = frozenset()) -> None:
    for ref in CATALOG.sensors:
        route_ = mock.get(f"{BASE}/sensors/{ref.sensor_id}")
        if ref.sensor_id in broken:
            route_.mock(return_value=httpx.Response(503, json={"error": {"category": "upstream"}}))
        else:
            route_.mock(return_value=httpx.Response(200, json=payload_for(ref, values[ref.sensor_id])))


def finding(sensor_id: str = "alkali-flat-water", *, category: str = "water_low", severity: str = "critical", value: float = 1.4, summary: str = "") -> Finding:
    return Finding(
        sensor_id=sensor_id,
        sensor_type="water-level",
        location="Alkali Flat",
        category=category,
        severity=severity,  # type: ignore[arg-type]
        value=value,
        unit=" gal",
        threshold=2.0,
        observed_at="2026-09-10T14:00:00.000Z",
        summary=summary or f"Alkali Flat: stock-tank level reads {value:g} gal on {sensor_id}.",
    )


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


# =========================================================================== #
# 1. the tick, end to end
# =========================================================================== #
@respx.mock
async def test_a_tick_holds_a_first_sighting_pending_opens_it_on_the_second_and_calls_it_ongoing_after(catalog: RanchMap, target: StoreTarget) -> None:
    """The whole point of the store, exercised through the tick rather than around it. One bad
    read is pending and reaches nobody; the same read twice in a row is an incident and is
    routed exactly once; after that it is ongoing, and a persisting fault re-alarming every
    five minutes is how a service teaches its client to ignore it."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})

    first = await run_tick(tick=1, store=target, now=T0, spend=False)
    assert first.error is None and first.failed_stage is None
    assert (first.sensors_read, first.sensors_failed) == (3, 0)
    assert [f.category for f in first.findings] == ["water_low"]
    assert len(first.pending) == 1 and first.opened == () and first.ongoing == ()
    assert first.routed == {}, "a first sighting reaches no agent and costs nothing"

    second = await run_tick(tick=2, store=target, now=T1, spend=False)
    assert len(second.opened) == 1 and second.pending == () and second.ongoing == ()
    assert second.opened[0].occurrences == 2 and second.opened[0].tick_opened == 2 and second.opened[0].first_seen_at == T0
    assert second.routed == {"water_feed": ("alkali-flat-water:water_low",)}

    third = await run_tick(tick=3, store=target, now=T2, spend=False)
    assert third.opened == () and len(third.ongoing) == 1 and third.ongoing[0].occurrences == 3
    # Nothing new opened, so nothing is handed to a model. This is the entire cost story.
    assert third.routed == {}


@respx.mock
async def test_a_confirmed_fault_that_heals_resolves_but_a_single_bad_read_is_dismissed(catalog: RanchMap, target: StoreTarget) -> None:
    """The debounce's other half. The deployed Sensor API invents a fresh reading per call, so a
    healthy tank reads empty one sweep and fine the next; that is `dismissed`, never alarmed and
    never `resolved`. A fault seen twice and then gone is a real `resolved`."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    await run_tick(tick=1, store=target, now=T0, spend=False)
    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 9.0, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    dice = await run_tick(tick=2, store=target, now=T1, spend=False)
    assert dice.findings == () and dice.resolved == () and len(dice.dismissed) == 1
    assert dice.dismissed[0].status == "dismissed" and dice.dismissed[0].occurrences == 1
    assert "dismissed 1" in summarize(dice)

    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    await run_tick(tick=3, store=target, now=T2, spend=False)
    confirmed = await run_tick(tick=4, store=target, now=T2, spend=False)
    assert len(confirmed.opened) == 1, "the dismissed row did not block a new pending on the same key"
    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 9.0, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    healed = await run_tick(tick=5, store=target, now=T2, spend=False)
    assert len(healed.resolved) == 1 and healed.resolved[0].status == "resolved" and healed.dismissed == ()


@respx.mock
async def test_a_sensor_that_did_not_answer_holds_its_incident_open(catalog: RanchMap, target: StoreTarget) -> None:
    """The rail that matters most in an outage. "No finding" and "no reading" are different
    facts, and conflating them means one upstream failure closes every incident on the
    ranch and reports an all-clear at the worst possible moment."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    await run_tick(tick=1, store=target, now=T0, spend=False)

    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0}, broken={"alkali-flat-water"})
    dark = await run_tick(tick=2, store=target, now=T1, spend=False)

    assert (dark.sensors_read, dark.sensors_failed) == (2, 1)
    assert dark.findings == (), "a failed read produces no finding; it is not a fault"
    assert dark.resolved == () and dark.dismissed == (), "the tank stopped answering, so nothing is known to have healed, and a pending row is held rather than dismissed"


@respx.mock
async def test_exactly_one_tick_line_is_written_and_it_carries_the_counts(catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})

    with structlog.testing.capture_logs() as logs:
        await run_tick(tick=7, store=target, now=T0, spend=False)

    lines = [line for line in logs if line["event"] == "tick"]
    assert len(lines) == 1
    line = lines[0]
    assert (line["sensors_read"], line["sensors_failed"], line["findings"], line["opened"], line["pending"], line["dismissed"]) == (3, 0, 1, 0, 1, 0)
    assert line["failed_stage"] is None and line["error"] is None
    assert line["agents_routed"] == [] and line["catalog_source"] == "mcp_resource"
    assert line["store"] == "test", "which database a tick wrote to has to be on the line"


async def test_an_empty_catalog_fails_the_tick_at_the_catalog_stage(monkeypatch: pytest.MonkeyPatch, target: StoreTarget) -> None:
    """An empty catalog downstream is indistinguishable from a calm ranch, so it is a
    failed tick rather than a quiet one, and nothing is reconciled: resolving every
    incident on the strength of a catalog we could not read is the worst outcome
    available."""

    async def _empty() -> tuple[RanchMap, str]:
        return RanchMap(), "unavailable"

    monkeypatch.setattr("src.agent.executor.fetch_catalog", _empty)

    with structlog.testing.capture_logs() as logs:
        state = await run_tick(tick=1, store=target, now=T0, spend=False)

    assert state.failed_stage == "catalog" and state.error is not None
    assert state.opened == () and state.resolved == ()
    assert len([line for line in logs if line["event"] == "tick"]) == 1, "a failed tick still writes exactly one line"


async def test_a_failing_stage_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """A tick reports its own failure. An exception escaping `run_tick` would take the loop
    down at M4, and one bad afternoon for one stage is not an outage."""

    async def _boom(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("sensor api unreachable")

    monkeypatch.setattr("src.agent.executor.sweep", _boom)

    state = await run_tick(tick=1, store=target, now=T0, spend=False)

    assert state.failed_stage == "sweep"
    assert state.error is not None and "ConnectError" in state.error
    assert "FAILED at sweep" in summarize(state)


@respx.mock
async def test_the_summary_line_names_the_reading_a_human_would_ask_about(first_sight: Settings, catalog: RanchMap, target: StoreTarget) -> None:
    """`--once` is run by a person watching a console. A summary that says "1 finding" and
    nothing else sends them to the JSON to learn anything at all."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    state = await run_tick(tick=1, store=target, now=T0, spend=False)
    text_out = summarize(state)
    assert "3 read" in text_out and "water_feed:1" in text_out
    assert "—" not in text_out


# =========================================================================== #
# 2. routing: a table, and the two ways a table goes wrong
# =========================================================================== #
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
    incidents_in = [
        incident("z-tank:water_low", category="water_low"),
        incident("a-tank:water_low", category="water_low"),
        incident("north-fence:fence_down", category="fence_down"),
    ]
    got = route(incidents_in)
    assert got == {"infrastructure": ("north-fence:fence_down",), "water_feed": ("a-tank:water_low", "z-tank:water_low")}
    assert list(got) == sorted(got), "agent order must not depend on insertion order"
    assert route(list(reversed(incidents_in))) == got


def test_route_honors_the_owner_already_stored_on_the_row() -> None:
    """The store stamps an owner when the incident is written. Re-deriving it here would
    let a routing-table edit silently reassign an incident that a human is already
    working, so the stored value wins."""
    assert route([incident("a:water_low", category="water_low", owner="compliance")]) == {"compliance": ("a:water_low",)}


def test_route_of_nothing_is_nothing_not_five_empty_agents() -> None:
    """A calm tick must fan out to zero agents. Pre-seeding the dict with every agent
    would spend five model calls on nothing, every five minutes, forever."""
    assert route([]) == {}


# =========================================================================== #
# 3. the store: pending -> opened -> ongoing -> resolved (or dismissed), against a real Postgres
# =========================================================================== #
async def test_a_new_finding_is_pending_once_opens_exactly_once_and_is_ongoing_after(store: AsyncSession) -> None:
    """The five statuses, in order, on one row. At the default `INCIDENT_CONFIRM_SWEEPS = 2` a
    finding is pending on its first sweep, opened on its second, and ongoing from the third."""
    first = await reconcile(store, [finding()], tick=1, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T0)
    assert first.counts == {"opened": 0, "ongoing": 0, "resolved": 0, "pending": 1, "dismissed": 0}
    assert first.pending[0].key == "alkali-flat-water:water_low" and first.pending[0].status == "pending"
    assert first.pending[0].tick_opened == 0 and first.pending[0].occurrences == 1, "not opened yet, so no opening tick"

    second = await reconcile(store, [finding()], tick=2, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T1)
    assert second.counts == {"opened": 1, "ongoing": 0, "resolved": 0, "pending": 0, "dismissed": 0}
    assert second.opened[0].tick_opened == 2 and second.opened[0].first_seen_at == T0 and second.opened[0].occurrences == 2

    third = await reconcile(store, [finding()], tick=3, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T2)
    assert third.counts == {"opened": 0, "ongoing": 1, "resolved": 0, "pending": 0, "dismissed": 0}, "a persisting fault is ongoing, never re-alarmed"
    assert third.ongoing[0].occurrences == 3
    assert third.ongoing[0].tick_opened == 2 and third.ongoing[0].tick_last_seen == 3
    assert third.ongoing[0].first_seen_at == T0 and third.ongoing[0].last_seen_at == T2

    # One row for the whole story, not three.
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 1


async def test_a_pending_finding_that_reads_clean_is_dismissed_not_resolved(store: AsyncSession) -> None:
    """One bad draw from a synthesized sensor is not an incident. It was never alarmed, so it
    cannot have healed; `dismissed` keeps the row so the churn rate stays countable and keeps it
    out of every resolved count a human reads."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.counts == {"opened": 0, "ongoing": 0, "resolved": 0, "pending": 0, "dismissed": 1}
    assert result.dismissed[0].resolved_at == T1
    assert await open_incidents(store) == () and await counts_by_status(store) == {"dismissed": 1}

    again = await reconcile(store, [finding()], tick=3, read_sensor_ids=ALL_SENSORS, now=T2)
    assert again.counts["pending"] == 1, "a dismissed row is terminal, so the same key can go pending again"
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 2


async def test_a_pending_finding_whose_sensor_went_dark_is_held_not_dismissed(store: AsyncSession) -> None:
    """Unread is not read-clean, for a pending row exactly as for an opened one. An outage
    during the confirmation window must not quietly dismiss what was about to open."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    blind = await reconcile(store, [], tick=2, read_sensor_ids=(), now=T1)
    assert blind.counts == {"opened": 0, "ongoing": 0, "resolved": 0, "pending": 0, "dismissed": 0}
    assert blind.skipped_unread == ("alkali-flat-water:water_low",)
    confirmed = await reconcile(store, [finding()], tick=3, read_sensor_ids=ALL_SENSORS, now=T2)
    assert confirmed.counts["opened"] == 1, "and the next clean read that still shows the fault opens it"


async def test_the_confirmation_window_is_a_knob_and_one_means_first_sight(store: AsyncSession) -> None:
    """`INCIDENT_CONFIRM_SWEEPS`. 1 is the pre-0003 behaviour; 3 needs three in a row."""
    now = await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0, confirm_sweeps=1)
    assert now.counts["opened"] == 1 and now.opened[0].tick_opened == 1
    await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1, confirm_sweeps=1)

    slow = [await reconcile(store, [finding()], tick=t, read_sensor_ids=ALL_SENSORS, now=T2, confirm_sweeps=3) for t in (3, 4, 5)]
    assert [r.counts["pending"] for r in slow] == [1, 1, 0] and [r.counts["opened"] for r in slow] == [0, 0, 1]


async def test_severity_on_a_pending_row_follows_the_latest_read_when_it_opens(store: AsyncSession) -> None:
    await reconcile(store, [finding(severity="warning", value=5.0)], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [finding(severity="critical", value=1.4)], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)
    assert result.opened[0].severity == "critical" and result.opened[0].last_value == "1.4"


async def test_a_finding_that_stops_appearing_resolves(first_sight: Settings, store: AsyncSession) -> None:
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.counts == {"opened": 0, "ongoing": 0, "resolved": 1, "pending": 0, "dismissed": 0}
    assert result.resolved[0].resolved_at == T1
    assert await open_incidents(store) == (), "a resolved incident is no longer open"
    assert await counts_by_status(store) == {"resolved": 1}


async def test_the_same_fault_returning_after_a_resolve_is_a_new_incident(first_sight: Settings, store: AsyncSession) -> None:
    """History survives. The same tank drying out in March and again in July is two work
    orders, and collapsing them would overwrite the March story."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)
    again = await reconcile(store, [finding()], tick=3, read_sensor_ids=ALL_SENSORS, now=T2)

    assert again.counts == {"opened": 1, "ongoing": 0, "resolved": 0, "pending": 0, "dismissed": 0}
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 2
    assert again.opened[0].tick_opened == 3


async def test_a_sensor_that_did_not_answer_resolves_nothing(first_sight: Settings, store: AsyncSession) -> None:
    """The failure this prevents: the Sensor API has a bad five minutes, the sweep returns
    errors instead of readings, every incident on the ranch closes, and the feed reports an
    all-clear at the exact moment nobody can see anything."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)

    blind = await reconcile(store, [], tick=2, read_sensor_ids=(), now=T1)

    assert blind.counts == {"opened": 0, "ongoing": 0, "resolved": 0, "pending": 0, "dismissed": 0}
    assert blind.skipped_unread == ("alkali-flat-water:water_low",)
    still_open = await open_incidents(store)
    assert len(still_open) == 1 and still_open[0].status == "opened"
    assert still_open[0].last_seen_at == T0, "an unread tick must not restamp the incident as freshly seen"


async def test_other_sensors_still_resolve_while_one_is_dark(first_sight: Settings, store: AsyncSession) -> None:
    """Per sensor, not per tick. One dark sensor must not freeze the whole ledger."""
    await reconcile(store, [finding(), finding("east-allotment-fence", category="fence_down")], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=("east-allotment-fence",), now=T1)

    assert [i.sensor_id for i in result.resolved] == ["east-allotment-fence"]
    assert result.skipped_unread == ("alkali-flat-water:water_low",)


async def test_severity_follows_the_current_reading_on_an_ongoing_incident(first_sight: Settings, store: AsyncSession) -> None:
    """Code owns severity at every tick, not just the first one. A tank that fills back to
    a warning is still one incident, described accurately."""
    await reconcile(store, [finding(severity="critical", value=1.4)], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [finding(severity="warning", value=5.0)], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.ongoing[0].severity == "warning"
    assert result.ongoing[0].last_value == "5"
    row = (await store.execute(text("select severity, last_value, status from incidents"))).one()
    assert tuple(row) == ("warning", "5", "ongoing")


async def test_two_categories_on_one_sensor_are_two_incidents(first_sight: Settings, store: AsyncSession) -> None:
    """A degraded probe reporting a dry tank is two work orders for two different people.
    The key is `sensor:category`, so they never collapse."""
    result = await reconcile(store, [finding(), finding(category="sensor_degraded", severity="warning")], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    assert {i.key for i in result.opened} == {"alkali-flat-water:water_low", "alkali-flat-water:sensor_degraded"}


async def test_a_gate_value_is_stored_as_open_not_as_true(first_sight: Settings, store: AsyncSession) -> None:
    gate = Finding(sensor_id="coyote-draw-gate", sensor_type="gate", location="Coyote Draw", category="gate_open", severity="warning", value=True, summary="Coyote Draw: gate coyote-draw-gate reads open.")
    result = await reconcile(store, [gate], tick=1, read_sensor_ids=("coyote-draw-gate",), now=T0)
    assert result.opened[0].last_value == "open"


async def test_the_owner_is_recorded_when_routing_supplies_one(first_sight: Settings, store: AsyncSession) -> None:
    result = await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0, owners={"alkali-flat-water:water_low": "water_feed"})
    assert result.opened[0].owner == "water_feed"


async def test_the_partial_unique_index_forbids_two_live_incidents_on_one_key(store: AsyncSession) -> None:
    """Enforced in Postgres rather than by application code remembering to check first.
    If this fails, migration 0001 lost its partial index and duplicate alarms are one
    concurrent tick away."""
    row = {
        "incident_key": "alkali-flat-water:water_low",
        "sensor_id": "alkali-flat-water",
        "sensor_type": "water-level",
        "location": "Alkali Flat",
        "category": "water_low",
        "severity": "critical",
        "status": "opened",
        "summary": "s",
        "first_seen_at": T0,
        "last_seen_at": T0,
    }
    await store.execute(incidents.insert(), row)
    with pytest.raises(IntegrityError):
        await store.execute(incidents.insert(), {**row, "status": "ongoing"})
    await store.rollback()
    await store.execute(incidents.insert(), row)
    with pytest.raises(IntegrityError):
        await store.execute(incidents.insert(), {**row, "status": "pending"})
    await store.rollback()
    # Two terminal rows on one key are history, not a collision.
    await store.execute(incidents.insert(), {**row, "status": "dismissed"})
    await store.execute(incidents.insert(), {**row, "status": "resolved"})
    await store.execute(incidents.insert(), {**row, "status": "pending"})
    await store.commit()


async def test_the_connection_can_only_see_the_agent_schema(store: AsyncSession) -> None:
    """The live half of the schema guard. This database also holds the upstream project's
    `farm`, `feed`, `animal_care`, and `sensor` schemas, and this connection cannot reach
    any of them without qualifying, which nothing in this repo does."""
    assert (await store.execute(text("select current_schemas(false)"))).scalar_one() == [SCHEMA_TEST]
    assert (await store.execute(text("select current_setting('search_path')"))).scalar_one() == SCHEMA_TEST


async def test_the_migration_put_alembic_version_in_our_schema(store: AsyncSession) -> None:
    """Alembic's default drops `alembic_version` into the first schema on the search path.
    On this database that has to be `sw_ops_test`, not a table belonging to the upstream
    project's own migrations."""
    found = (await store.execute(text("select count(*) from information_schema.tables where table_schema = :s and table_name = 'alembic_version'"), {"s": SCHEMA_TEST})).scalar_one()
    assert found == 1


# =========================================================================== #
# 4. the schema guards. No database required, and that is the point
# =========================================================================== #
def test_only_the_two_agent_schemas_are_allowed() -> None:
    assert assert_agent_schema(SCHEMA) == SCHEMA
    assert assert_agent_schema(SCHEMA_TEST) == SCHEMA_TEST
    assert ALLOWED_SCHEMAS == {SCHEMA, SCHEMA_TEST}


@pytest.mark.parametrize("schema", sorted(RANCH_SCHEMAS))
def test_a_ranch_schema_is_refused_by_name(schema: str) -> None:
    """The credentials that reach `sw_ops` also have write access to `farm`, `feed`,
    `animal_care`, and `sensor` in the same database. The only way to reach ranch data
    from this repo is over HTTP through the deployed APIs."""
    with pytest.raises(SchemaGuardError, match="ranch schema"):
        assert_agent_schema(schema)


def test_public_and_typos_are_refused_too() -> None:
    for name in ("public", "sw_op", "sw_ops_prod", "", "SW_OPS"):
        with pytest.raises(SchemaGuardError):
            assert_agent_schema(name)


def test_only_the_test_schema_is_ever_droppable() -> None:
    """`sw_ops` holds the incident history the demo reads from. A DROP a test can reach is
    a DROP that eventually runs against the wrong database."""
    assert assert_droppable_schema(SCHEMA_TEST) == SCHEMA_TEST
    with pytest.raises(SchemaGuardError, match="Only 'sw_ops_test'"):
        assert_droppable_schema(SCHEMA)


def test_the_engine_factory_refuses_a_foreign_schema() -> None:
    with pytest.raises(SchemaGuardError):
        build_engine("postgresql+asyncpg://u@localhost/db", schema="farm")


def test_the_connection_pins_search_path_to_one_schema() -> None:
    """Not remembering to qualify a table name; being unable to reach anything else. The
    live proof that this reaches Postgres is in the store suite above; this is the wiring."""
    args = connect_args_for(SCHEMA)
    assert args["server_settings"]["search_path"] == SCHEMA
    assert args["statement_cache_size"] == 0, "pgBouncer breaks asyncpg's prepared statement cache"
    with pytest.raises(SchemaGuardError):
        connect_args_for("animal_care")


def test_a_supabase_url_is_a_hard_failure_for_tests() -> None:
    """Failed, not skipped. A skip would let a prod-pointing test config sit undetected
    until the run that drops the schema the demo reads from."""
    for url in (
        "postgresql+asyncpg://postgres:pw@aws-0-us-west-2.pooler.supabase.com:5432/postgres",
        "postgresql+asyncpg://postgres:pw@db.abcdefgh.supabase.co:5432/postgres",
    ):
        with pytest.raises(SchemaGuardError, match="Supabase"):
            assert_local_test_url(url)
    with pytest.raises(SchemaGuardError, match="empty"):
        assert_local_test_url("")
    assert assert_local_test_url("postgresql+asyncpg://postgres@localhost:5432/farm_systems_test").endswith("farm_systems_test")


# --- the grep: no SQL in this repo names a ranch schema --------------------- #
NAMES = "|".join(sorted(RANCH_SCHEMAS))
PATTERNS = (
    re.compile(rf'(?i)\b(?:from|join|into|update|table|truncate)\s+"?(?:{NAMES})"?\.'),
    re.compile(rf'(?i)\b(?:create|drop|alter)\s+schema\s+(?:if\s+(?:not\s+)?exists\s+)?"?(?:{NAMES})"?'),
    re.compile(rf'(?i)\bsearch_path\s*(?:to|=)\s*"?(?:{NAMES})"?'),
)

SCANNED = ("src/**/*.py", "alembic/**/*.py", "alembic/**/*.mako", "tests/**/*.py", "main.py", "alembic.ini", "**/*.sql")


def test_the_detector_actually_detects() -> None:
    """A positive control first. A grep rail that cannot fail is a rail that proves the
    repo is clean of nothing at all."""
    offenders = [
        "select * from farm.animals",
        'UPDATE "feed".rations set x = 1',
        "insert into animal_care.observations values (1)",
        "drop schema sensor cascade",
        "SET search_path TO farm",
    ]
    for line in offenders:
        assert any(p.search(line) for p in PATTERNS), f"the detector missed {line!r}"


def test_no_sql_in_this_repo_names_a_ranch_schema() -> None:
    """`sw_ops` lives in the same database as the ranch's schemas, and the connection can
    write to all of them. This repo adds no endpoints and reads no ranch tables: the only
    way to ranch data is over HTTP through the deployed APIs."""
    here = Path(__file__).resolve()
    hits: list[str] = []
    for pattern in SCANNED:
        for path in REPO_ROOT.glob(pattern):
            if path.resolve() == here or ".venv" in path.parts:
                continue  # this file holds the positive control above
            body = path.read_text(encoding="utf-8")
            for regex in PATTERNS:
                for match in regex.finditer(body):
                    line_no = body[: match.start()].count("\n") + 1
                    hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_no}: {match.group(0)!r}")
    assert hits == [], "SQL naming a ranch schema:\n  " + "\n  ".join(hits)


def test_the_table_definition_carries_no_schema_of_its_own() -> None:
    """One definition, two schemas, decided by the connection. A `schema=` here is how the
    test suite ends up writing to prod."""
    assert metadata.schema is None
    assert metadata.tables["incidents"].schema is None


# =========================================================================== #
# 5. config and logging: the instrument gets tests first
# =========================================================================== #
def test_trailing_slash_is_stripped_from_every_upstream() -> None:
    """`f"{base}/sensors"` with a trailing slash yields `//sensors`, which API Gateway
    answers with a 404 that reads like a missing route rather than a formatting bug."""
    s = Settings(mcp_url="https://x/", farm_api="https://f/", feed_api="https://d/", sensor_api="https://s/", care_api="https://c/", _env_file=None)
    assert (s.mcp_url, s.farm_api, s.feed_api, s.sensor_api, s.care_api) == ("https://x", "https://f", "https://d", "https://s", "https://c")


def test_missing_upstreams_reports_all_of_them_at_once() -> None:
    """All of them, not just the first: a config error should take one round trip to fix,
    not four runs discovering one blank variable at a time."""
    s = Settings(mcp_url="https://x", farm_api="", feed_api="", sensor_api="https://s", care_api="", _env_file=None)
    assert set(s.missing_upstreams()) == {"farm_api", "feed_api", "care_api"}
    assert Settings(mcp_url="a", farm_api="b", feed_api="c", sensor_api="d", care_api="e", _env_file=None).missing_upstreams() == []


def test_bare_postgres_urls_are_upgraded_to_the_asyncpg_driver() -> None:
    """SQLAlchemy picks its DBAPI from the scheme. Bare `postgresql://` means psycopg2,
    which is not installed, and the failure reads like a missing dependency rather than a
    URL that needed six characters added to it."""
    s = Settings(database_url="postgresql://u:p@supabase.example:5432/postgres", database_url_test="postgres://postgres@localhost:5432/farm_systems_test", _env_file=None)
    assert s.database_url.startswith("postgresql+asyncpg://u:p@")
    assert s.database_url_test.startswith("postgresql+asyncpg://postgres@localhost")


def test_an_explicit_driver_is_left_alone_and_a_blank_stays_blank() -> None:
    s = Settings(database_url="postgresql+asyncpg://u@h/db", database_url_test="", _env_file=None)
    assert s.database_url == "postgresql+asyncpg://u@h/db"
    assert s.database_url_test == ""


def test_chaos_cohort_parses_and_tolerates_whitespace() -> None:
    s = Settings(chaos_animal_cohort=" cow-0901, cow-0902 ,, cow-0903 ", _env_file=None)
    assert s.chaos_cohort == ("cow-0901", "cow-0902", "cow-0903")


def test_empty_chaos_cohort_is_empty_not_a_single_blank() -> None:
    """An empty cohort must mean "no animal may be mutated", never a cohort of one
    animal named "". A guard that parses to `('',)` is a guard that passes nothing."""
    assert Settings(chaos_animal_cohort="", _env_file=None).chaos_cohort == ()


def test_secrets_are_redacted_and_bulk_bodies_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.logger.get_settings", lambda: Settings(log_transcripts=False, _env_file=None))

    out = _redact(None, "info", {"anthropic_api_key": "sk-ant-real", "database_url": "postgres://u:p@h/db", "prompt": "x" * 5000, "tick": 4})

    assert out["anthropic_api_key"] == "[redacted]"
    assert out["database_url"] == "[redacted]"
    assert "sk-ant-real" not in json.dumps(out)
    assert out["tick"] == 4, "redaction must not touch ordinary fields"
    # A marker, not a deletion: a reader has to be able to tell "there was a prompt we
    # chose not to store" from "there was no prompt."
    assert out["prompt"].startswith("[omitted")


def test_transcripts_flag_lets_bulk_bodies_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.logger.get_settings", lambda: Settings(log_transcripts=True, _env_file=None))
    out = _redact(None, "info", {"prompt": "the real prompt", "anthropic_api_key": "sk-ant-real"})
    assert out["prompt"] == "the real prompt"
    assert out["anthropic_api_key"] == "[redacted]", "a secret is redacted even with transcripts on"


def test_a_stdlib_log_record_renders_like_every_other_line() -> None:
    """The M1-caught M0 defect. httpx, sqlalchemy, and alembic log through the stdlib, and
    without a `foreign_pre_chain` their records skip the processors: the message lands
    under `event` while both renderers look for `msg`, and the line prints with no level,
    no timestamp, and no `run_id` to join on. Found by reading the output of a migration
    this repo had just documented as working."""
    import logging

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, structlog.processors.JSONRenderer()],
        foreign_pre_chain=_foreign_chain(),
    )
    record = logging.LogRecord("alembic.runtime.migration", logging.INFO, __file__, 1, "Running upgrade -> 0001", None, None)

    payload = json.loads(formatter.format(record))

    assert payload["msg"] == "Running upgrade -> 0001"
    assert "event" not in payload, "a foreign record under `event` is invisible to both renderers"
    assert payload["level"] == "info"
    assert payload["ts"].endswith("Z")


def test_timestamp_has_millisecond_precision_and_z_suffix() -> None:
    """Must match the four upstream services exactly. Lexicographic ordering is relied
    upon, and sorting mixed precision misorders silently."""
    ts = utc_now_iso()
    assert ts.endswith("Z") and len(ts) == len("2026-09-10T14:30:00.000Z"), ts
    assert ts[-5] == "." or ts[19] == ".", ts


def test_flatten_exception_unwraps_a_task_group() -> None:
    """`unhandled errors in a TaskGroup (1 sub-exception)` is the same string whether
    the host refused the connection or the server returned a 401. Flatten to the leaf."""
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError("All connection attempts failed")])
    assert flatten_exception(group) == "ConnectionRefusedError: All connection attempts failed"


def test_flatten_exception_handles_nesting() -> None:
    inner = BaseExceptionGroup("inner", [ValueError("bad value")])
    outer = BaseExceptionGroup("outer", [inner, TimeoutError("timed out")])
    assert flatten_exception(outer) == "ValueError: bad value; TimeoutError: timed out"


def test_flatten_exception_deduplicates() -> None:
    """160 concurrent reads failing the same way otherwise produce 160 identical leaves
    and a log line nobody scrolls to the end of."""
    group = BaseExceptionGroup("g", [ConnectionRefusedError("refused") for _ in range(160)])
    assert flatten_exception(group) == "ConnectionRefusedError: refused"


def test_flatten_exception_survives_a_message_less_error() -> None:
    assert flatten_exception(BaseExceptionGroup("g", [RuntimeError()])) == "RuntimeError"


GROUPED = {
    "data": {
        "locations": [
            {"locationName": "Alkali Flat", "sensors": [{"id": "alkali-flat-water", "type": "water-level", "status": "ok", "coordinates": {"x": 12.0, "y": 40.5}}]},
            {"locationName": "East Allotment", "sensors": [{"id": "east-allotment-fence", "type": "fence-voltage", "status": "ok"}]},
        ]
    }
}
FLAT = [{"id": "home-place-diesel", "type": "fuel-level", "locationName": "Home Place", "status": "ok"}]


def test_parses_the_location_grouped_shape() -> None:
    m = parse_ranch_map(GROUPED)
    assert len(m.sensors) == 2
    assert m.locations == ("Alkali Flat", "East Allotment")
    assert m.types == ("fence-voltage", "water-level")
    tank = m.get("alkali-flat-water")
    assert tank is not None and tank.location == "Alkali Flat" and tank.coordinates == {"x": 12.0, "y": 40.5}


def test_parses_a_flat_list_too() -> None:
    """Tolerant on purpose: a harmless upstream reshape should degrade into a different
    code path, not into an empty map. An empty map looks exactly like a calm ranch."""
    m = parse_ranch_map(FLAT)
    assert len(m.sensors) == 1 and m.sensors[0].location == "Home Place"


def test_filters_by_type_and_location() -> None:
    """`GET /sensors` takes pagination only, so type filtering has to happen here."""
    m = parse_ranch_map(GROUPED)
    assert [s.sensor_id for s in m.of_type("water-level")] == ["alkali-flat-water"]
    assert [s.sensor_id for s in m.at_location("East Allotment")] == ["east-allotment-fence"]
    assert m.of_type("wellhead-pressure") == ()


def test_entries_without_an_id_are_dropped() -> None:
    m = parse_ranch_map([{"type": "water-level"}, {"id": "ok-1", "type": "gate"}])
    assert [s.sensor_id for s in m.sensors] == ["ok-1"]


def test_backoff_is_jittered_and_capped() -> None:
    """Jittered because the sweep fires 160 requests at once: on a shared outage,
    unjittered retries rebuild the exact thundering herd backoff was added to avoid."""
    import random

    rng = random.Random(1)
    delays = [backoff_delay(a, rng=rng) for a in range(8)]
    assert all(0 <= d <= 60.0 for d in delays)
    assert len(set(delays)) > 1, "identical delays across attempts means no jitter"
    assert backoff_delay(50, base=1.0, cap=60.0, rng=rng) <= 60.0


def test_no_upstream_url_is_hardcoded_outside_config_and_examples() -> None:
    """The deployed ranch has moved hosts before. A URL in a source file is a URL you
    find with grep at the worst possible moment."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in [*REPO_ROOT.glob("src/**/*.py"), REPO_ROOT / "main.py"]
        if "execute-api" in p.read_text(encoding="utf-8") or "lambda-url" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"hardcoded upstream URL in {offenders}; read it from config instead"


# =========================================================================== #
# 6. the work order: the rails on what a model is allowed to have said
# =========================================================================== #
# Nothing here calls a model. `conftest.no_model_calls` makes sure of it, and the rails that
# need a response build a `ModelResponse` directly, which is the honest way to test a parser.
#
# What they protect, in one sentence each: severity stays triage's, an all-clear can never
# ship, a cited rule exists, and the prose is graded for grounding rather than trusted
# because the label happened to match.
WATER_SOP = (REPO_ROOT / "data" / "knowledge_base" / "water.md").read_text(encoding="utf-8")
FEED_SOP = (REPO_ROOT / "data" / "knowledge_base" / "feed.md").read_text(encoding="utf-8")

TANK_INCIDENT = Incident(
    key="alkali-flat-water:water_low",
    sensor_id="alkali-flat-water",
    sensor_type="water-level",
    location="Alkali Flat",
    category="water_low",
    severity="critical",
    status="opened",
    summary="stock-tank level at Alkali Flat is 1.9 gal, below the critical line of 2 gal",
    last_value="1.9 gal",
    unit=" gal",
    threshold=2.0,
    first_seen_at=T0,
    last_seen_at=T0,
    owner="water_feed",
)

TANK_PACKET = EvidencePacket(
    incident=TANK_INCIDENT,
    history=tuple(HistoryPoint(recorded_at=f"2026-09-10T13:{minute:02d}:00.000Z", value=value) for minute, value in ((10, 0.8), (20, 3.9), (30, 2.1), (40, 1.2))),
    siblings=(
        SiblingReading(sensor_id="alkali-flat-battery", sensor_type="battery-level", status="online", value=82.5),
        SiblingReading(sensor_id="alkali-flat-temp", sensor_type="temperature", status="online", value=71.6),
        SiblingReading(sensor_id="alkali-flat-water-2", sensor_type="water-level", status="online", value=16.7),
    ),
    pasture=PastureContext(pasture_id="alkali-flat", name="Alkali Flat", acreage=2400, fence_type="barbed-wire", status="open", head_count=111),
    sop_name="water.md",
    sop_text=WATER_SOP,
)

#: A real Opus answer to this packet, captured from the first live M2 call on 2026-09-10
#: (`us.anthropic.claude-opus-5`, `stop_reason=tool_use`, 5,555 in / 1,137 out). Recorded
#: verbatim rather than invented, so the graded rail below is calibrated against prose a model
#: actually produced and a future prompt change can be diffed against a known-good answer.
RECORDED_ANSWER: dict[str, object] = {
    "severity_echo": "critical",
    "headline": "Alkali Flat tank dry at 1.9 gal, 111 head: haul water this shift and check the well",
    "assessment": (
        "alkali-flat-water reads 1.9 gal against the critical line of 2 gal, so treat that tank as dry with 111 head on 2400 acres behind it. "
        "The second tank on the same ground, alkali-flat-water-2, reads 16.7 gal in the same sweep, which points at this tank or its supply line "
        "rather than the whole pasture being off water. The 12-reading series on alkali-flat-water swings between 0.8 and 3.9 gal with no direction "
        "to it, which reads like a float or level fault. alkali-flat-temp at 71.6 F is not driving heat demand, and alkali-flat-battery at 82.5% "
        "does not look like a dead solar site."
    ),
    "actions": [
        "Load water and roll to Alkali Flat this shift, 111 head, do not wait to confirm the reading first",
        "Before leaving the yard, check the well or windmill feeding the Alkali Flat tanks",
        "Inspect the float, valve, and supply line on the low tank",
        "Re-read the tank later today and again tomorrow; do not close on one good reading",
    ],
    "rules_cited": ["WATER-01", "WATER-05"],
    "escalate": False,
    "escalate_reason": "",
    "unknowns": ["Tank capacity on alkali-flat-water, so no gallons needed or days of water can be figured"],
}


def _answer(**over: object) -> dict[str, object]:
    return dict(RECORDED_ANSWER) | over


def _response(payload: dict[str, object] | None, *, finish_reason: str = "tool_use", error: str = "") -> ModelResponse:
    return ModelResponse(provider="bedrock", model="us.anthropic.claude-opus-5", finish_reason=finish_reason, payload=payload, input_tokens=5555, output_tokens=1137, latency_ms=15870, error=error)


def _order(payload: dict[str, object] | None, **kw: object) -> WorkOrder:
    return to_work_order(packet=TANK_PACKET, agent="water_feed", response=_response(payload, **kw))  # type: ignore[arg-type]


def test_the_citable_rules_are_the_ones_actually_in_the_packet() -> None:
    assert citable_rules(WATER_SOP) == {"WATER-01", "WATER-02", "WATER-03", "WATER-04", "WATER-05", "WATER-06"}
    assert citable_rules(FEED_SOP) == {"FEED-01", "FEED-02", "FEED-03", "FEED-04"}
    assert citable_rules("") == frozenset(), "a packet with no SOP makes every citation invented, which is the point"


def test_a_real_recorded_answer_passes_every_rail() -> None:
    """The calibration rail. If a prompt change makes this fail, the rails did not get
    stricter, the answer got worse."""
    order = _order(_answer())
    assert order.violations == ()
    assert order.status == "ok" and order.shippable


def test_the_stored_severity_is_triages_even_when_the_model_argues_with_it() -> None:
    """Severity is `triage.py`'s, always. The echo is checked and then thrown away: storing
    what the model said creates a second answer to a question that already has one."""
    order = _order(_answer(severity_echo="warning"))
    assert order.severity == "critical", "the incident's severity, never the echo"
    assert order.severity_echo == "warning", "and the disagreement is recorded rather than smoothed over"
    assert "severity_mismatch" in order.violations
    assert order.status == "rejected"


def test_a_work_order_with_no_real_action_in_it_is_an_all_clear_and_cannot_ship() -> None:
    """Code already flagged this incident, so "monitor and see" is a contradiction rather than
    a finding. Do not relax this one (`src/agent/CLAUDE.md`)."""
    order = _order(_answer(actions=["Continue to monitor the tank", "No further action needed at this time"]))
    assert "all_clear" in order.violations and order.status == "rejected"


def test_an_all_clear_headline_cannot_ship_either() -> None:
    order = _order(_answer(headline="Alkali Flat: no action required, tank is within normal range"))
    assert "all_clear" in order.violations and order.status == "rejected"


def test_an_accurate_sentence_about_a_healthy_sibling_is_not_an_all_clear() -> None:
    """Why the rail reads the actions list and not the prose. "the second tank is fine" is
    correct, useful, and the diagnosis; a prose matcher would reject it, and a rail that
    punishes accurate writing gets switched off inside a week."""
    order = _order(_answer(assessment="The second tank alkali-flat-water-2 at 16.7 gal is fine, so nothing is wrong with the supply to the pasture as a whole. alkali-flat-water at 1.9 gal is the problem."))
    assert "all_clear" not in order.violations


def test_a_rule_id_that_is_not_in_the_packet_is_rejected() -> None:
    """`WATER-07` does not exist. An invented id is worse than no citation, because the next
    person goes looking for it."""
    with capture_logs() as logs:
        order = _order(_answer(rules_cited=["WATER-01", "WATER-07"]))
    assert "invented_rule" in order.violations and order.status == "rejected"
    assert any(entry["event"] == "invented_rule_ids" for entry in logs)


def test_citing_nothing_is_recorded_but_does_not_block() -> None:
    """A quality signal, not a safety one. A work order that acts correctly and forgets its
    citation is still a truck going to the right tank."""
    order = _order(_answer(rules_cited=[]))
    assert order.violations == ("no_rule_cited",) and order.status == "ok"


def test_prose_that_never_names_the_sensor_is_flagged() -> None:
    order = _order(_answer(headline="Haul water to Alkali Flat", assessment="The tank is nearly empty and 111 head are on it. Send water now."))
    assert "sensor_not_named" in order.violations


def test_a_call_that_never_answered_still_produces_a_work_order() -> None:
    """A tick that silently drops an incident is one nobody is paged about, which is
    indistinguishable from a ranch with nothing wrong."""
    order = _order(None, finish_reason="transport_error", error="ExpiredTokenException: the session credential expired")
    assert order.status == "no_answer"
    assert order.violations == ("no_payload", "transport_error")
    assert order.severity == "critical", "the incident is still critical when the model is unreachable"
    assert "ExpiredToken" in order.assessment


def test_a_truncated_answer_is_a_config_bug_and_says_so() -> None:
    """`max_tokens` and `tool_use` look identical in the response text. That distinction is
    worth more than any other field in `agent.jsonl`."""
    response = _response(None, finish_reason="max_tokens")
    assert response.truncated and not response.ok
    order = to_work_order(packet=TANK_PACKET, agent="water_feed", response=response)
    assert order.status == "no_answer" and "truncated" in order.assessment


def test_a_half_filled_tool_call_is_no_answer_and_not_a_rail_failure() -> None:
    """The live M3 tick's actual defect, caught on the supervisor and fixed in both places. A tool
    call cut off at `max_tokens` still arrives carrying its `input` dict, partially filled, so a
    check for `payload is None` sends the half-answer through the rails. It then fails as
    `all_clear` and the log blames the model for what is a budget bug."""
    order = _order(_answer(actions=[], unknowns=[]), finish_reason="max_tokens")
    assert order.status == "no_answer", "not 'rejected': nothing was judged, so there is nothing to reject"
    assert order.violations == ("no_payload", "max_tokens"), "and the receipt says which of the two it was"
    assert "all_clear" not in order.violations


# --- graded, not asserted --------------------------------------------------- #
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
#: Stripped from **both** sides before either is tokenized. A clock reading is not a quotable
#: quantity, and leaving it on the page makes almost any two-digit number look grounded:
#: `13:40:00` grounds "40", which is how the first version of this grader passed an invented head
#: count. Leaving it in the prose fails the opposite way: an agent told to write for an auditor
#: eight months out quotes the date it was given, and `2026-09-10T20:08:41Z` then grades as five
#: invented numbers. Dropping times everywhere means a fabricated timestamp goes ungraded, which
#: is a real hole and a small one, because nothing in the prose is anchored to a time anyway.
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?|\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z?")


def ungrounded_numbers(order: WorkOrder, packet: EvidencePacket) -> set[str]:
    """Every number in the prose that is not a number on the page it was given.

    The grading rail `tests/CLAUDE.md` asks for, and the only check here that reads the reason
    text rather than a label. A model can echo severity correctly and still write "the tank
    holds about 300 gallons" about a tank whose capacity is nowhere in the packet, and a
    label-only assertion passes happily while the product is useless.

    Compared as **number tokens on both sides**, never as substrings. `40` is inside `2400`
    and inside every `:40:` timestamp, so a substring test grounds an invented number against
    an unrelated one and grades nothing.
    """
    grounded = set(_NUMBER.findall(_TIMESTAMP.sub(" ", packet.render())))
    prose = _TIMESTAMP.sub(" ", f"{order.headline} {order.assessment} {' '.join(order.actions)}")
    return set(_NUMBER.findall(prose)) - grounded


def test_a_real_answer_quotes_only_numbers_that_are_on_the_page() -> None:
    order = _order(_answer())
    assert ungrounded_numbers(order, TANK_PACKET) == set()
    assert TANK_INCIDENT.sensor_id in order.assessment, "name the sensor behind the number"
    assert "1.9" in order.assessment, "and quote the reading triage actually judged"


def test_the_grader_catches_an_invented_number() -> None:
    """The grader has to be able to fail or it is decoration. 300 and 40 are nowhere in the
    packet, and both are exactly the kind of number that reads as authority."""
    order = _order(_answer(assessment="alkali-flat-water at 1.9 gal is roughly 300 gallons short of full and will run 40 head short by dark."))
    assert ungrounded_numbers(order, TANK_PACKET) == {"300", "40"}


def test_quoting_the_date_off_the_page_is_not_an_invented_number() -> None:
    """The other direction, found by the `compliance` transcript below. That agent is briefed to
    write for an auditor eight months out, so it quotes the timestamp it was given, and a grader
    that strips times from the page but not from the prose calls the date five inventions."""
    order = _order(_answer(assessment="alkali-flat-water read 1.9 gal at 2026-09-10T13:40:00.000Z, first seen 13:10Z."))
    assert ungrounded_numbers(order, TANK_PACKET) == set()


# --- the fan-out ------------------------------------------------------------ #
async def test_no_packets_means_no_calls_at_all() -> None:
    assert await run_water_feed([]) == ()


async def test_one_agent_raising_is_not_an_outage_for_the_other_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    """`src/agent/CLAUDE.md`: a tick survives one sub-agent raising. Eleven incidents behind
    one unhandled error is ten pastures nobody hears about."""
    second = EvidencePacket(incident=TANK_INCIDENT.model_copy(update={"key": "windmill-pasture-water:water_low", "sensor_id": "windmill-pasture-water"}), sop_name="water.md", sop_text=WATER_SOP)

    async def _judge(packet: EvidencePacket, **_kw: object) -> WorkOrder:
        if packet.incident.sensor_id == "alkali-flat-water":
            raise RuntimeError("bedrock said no")
        return to_work_order(packet=packet, agent="water_feed", response=_response(_answer()))

    monkeypatch.setattr("src.agent.workers.judge_packet", _judge)
    orders = await run_water_feed([TANK_PACKET, second])
    assert [o.status for o in orders] == ["no_answer", "ok"]
    assert orders[0].violations == ("worker_raised",)


# --- the provider registry and the receipt ---------------------------------- #
def test_a_key_picks_the_first_party_api_and_no_key_falls_back_to_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two credentials, one call path. The model id carries Bedrock's region scope while
    `TIER2_MODEL` stays the first-party spelling, so one setting is right for both."""
    monkeypatch.setattr("src.models.llm_client.get_settings", lambda: Settings(tier2_model="claude-opus-5", anthropic_api_key="", _env_file=None))
    assert resolve_provider("sk-ant-test") == ("anthropic", "claude-opus-5")
    assert resolve_provider() == ("bedrock", "us.anthropic.claude-opus-5")


def test_an_already_scoped_model_id_is_not_scoped_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.models.llm_client.get_settings", lambda: Settings(tier2_model="us.anthropic.claude-opus-5", anthropic_api_key="", _env_file=None))
    assert resolve_provider() == ("bedrock", "us.anthropic.claude-opus-5")


async def test_finish_reason_is_logged_before_the_answer_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rail that keeps "too weak" distinguishable from "never answered". A truncated
    response fails every check below it and the receipt still has to exist: without this line
    the only trace of a rejected answer is the absence of a good one."""

    class _Messages:
        @staticmethod
        async def create(**_kw: object) -> object:
            return SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", name="write_work_order", input='{"headline": "Alkali Fl')],
                stop_reason="max_tokens",
                usage=SimpleNamespace(input_tokens=5555, output_tokens=2048),
            )

    monkeypatch.setattr("src.models.llm_client.build_client", lambda *_a, **_k: (SimpleNamespace(messages=_Messages()), "bedrock", "us.anthropic.claude-opus-5"))
    with capture_logs() as logs:
        response = await call_tier2(agent="water_feed", system="s", user="u", schema=WORK_ORDER_SCHEMA, schema_name="write_work_order", max_tokens=64)

    receipt = next(entry for entry in logs if entry["event"] == "agent_call")
    assert receipt["finish_reason"] == "max_tokens" and receipt["tier"] == 2 and receipt["provider"] == "bedrock"
    assert response.truncated and response.payload is None
    assert any(entry["event"] == "tier2_truncated" for entry in logs), "and it says out loud that this is a config bug"


async def test_an_unknown_reasoning_effort_falls_back_loudly_rather_than_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """`reasoning_effort` is an explicit per-call argument (`src/models/CLAUDE.md`). A knob
    that silently changes verdicts is how a landed fix once failed to reach the rung it was
    written for."""
    captured: dict[str, object] = {}

    class _Messages:
        @staticmethod
        async def create(**kw: object) -> object:
            captured.update(kw)
            return SimpleNamespace(content=[], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    monkeypatch.setattr("src.models.llm_client.build_client", lambda *_a, **_k: (SimpleNamespace(messages=_Messages()), "bedrock", "m"))
    with capture_logs() as logs:
        await call_tier2(agent="water_feed", system="s", user="u", reasoning_effort="maximum")
    assert any(entry["event"] == "unknown_reasoning_effort" for entry in logs)
    assert "thinking" not in captured, "an unrecognized effort must not quietly enable a thinking budget"


def test_thinking_is_off_by_default_and_its_budget_never_shares_the_output_ceiling() -> None:
    """Turning thinking off is free exactly when the model is not the one classifying, and
    sizing `max_tokens` at or below `budget_tokens` is the classic way to get a response that
    is all reasoning and no content."""
    assert THINKING_BUDGET["none"] == 0
    assert THINKING_BUDGET["high"] > THINKING_BUDGET["low"] > 0


# --- the brief -------------------------------------------------------------- #
def test_the_brief_carries_the_inherited_rules_and_the_agents_own_patch() -> None:
    brief = system_prompt("water_feed")
    assert "SEVERITY IS NOT YOURS" in brief
    assert "NEVER WRITE AN ALL-CLEAR" in brief
    assert "QUOTE ONLY WHAT IS ON THE PAGE" in brief
    assert "CITE THE RULE YOU ACTED ON" in brief
    assert "YOUR PATCH: water and feed" in brief


def test_an_agent_with_no_mandate_is_loud_about_it() -> None:
    """The inherited rules alone read like a complete brief and ground nothing: the model would
    know it must cite a rule and not what patch it works. After M3 briefed the four responders,
    `chaos` is the one agent that legitimately still trips this, and it arrives at M5."""
    with capture_logs() as logs:
        system_prompt("chaos")
    assert any(entry["event"] == "no_mandate_for_agent" for entry in logs)


def test_every_responder_has_a_brief_and_chaos_deliberately_does_not() -> None:
    """`docs/Plan.md` says "the five briefs" and this is four on purpose: `chaos` stays out of
    the graph until M5 and authors nothing a human reads before it has real animal events. The
    absence is asserted rather than assumed, so M5 adding one is a deliberate edit here."""
    assert set(MANDATES) == set(RESPONDERS)
    assert "chaos" not in MANDATES
    assert set(RESPONDERS) | {"chaos"} == set(AGENTS), "the responders plus chaos are the five agents; a sixth needs a brief and a slice"


@pytest.mark.parametrize("agent", RESPONDERS)
def test_every_brief_names_its_patch_and_what_is_not_its_patch(agent: str) -> None:
    """The third part is the load-bearing one. Four agents read one shared sweep, so a brief
    that only says what an agent owns gets four work orders about the same broken sensor. Each
    brief has to name at least two neighbours by the name the router uses for them."""
    brief = MANDATES[agent]
    assert brief.startswith("YOUR PATCH:")
    neighbours = {other for other in RESPONDERS if other != agent and f"`{other}`" in brief}
    assert len(neighbours) >= 2, f"{agent}'s brief hands work to {neighbours or 'nobody'}; it needs to name at least two neighbours it does not own"


@pytest.mark.parametrize("agent", RESPONDERS)
def test_no_brief_ranks_severity_or_promises_a_tool(agent: str) -> None:
    """Two ways a brief silently undoes the architecture. Telling an agent something is
    "critical" makes severity negotiable when `triage.py` already owns it, and naming a tool
    invites a model with none bound to describe calling one. `herd_health` is allowed to say
    it CANNOT read a sensor, which is why the check is on the tool names, not the word."""
    brief = MANDATES[agent]
    for forbidden in ("critical", "severity", "nominal"):
        assert forbidden not in brief.lower(), f"{agent}'s brief touches severity, which belongs to triage.py"
    for tool in DEPLOYED_TOOLS:
        assert tool not in brief, f"{agent}'s brief names the tool {tool}; a worker has no tools bound and nothing to navigate"


def test_nothing_a_model_reads_carries_an_em_dash() -> None:
    readable = [system_prompt(agent) for agent in RESPONDERS]
    readable += [WATER_SOP, FEED_SOP, TANK_PACKET.render()]
    for page in readable:
        assert "\u2014" not in page


def test_the_schema_forces_every_field_a_lazy_answer_would_leave_out() -> None:
    """`unknowns` is where a number the packet does not have goes instead of into the
    assessment, so it cannot be optional."""
    assert set(WORK_ORDER_SCHEMA["required"]) == set(WORK_ORDER_SCHEMA["properties"])
    assert {"unknowns", "rules_cited", "severity_echo"} <= set(WORK_ORDER_SCHEMA["required"])
    assert WORK_ORDER_SCHEMA["properties"]["severity_echo"]["enum"] == ["nominal", "warning", "critical"]


@respx.mock
async def test_a_tick_told_not_to_spend_stops_at_the_end_of_the_free_pass(first_sight: Settings, catalog: RanchMap, target: StoreTarget) -> None:
    """How every rail above the model layer exercises the whole pipeline for free, and the
    reason `conftest.no_model_calls` sits behind it. A forgotten flag has to fail, not bill."""
    serve(respx.mock, {"alkali-flat-water": 1.4, "east-allotment-fence": 6.0, "home-place-bin": 4000})
    state = await run_tick(tick=1, store=target, now=T0, spend=False)
    assert state.error is None
    assert state.opened, "the free pass still opened incidents"
    assert state.routed.get("water_feed"), "and still routed one to the agent that would have spent"
    assert state.work_orders == (), "and paid nothing to do it"
    assert state.shift_report is not None and state.shift_report.source == "code", "the free pass still ends with a page, assembled in code"
    assert state.shift_report.input_tokens == 0


# --- the bounded fan-out ---------------------------------------------------- #
def _packet(sensor_id: str, *, key: str = "", severity: str = "critical") -> EvidencePacket:
    """One packet per sensor, cheap. The SOP text is real because the citation rail reads it."""
    return EvidencePacket(
        incident=TANK_INCIDENT.model_copy(update={"key": key or f"{sensor_id}:water_low", "sensor_id": sensor_id, "severity": severity}),
        sop_name="water.md",
        sop_text=WATER_SOP,
    )


def _fan(**by_agent: int) -> dict[str, tuple[EvidencePacket, ...]]:
    return {agent: tuple(_packet(f"{agent}-sensor-{i}") for i in range(count)) for agent, count in by_agent.items()}


async def _judged(packet: EvidencePacket, **_kw: object) -> WorkOrder:
    """A finished work order without a model call. The recorded answer, one per packet."""
    return to_work_order(packet=packet, agent="water_feed", response=_response(_answer()))


async def test_the_concurrency_ceiling_is_global_and_not_one_per_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason `fan_out` builds the semaphore instead of letting `run_agent` do it.

    `AGENT_CONCURRENCY = 4` is a statement about how many Opus calls this repo will have in
    flight. Four agents each bounding themselves at four is sixteen, which is the version of
    this bug that passes every other test in this file and shows up as a 429 on a busy tick.
    """
    live = 0
    peak = 0

    async def _judge(packet: EvidencePacket, **_kw: object) -> WorkOrder:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)  # long enough that everything admitted piles up together
        live -= 1
        return await _judged(packet)

    monkeypatch.setattr("src.agent.workers.judge_packet", _judge)
    orders = await fan_out(_fan(water_feed=4, infrastructure=4, compliance=4, herd_health=4))

    assert peak <= AGENT_CONCURRENCY, f"{peak} concurrent model calls against a ceiling of {AGENT_CONCURRENCY}"
    assert sum(len(group) for group in orders.values()) == 16, "and all sixteen still got answered"


async def test_one_agent_raising_is_not_an_outage_for_the_other_three(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run_agent` already survives a raising packet. This is the other failure: the call to
    `run_agent` itself blowing up before any packet does, which without this takes three other
    agents' finished work down with it."""
    real = run_agent

    async def _run(agent: str, packets: object, **kw: object) -> tuple[WorkOrder, ...]:
        if agent == "infrastructure":
            raise RuntimeError("the infrastructure slice is misconfigured")
        return await real(agent, packets, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr("src.agent.workers.judge_packet", _judged)
    monkeypatch.setattr("src.agent.workers.run_agent", _run)
    with capture_logs() as logs:
        orders = await fan_out(_fan(water_feed=2, infrastructure=2, compliance=1))

    assert [o.status for o in orders["water_feed"]] == ["ok", "ok"], "the other agents finished"
    assert [o.status for o in orders["compliance"]] == ["ok"]
    assert [o.status for o in orders["infrastructure"]] == ["no_answer", "no_answer"], "and the failed one produced a work order per packet rather than nothing"
    assert all(o.violations == ("agent_raised",) for o in orders["infrastructure"])
    assert all("misconfigured" in o.assessment for o in orders["infrastructure"]), "with the reason kept, because somebody has to fix the slice"
    assert any(entry["event"] == "agent_raised" for entry in logs)


async def test_every_routed_incident_produces_a_work_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant the fan-out exists to hold. An incident nobody was handed is an incident
    nobody is paged about, which is indistinguishable from a ranch with nothing wrong."""
    monkeypatch.setattr("src.agent.workers.judge_packet", _judged)
    packets = _fan(water_feed=3, infrastructure=5, compliance=1)
    orders = await fan_out(packets)

    for name, group in packets.items():
        assert {o.incident_key for o in orders[name]} == {p.incident.key for p in group}


async def test_herd_health_is_handed_nothing_and_nobody_logs_a_fault_about_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docs/STATE.md` decision 5 working correctly rather than a gap. `herd_health` cannot read
    a sensor and nothing writes animal events until chaos does at M5, so it is handed nothing on
    every tick. A warning per tick per idle agent trains everyone to ignore the log."""
    monkeypatch.setattr("src.agent.workers.judge_packet", _judged)
    with capture_logs() as logs:
        orders = await fan_out(_fan(water_feed=1, herd_health=0, infrastructure=1, compliance=0))

    assert orders["herd_health"] == () and orders["compliance"] == ()
    assert set(orders) == set(RESPONDERS), "asked-and-found-nothing has to stay distinguishable from never-asked"
    assert not [entry for entry in logs if entry.get("agent") == "herd_health"], "an idle agent produces no line at all, not even an info one"
    assert next(entry for entry in logs if entry["event"] == "fan_out_start")["worlds"] == 2, "and an idle agent is not a world"


async def test_a_fan_out_with_nothing_in_it_calls_nobody() -> None:
    """A calm tick reaches this stage with four empty lists. `conftest.no_model_calls` is what
    makes a regression here bill instead of pass."""
    assert await fan_out({agent: () for agent in RESPONDERS}) == {agent: () for agent in RESPONDERS}


# --- the shift report ------------------------------------------------------- #
def _wo(agent: str, key: str, *, severity: str = "warning", headline: str = "", status: str = "ok", escalate: bool = False, **kw: object) -> WorkOrder:
    return WorkOrder(
        incident_key=key,
        agent=agent,
        severity=severity,  # type: ignore[arg-type]
        headline=headline or f"something is wrong at {key}",
        assessment="Two sentences of prose a supervisor is going to read and fuse with somebody else's.",
        actions=("Roll a truck",),
        status=status,  # type: ignore[arg-type]
        escalate=escalate,
        escalate_reason="the standing orders say the general manager hears about this" if escalate else "",
        **kw,  # type: ignore[arg-type]
    )


#: Two worlds, one location. The shape the free pass already produces most ticks on this ranch,
#: which is why cross-domain fusion is testable at M3 without `chaos`.
STORM = (
    _wo("water_feed", "alkali-flat-water:water_low", severity="critical", headline="Alkali Flat tank dry at 1.9 gal, 111 head"),
    _wo("infrastructure", "alkali-flat-battery:power_low", headline="Alkali Flat solar battery at 11%"),
)


def _report_payload(**over: object) -> dict[str, object]:
    return {
        "headline": "Alkali Flat is failing as one site: dead battery, blind tank, 111 head",
        "situation": "The battery at 11% and the tank reading 1.9 gal are one failure seen twice. The pump and the telemetry radio run off the same panel, so the tank has probably been low longer than the sweep can show.",
        "priorities": [
            "Haul water to Alkali Flat now, 111 head and no reserve behind the tank",
            "Swap the panel or the battery on the same trip, because a blind tank is why this was found late",
        ],
        "linked": ["alkali-flat-water:water_low", "alkali-flat-battery:power_low"],
        "escalations": [],
    } | over


def _supervisor(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object] | None, *, finish_reason: str = "tool_use") -> list[dict[str, object]]:
    """Stand in for the one Opus call this stage may make, and record that it happened."""
    calls: list[dict[str, object]] = []

    async def _call(**kw: object) -> ModelResponse:
        calls.append(kw)
        return _response(payload, finish_reason=finish_reason)

    monkeypatch.setattr("src.models.llm_client.call_tier2", _call)
    return calls


def _state(orders: tuple[WorkOrder, ...]) -> RanchState:
    return RanchState(run_id="test", tick=1, work_orders=orders, worlds=tuple(agent for agent in RESPONDERS if any(o.agent == agent for o in orders)))


async def test_one_world_reporting_is_assembled_in_code_and_costs_nothing() -> None:
    """The whole cost decision in this stage is one `if`. One world is a concatenation of length
    one, and paying Opus to reformat a single agent's work orders buys a header.
    `conftest.no_model_calls` is what makes this test fail loudly rather than bill."""
    report = await synthesize(_state(STORM[:1]))
    assert report.source == "code" and report.input_tokens == 0 and report.output_tokens == 0
    assert report.worlds == ("water_feed",)
    assert report.linked == (), "code claims nothing about causation, ever"
    assert "Alkali Flat tank dry at 1.9 gal" in report.render(), "and the work order's own headline is what the priority line says"


async def test_two_worlds_is_the_storm_front_and_the_one_shape_worth_paying_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """The condition `src/agent/CLAUDE.md` already names as an escalation trigger, reused as the
    spend gate. It is the first tick where an incident in one world can explain an incident in
    another, which is the only thing a model is being bought for here."""
    assert FUSION_THRESHOLD == 2
    calls = _supervisor(monkeypatch, _report_payload())
    report = await synthesize(_state(STORM))

    assert len(calls) == 1, "one call per tick, never one per work order"
    assert report.source == "model" and report.violations == ()
    assert report.linked == ("alkali-flat-water:water_low", "alkali-flat-battery:power_low")
    assert report.input_tokens == 5555, "and the receipt is kept, because this is the tick's second-largest bill"
    assert calls[0]["agent"] == "supervisor" and calls[0]["max_tokens"] == SHIFT_REPORT_MAX_TOKENS


async def test_the_supervisor_does_not_inherit_the_workers_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    """`INHERITED_RULES` describes judging one evidence packet and writing a work order, which is
    not this job. A brief whose first paragraph is somebody else's task is worse than no brief."""
    calls = _supervisor(monkeypatch, _report_payload())
    await synthesize(_state(STORM))

    assert calls[0]["system"] == SUPERVISOR_MANDATE
    assert "SEVERITY IS NOT YOURS" not in SUPERVISOR_MANDATE, "the workers' rule 1 tells you to echo a field this job does not have"
    assert "DO NOT RE-RANK SEVERITY" in SUPERVISOR_MANDATE, "the supervisor's version, which is about ordering rather than echoing"
    assert "—" not in SUPERVISOR_MANDATE
    assert "supervisor" not in MANDATES, "the supervisor is not a responder: no slice, no packet, no route"


async def test_a_report_linking_an_incident_nobody_handed_over_is_thrown_away(monkeypatch: pytest.MonkeyPatch) -> None:
    """`invented_rule` pointed at a different output. `linked` is the report's causal claim and
    code knows exactly which keys it handed over, so a fabricated one is cheap to catch and
    sends somebody looking for an incident that does not exist."""
    calls = _supervisor(monkeypatch, _report_payload(linked=["alkali-flat-water:water_low", "windmill-pasture-fence:fence_down"]))
    with capture_logs() as logs:
        report = await synthesize(_state(STORM))

    assert len(calls) == 1, "replaced, never retried: a retry loop turns the rail into a sampler"
    assert report.source == "code" and "invented_incident" in report.violations
    assert report.provider == "bedrock" and report.model, "the receipt survives the rejection, because the call was still billed"
    assert any(entry["event"] == "shift_report_invented_incident" for entry in logs)


async def test_a_report_that_tells_nobody_to_do_anything_is_an_all_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Code found every one of these before the supervisor was called, so a quiet page is a
    contradiction rather than a finding."""
    _supervisor(monkeypatch, _report_payload(priorities=["Continue to monitor Alkali Flat", "No further action this shift"]))
    report = await synthesize(_state(STORM))
    assert "all_clear" in report.violations and report.source == "code"


async def test_an_accurate_situation_sentence_is_not_an_all_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Why the rail reads the priorities and the headline and never the `situation` prose. "The
    only real problem tonight is the tank" is the correct summary of a thin tick, and a rail that
    punishes accurate writing gets switched off inside a week."""
    _supervisor(monkeypatch, _report_payload(situation="Nothing else on the ranch is wrong tonight. The tank at Alkali Flat is the only real problem, and the battery is why it was found late."))
    report = await synthesize(_state(STORM))
    assert report.violations == () and report.source == "model"


async def test_a_supervisor_that_never_answered_still_produces_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The person coming on shift needs a page whatever happened upstream. The fallback is the
    same function that writes every calm tick's report, which is why it is the best-tested path
    in the stage rather than one nobody has read."""
    _supervisor(monkeypatch, None, finish_reason="transport_error")
    report = await synthesize(_state(STORM))

    assert report.source == "code"
    assert report.violations == ("no_payload", "transport_error")
    assert report.finish_reason == "transport_error", "and the receipt says which failure it was"
    assert "Alkali Flat tank dry" in report.render(), "the work orders are still on the page"


async def test_a_truncated_supervisor_is_a_budget_bug_and_is_labelled_as_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """**What the first live M3 tick actually did**, and the reason `SHIFT_REPORT_MAX_TOKENS`
    doubled. 15 work orders is a 32.5k-char page, 1,024 output tokens ran out inside the
    priorities list, and the half-filled `input` dict then failed the `all_clear` rail. The log
    said the supervisor wrote an all-clear about a ranch with 10 critical incidents on it. It did
    not: it was cut off, and the two are worth telling apart at 2am."""
    _supervisor(monkeypatch, _report_payload(priorities=[], linked=[]), finish_reason="max_tokens")
    report = await synthesize(_state(STORM))

    assert report.violations == ("no_payload", "max_tokens"), "not ('all_clear',)"
    assert report.source == "code" and "Alkali Flat tank dry" in report.render()


def test_the_code_page_never_writes_an_all_clear_even_with_nothing_to_report() -> None:
    """No work orders and a healthy ranch are different claims, and only one of them is true.
    The empty tick is the case where a template most wants to say the wrong one."""
    report = assemble_shift_report((), worlds=())
    violations, _ = check_shift_report({"headline": report.headline, "situation": report.situation, "priorities": list(report.priorities), "linked": [], "escalations": []}, keys=frozenset())

    assert "all_clear" in violations, "the rail agrees there is no instruction on this page"
    assert "nothing here reports on the shift" in report.headline
    assert "not a statement about the ranch" in report.situation


def test_the_code_page_ranks_by_triage_severity_and_names_the_storm_front() -> None:
    orders = (_wo("compliance", "east-creek-flow:stream_flow_low"), *STORM)
    report = assemble_shift_report(orders, worlds=("water_feed", "infrastructure", "compliance"))

    assert report.priorities[0].startswith("critical:"), "triage's ranking, not a model's"
    assert "3 worlds" in report.headline and "1 critical" in report.headline
    assert "storm-front trigger" in report.escalations[0], "two or more worlds in one tick is an escalation in its own right"
    assert "nothing here claims a connection between them" in report.situation, "and code says out loud that it fused nothing"


def test_an_incident_nobody_could_judge_is_on_the_page_rather_than_dropped() -> None:
    """The one thing the supervisor can say that no responder could: somebody has to go look at
    this, because the system could not."""
    orders = (STORM[0], _wo("infrastructure", "windmill-pasture-fence:fence_down", status="no_answer", violations=("no_payload", "transport_error")))
    page = render_shift_page(orders)
    report = assemble_shift_report(orders, worlds=("water_feed", "infrastructure"))

    assert "NO USABLE WORK ORDER" in page and "windmill-pasture-fence:fence_down" in page
    assert "Alkali Flat tank dry" in page, "and the one that did get judged is rendered in full"
    assert "should be treated as unworked" in report.situation
    assert any("was not judged" in p for p in report.priorities)


def test_a_work_order_asking_to_escalate_reaches_the_page_once() -> None:
    report = assemble_shift_report((_wo("water_feed", "alkali-flat-water:water_low", escalate=True),), worlds=("water_feed",))
    assert len(report.escalations) == 1 and "general manager" in report.escalations[0]


def test_the_shift_report_schema_makes_the_fusion_claim_checkable() -> None:
    """`linked` has to be a list of keys rather than prose, because "the battery and the tank are
    one problem" in a sentence is a claim no rail can verify."""
    assert set(SHIFT_REPORT_SCHEMA["required"]) == set(SHIFT_REPORT_SCHEMA["properties"])
    assert SHIFT_REPORT_SCHEMA["properties"]["linked"]["items"] == {"type": "string"}
    assert SHIFT_REPORT_SCHEMA["properties"]["priorities"]["minItems"] == 1, "a report with no priorities is an all-clear in a different shape"
    assert SHIFT_REPORT_SCHEMA["additionalProperties"] is False


# --- the no-brief experiment ------------------------------------------------ #
# The pair in `docs/no-brief-transcript.md` and `docs/with-brief-transcript.md`, pinned. Both
# answers are recorded verbatim from the live run on 2026-09-10, and the point of the block is
# what it CANNOT assert: the rails do not separate them.

COMPLIANCE_SOP = (REPO_ROOT / "data" / "knowledge_base" / "compliance.md").read_text(encoding="utf-8")

RANGE_INCIDENT = Incident(
    key="alkali-flat-soil:range_dry",
    sensor_id="alkali-flat-soil",
    sensor_type="soil-moisture",
    location="Alkali Flat",
    category="range_dry",
    severity="critical",
    status="opened",
    summary="Alkali Flat: soil moisture reads 4.2% on alkali-flat-soil, at or below the critical line of 5%. This ground is bare-dry. Grazing it at planned stocking is how a conservation payment turns into a finding.",
    last_value="4.2%",
    unit="%",
    threshold=5.0,
    first_seen_at="2026-09-10T20:08:41.585000Z",
    last_seen_at="2026-09-10T20:08:41.585000Z",
    owner="compliance",
)

#: The page both live runs were handed, reproduced from `logs/brief_experiment.json`. Recorded
#: rather than invented for the same reason the answers are: a recorded answer graded against a
#: made-up input grades nothing.
RANGE_PACKET = EvidencePacket(
    incident=RANGE_INCIDENT,
    history=tuple(
        HistoryPoint(recorded_at=f"2026-09-10T{hour:02d}:{minute:02d}:45.396Z", value=value)
        for hour, minute, value in ((20, 8, 58.0), (19, 58, 18.2), (19, 48, 26.1), (19, 38, 31.4), (19, 28, 28.2), (19, 18, 25.1), (19, 8, 35.8), (18, 58, 17.1), (18, 48, 6.9), (18, 38, 56.2), (18, 28, 45.6), (18, 18, 23.1))
    ),
    siblings=(
        SiblingReading(sensor_id="alkali-flat-battery", sensor_type="battery-charge", status="online", value=11.1),
        SiblingReading(sensor_id="alkali-flat-fence", sensor_type="fence-voltage", status="online", value=5.8),
        SiblingReading(sensor_id="alkali-flat-gate", sensor_type="gate", status="online", value=False),
        SiblingReading(sensor_id="alkali-flat-humidity", sensor_type="humidity", status="online", value=37.0),
        SiblingReading(sensor_id="alkali-flat-temp", sensor_type="temperature", status="online", value=71.0),
        SiblingReading(sensor_id="alkali-flat-water", sensor_type="water-level", status="online", value=2.6),
        SiblingReading(sensor_id="alkali-flat-water-2", sensor_type="water-level", status="online", value=17.6),
    ),
    pasture=PastureContext(pasture_id="alkali-flat", name="Alkali Flat", acreage=2400, fence_type="barbed-wire", status="open", head_count=111),
    sop_name="compliance.md",
    sop_text=COMPLIANCE_SOP,
)

#: `compliance` answering `RANGE_PACKET` with `system_prompt("compliance", mandate=" ")`: the
#: inherited rules, the whole SOP, and no patch. 5,713 in / 884 out, `tool_use`. `unknowns` is
#: trimmed to the one that matters here; the other three are in the transcript.
NO_BRIEF_ANSWER: dict[str, object] = {
    "severity_echo": "critical",
    "headline": "Alkali Flat: soil moisture 4.2% with 111 head on it, log the reading and get GM on stocking review",
    "assessment": (
        "alkali-flat-soil reads 4.2% at Alkali Flat, at or below the 5% critical line, first seen 2026-09-10T20:08:41Z and still open. Alkali Flat is 2400 acres carrying 111 head, "
        "pasture status open, so this is a stocking decision and not just a note. The recent series on the same sensor swings from 6.9% to 58% within two hours, so the instrument's "
        "own record is erratic and the defensible record for this period is thin; the current triaged value is still the authoritative reading. Sibling sensors in the same sweep say "
        "nothing wet or dry about the ground: alkali-flat-temp 71 F, alkali-flat-humidity 37%, alkali-flat-battery 11.1% (low enough to question the datalogging), and two tanks "
        "reading alkali-flat-water 2.6 gal and alkali-flat-water-2 17.6 gal, which are stock water on an hours clock and belong to a separate water work order, not to this record."
    ),
    "actions": ["Log the compliance record now: alkali-flat-soil 4.2% at Alkali Flat, critical line 5%, first seen 2026-09-10T20:08:41Z, pasture alkali-flat 2400 acres, 111 head on it, status open (COMP-01, COMP-05)"],
    "rules_cited": ["COMP-01", "COMP-05", "COMP-04"],
    "escalate": True,
    "escalate_reason": "COMP-01 requires the general manager on every critical range reading, and 111 head on Alkali Flat makes this a stocking and lease question that is not the crew's to answer.",
    "unknowns": ["Whether alkali-flat-soil is reading true; battery alkali-flat-battery at 11.1% and a series ranging 6.9% to 58% in two hours are not a trustworthy record"],
}

#: The same model, the same page, `system_prompt("compliance")`. Five actions instead of one, and
#: two of them hand something to a named neighbour.
WITH_BRIEF_ANSWER: dict[str, object] = {
    "severity_echo": "critical",
    "headline": "Alkali Flat: soil moisture 4.2% with 111 head on it, stocking review to GM, gauge suspect",
    "assessment": (
        "Alkali Flat, 2026-09-10: alkali-flat-soil reads 4.2% soil moisture, at or below the 5% critical line, with 111 head standing on the 2400-acre pasture (status open, "
        "barbed-wire). This is a stocking decision, not a note. The same sensor's last 12 readings swing from 6.9% to 58% inside two hours (18.2% at 19:58Z, 58% at 20:08:45Z), so "
        "the instrument's stability is questionable and the defensible record for this period is thin; that repair belongs to infrastructure, but the hole in the record is ours. "
        "Supporting conditions in the same sweep: alkali-flat-temp 71 F, alkali-flat-humidity 37%, alkali-flat-battery at 11.1% charge, which may bear on why the soil series is "
        "jumping. Stock tank readings at this location (alkali-flat-water 2.6 gal, alkali-flat-water-2 17.6 gal) are water_feed's work order, not evidence about range condition, "
        "and are not addressed here."
    ),
    "actions": [
        "Log for the program file now: Alkali Flat, sensor alkali-flat-soil, 4.2% soil moisture, 2026-09-10T20:08:41Z, critical line 5%, pasture alkali-flat 2400 acres carrying 111 head, status open (COMP-01, COMP-05)",
        "Escalate to the general manager for a stocking review on Alkali Flat with the lease in front of them: this ground will not carry what the grazing plan assumed. Do not set an AUM number in the field (COMP-01)",
        "Send someone to Alkali Flat to eyeball forage and ground cover against the reading, and to note whether the site is representative of the pasture, recorded as a dated observation (COMP-01, COMP-05)",
        "Hand alkali-flat-soil to infrastructure for verification: the 12-reading series runs 6.9% to 58% within two hours and alkali-flat-battery reads 11.1%. Note in the file that this period has no stable reading behind it (COMP-05)",
        "Pass the alkali-flat-water 2.6 gal and alkali-flat-water-2 17.6 gal tank readings to water_feed in their own work order, separate from this one (COMP-04)",
    ],
    "rules_cited": ["COMP-01", "COMP-04", "COMP-05"],
    "escalate": True,
    "escalate_reason": "COMP-01 requires the general manager on every critical range reading, and this pasture is carrying 111 head, which makes it a stocking and lease question rather than a crew call.",
    "unknowns": ["Whether alkali-flat-soil is calibrated and powered correctly, given the 6.9% to 58% swing and 11.1% battery charge"],
}


def _compliance_order(payload: dict[str, object]) -> WorkOrder:
    return to_work_order(packet=RANGE_PACKET, agent="compliance", response=_response(payload))


def test_the_rails_cannot_tell_the_unbriefed_answer_from_the_briefed_one() -> None:
    """**The finding of the M3 experiment, and the reason `RECORDED_ANSWER` exists at all.**

    An unbriefed Opus did not flail. It echoed severity, named its sensor, quoted only numbers on
    the page, cited three real rule ids, and asked for a real action, so every rail in
    `workers.check` passes and the order ships. The rails ask whether an answer is defensible
    about its own incident, and that question has nothing to do with whether the agent knew which
    of four patches was its own.

    Which is exactly why nothing below can be turned into a blocking rail. Do not try: a rail
    that counts actions is a rail that gets satisfied by padding.
    """
    no_brief, with_brief = _compliance_order(NO_BRIEF_ANSWER), _compliance_order(WITH_BRIEF_ANSWER)

    for order in (no_brief, with_brief):
        assert order.violations == (), "both answers are clean; see docs/no-brief-transcript.md"
        assert order.status == "ok" and order.shippable
        assert ungrounded_numbers(order, RANGE_PACKET) == set(), "and both quote only the page"
        assert RANGE_INCIDENT.sensor_id in order.assessment and "4.2" in order.assessment

    assert len(no_brief.actions) == 1, "one action, and it is filing a note"
    assert len(with_brief.actions) == 5


def test_only_the_briefed_answer_hands_anything_to_a_neighbour() -> None:
    """What the brief buys, and the part no rail reads. Both answers noticed the two stock tanks
    and the suspect gauge; only the briefed one said whose they are.

    `data/knowledge_base/compliance.md` was on the page in both runs, COMP-04 and COMP-05
    included, and both of those name the owner in as many words. **The SOP alone did not produce
    the handoff.** Standing orders describe the ranch; the brief says which part of it is yours.
    """
    no_brief, with_brief = _compliance_order(NO_BRIEF_ANSWER), _compliance_order(WITH_BRIEF_ANSWER)
    neighbours = ("water_feed", "infrastructure", "herd_health")

    assert {"COMP-04", "COMP-05"} <= citable_rules(COMPLIANCE_SOP), "both runs could cite them, and both did"
    assert not [n for n in neighbours if n in " ".join(no_brief.actions)], "it deferred to 'a separate water work order' and named nobody"
    assert [n for n in neighbours if n in " ".join(with_brief.actions)] == ["water_feed", "infrastructure"]


def test_the_unbriefed_headline_promises_an_action_it_never_writes_down() -> None:
    """The sharpest single difference, and unreachable from a rail. "get GM on stocking review"
    is in the headline and in `escalate_reason`, and the actions list a human works from does not
    contain it. The order is internally inconsistent and completely defensible."""
    no_brief = _compliance_order(NO_BRIEF_ANSWER)
    assert "GM on stocking review" in no_brief.headline and no_brief.escalate
    assert not [a for a in no_brief.actions if "escalat" in a.lower() or "general manager" in a.lower()]
    assert any("Escalate to the general manager" in a for a in _compliance_order(WITH_BRIEF_ANSWER).actions)


# =========================================================================== #
# 7. the loop: held incidents, backoff, the ceiling, cadence, shutdown. M4.
# =========================================================================== #
# Nothing here reaches a model or the ranch. The tick rails run `run_tick` against respx and
# `sw_ops_test`; the loop rails hand `run_loop` a fake tick, a fake clock, and a fake sleep,
# because thirty ticks of bookkeeping should take a millisecond and prove the same thing.


def _held_order(key: str, *, agent: str = "water_feed", violations: tuple[str, ...] = ("agent_raised",), status: str = "no_answer") -> WorkOrder:
    return WorkOrder(incident_key=key, agent=agent, severity="critical", status=status, violations=violations, assessment="x")  # type: ignore[arg-type]


def _stub_spend(monkeypatch: pytest.MonkeyPatch, outcome: Callable[[str], WorkOrder]) -> list[str]:
    """`assemble` returns bare packets and `fan_out` answers each with `outcome(key)`. Returns
    the list of incident keys the fan-out was actually handed, for asserting on."""
    handed: list[str] = []

    async def _assemble(incidents: object, **_kw: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(incident=inc) for inc in incidents]  # type: ignore[attr-defined]

    async def _fan_out(by_agent: dict[str, tuple[SimpleNamespace, ...]], **_kw: object) -> dict[str, tuple[WorkOrder, ...]]:
        out: dict[str, tuple[WorkOrder, ...]] = {}
        for owner, packets in by_agent.items():
            handed.extend(p.incident.key for p in packets)
            out[owner] = tuple(outcome(p.incident.key) for p in packets)
        return out

    monkeypatch.setattr("src.agent.executor.assemble", _assemble)
    monkeypatch.setattr("src.agent.executor.fan_out", _fan_out)
    return handed


LOW = {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0}
CALM = {"alkali-flat-water": 18.0, "east-allotment-fence": 6.4, "home-place-bin": 900.0}


@respx.mock
async def test_a_crashed_agent_resolves_nothing_and_its_incidents_are_held(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """M1's rule one layer up. Reconcile runs before any agent and reads triage's findings, so
    an agent raising cannot close an incident; the rail asserts that first. What it CAN do is
    leave a `no_answer` order on an incident that is `ongoing` next tick and never re-routed.
    `held` is the fix: carried out of the tick, counted on the line, re-routed on the next."""
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: _held_order(key))
    with capture_logs() as logs:
        crashed = await run_tick(tick=1, store=target, now=T0, spend=True)

    assert crashed.error is None and len(crashed.opened) == 1
    assert crashed.resolved == (), "a crashed agent resolved nothing"
    assert crashed.held == ("alkali-flat-water:water_low",)
    line = next(entry for entry in logs if entry["event"] == "tick")
    assert line["held"] == 1 and line["ledger"] == {"opened": 1}
    assert any(entry["event"] == "incidents_held" and entry["count"] == 1 for entry in logs)

    respx.mock.reset()
    serve(respx.mock, LOW)
    handed = _stub_spend(monkeypatch, lambda key: to_work_order(packet=_packet("alkali-flat-water"), agent="water_feed", response=_response(_answer())))
    recovered = await run_tick(tick=2, store=target, now=T1, spend=True, held=crashed.held)

    assert recovered.opened == () and len(recovered.ongoing) == 1, "the ledger calls it ongoing, which is why route() alone would never see it again"
    assert handed == ["alkali-flat-water:water_low"], "and the held incident was re-routed anyway"
    assert recovered.held == (), "answered, so no longer held"


@respx.mock
async def test_a_held_incident_that_heals_falls_out_of_the_held_set(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: _held_order(key))
    crashed = await run_tick(tick=1, store=target, now=T0, spend=True)

    respx.mock.reset()
    serve(respx.mock, CALM)
    handed = _stub_spend(monkeypatch, lambda key: _held_order(key))
    healed = await run_tick(tick=2, store=target, now=T1, spend=True, held=crashed.held)

    assert len(healed.resolved) == 1 and healed.held == () and handed == [], "nothing to re-route: the tank came back"


@respx.mock
async def test_a_rail_rejection_is_never_held(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """A retry loop turns a rail into a sampler. `rejected` means the model answered and the
    answer was indefensible; asking again until it passes is the one thing not to do. And a
    `max_tokens` no-answer is a config bug that buys the same truncation twice."""
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: _held_order(key, status="rejected", violations=("all_clear",)))
    assert (await run_tick(tick=1, store=target, now=T0, spend=True)).held == ()

    respx.mock.reset()
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: _held_order(key, violations=("no_payload", "max_tokens")))
    assert (await run_tick(tick=2, store=target, now=T1, spend=True)).held == ()


@respx.mock
async def test_a_transport_failure_is_held_because_the_model_never_answered(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: _held_order(key, violations=("no_payload", "transport_error")))
    assert (await run_tick(tick=1, store=target, now=T0, spend=True)).held == ("alkali-flat-water:water_low",)


async def test_a_tick_with_the_free_pass_in_backoff_still_writes_its_line(monkeypatch: pytest.MonkeyPatch, target: StoreTarget) -> None:
    """The rule the whole design hangs on. A loop in backoff that writes no line is
    indistinguishable from a dead loop. So the tick writes one, names who is sick, touches
    nothing, and the held set passes through untouched because nothing was attempted."""

    async def _never() -> tuple[RanchMap, str]:
        raise AssertionError("the catalog must not be fetched while the sensor api is in backoff")

    monkeypatch.setattr("src.agent.executor.fetch_catalog", _never)
    with capture_logs() as logs:
        state = await run_tick(tick=3, store=target, spend=True, skip=("sensor",), held=("a:b",))

    lines = [entry for entry in logs if entry["event"] == "tick"]
    assert len(lines) == 1
    assert lines[0]["skipped_upstreams"] == ["sensor"] and lines[0]["error"] is None and lines[0]["failed_stage"] is None
    assert lines[0]["held"] == 1 and lines[0]["cost_usd"] == 0.0
    assert state.held == ("a:b",) and state.sensors_read == 0
    assert "skipped" in summarize(state) and "sensor" in summarize(state)


@respx.mock
async def test_a_sick_model_holds_new_incidents_instead_of_paying_for_packets_nobody_can_judge(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """Per-upstream, not global: the ranch is readable, so the free pass runs and the ledger
    moves. Only the spend is withheld, and what it would have spent on is held for the tick
    when the window closes."""
    serve(respx.mock, LOW)

    async def _no(*_a: object, **_k: object) -> None:
        raise AssertionError("no evidence call while the model is in backoff")

    monkeypatch.setattr("src.agent.executor.assemble", _no)
    state = await run_tick(tick=1, store=target, now=T0, spend=True, skip=("model",))

    assert len(state.opened) == 1, "the free pass ran"
    assert state.skipped_upstreams == ("model",) and state.held == ("alkali-flat-water:water_low",)
    assert state.work_orders == () and state.cost_usd == 0.0


@respx.mock
async def test_every_read_failing_is_the_sensor_api_being_down_and_fails_the_sweep(catalog: RanchMap, target: StoreTarget) -> None:
    """One dark sensor is data; every sensor dark is an outage. Without this the sweep "succeeds"
    with zero readings, the tick is green, and the loop reads 160 connection errors on every tick
    at cadence forever, which is the opposite of backing off. Found while planning the live kill."""
    serve(respx.mock, LOW, broken=set(LOW))
    with capture_logs() as logs:
        state = await run_tick(tick=1, store=target, now=T0, spend=False)
    assert state.failed_stage == "sweep" and state.error is not None and "all 3 reads failed" in state.error
    assert next(entry for entry in logs if entry["event"] == "tick")["failed_stage"] == "sweep"
    assert state.resolved == () and state.opened == ()

    respx.mock.reset()
    serve(respx.mock, LOW, broken={"alkali-flat-water"})
    partial = await run_tick(tick=2, store=target, now=T1, spend=False)
    assert partial.failed_stage is None and (partial.sensors_read, partial.sensors_failed) == (2, 1), "one dark sensor is still just one dark sensor"


@respx.mock
async def test_a_failed_tick_keeps_carrying_what_it_was_handed(catalog: RanchMap, target: StoreTarget, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("sensor api unreachable")

    monkeypatch.setattr("src.agent.executor.sweep", _boom)
    state = await run_tick(tick=1, store=target, now=T0, spend=False, held=("x:y",))
    assert state.failed_stage == "sweep" and state.held == ("x:y",)


@respx.mock
async def test_cost_usd_rides_on_the_tick_line_and_a_calm_tick_is_exactly_zero(catalog: RanchMap, target: StoreTarget) -> None:
    """The ceiling is denominated in dollars, so the dollars are on the line rather than
    derived later from the tokens by whoever is awake. The rate is an assumption, in one
    place, and the field is exact zero when nothing billed, not a rounding of nothing."""
    serve(respx.mock, CALM)
    with capture_logs() as logs:
        state = await run_tick(tick=1, store=target, now=T0, spend=False)
    line = next(entry for entry in logs if entry["event"] == "tick")
    assert line["cost_usd"] == 0.0 and state.cost_usd == 0.0
    assert cost_usd(1_000_000, 0) == ASSUMED_RATE_USD_PER_M[0] and cost_usd(0, 1_000_000) == ASSUMED_RATE_USD_PER_M[1]
    assert cost_usd(91_468, 15_545) == pytest.approx(2.54, abs=0.01), "tick B in docs/model-routing.md, so the constant and the ledger agree"


# --- the backoff registry --------------------------------------------------------------- #
def test_backoff_is_per_upstream_exponential_and_capped() -> None:
    """Backing the whole loop off because the Feed API is sick means one dead service stops
    the ranch watch. One key per upstream, and a failure on one says nothing about another."""
    b = Backoff(base=60.0, cap=900.0)
    assert [b.record_failure("sensor", now=0.0) for _ in range(5)] == [60.0, 120.0, 240.0, 480.0, 900.0]
    assert b.blocked(now=0.0) == {"sensor": 900.0}
    assert "mcp" not in b.blocked(now=0.0), "and mcp is untouched"
    assert b.blocked(now=901.0) == {}, "the window closes on its own"
    b.record_success("sensor")
    assert b.failures == {} and b.record_failure("sensor", now=0.0) == 60.0, "a success resets the count"


def test_backoff_reads_the_tick_outcome_by_stage() -> None:
    b = Backoff(base=60.0, cap=900.0)
    b.observe(RanchState(failed_stage="sweep", error="ConnectError"), now=0.0)
    assert set(b.blocked(now=0.0)) == {"sensor"}

    b.observe(RanchState(failed_stage="reconcile", error="OperationalError"), now=0.0)
    assert set(b.blocked(now=0.0)) == {"sw_ops"}, "the sweep ran clean on that tick, which is the sensor api recovering, and the ledger is now the sick one"

    b.observe(RanchState(failed_stage=None, catalog_source="mcp_resource"), now=0.0)
    assert b.blocked(now=0.0) == {}, "a clean tick clears everything it attempted"

    b.observe(RanchState(failed_stage="chaos", error="x"), now=0.0)
    assert b.blocked(now=0.0) == {}, "an unknown stage marks nothing rather than crashing the loop"


def test_backoff_treats_every_order_dying_in_transport_as_the_model_being_down() -> None:
    """`fan_out` never raises; the model's outage arrives as transport-error orders. All of them
    dying is the model down. Some answering is not, and must not back the model off."""
    b = Backoff(base=60.0, cap=900.0)
    dead = RanchState(work_orders=(_held_order("a", violations=("no_payload", "transport_error")), _held_order("b", violations=("no_payload", "transport_error"))))
    b.observe(dead, now=0.0)
    assert set(b.blocked(now=0.0)) == {"model"}

    mixed = RanchState(work_orders=(_held_order("a", violations=("no_payload", "transport_error")), _held_order("b", status="ok", violations=())))
    b.observe(mixed, now=0.0)
    assert b.blocked(now=0.0) == {}, "one real answer is a recovery"


# --- the loop ---------------------------------------------------------------------------- #
class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _fake_tick(states: list[RanchState], *, calls: list[dict[str, object]] | None = None, clock: _Clock | None = None, takes: float = 0.0) -> Callable[..., Awaitable[RanchState]]:
    """A tick that returns the next canned state, records what it was handed, and advances
    the fake clock by `takes` so cadence can be asserted on."""
    queue = list(states)

    async def _tick(**kw: object) -> RanchState:
        if calls is not None:
            calls.append(kw)
        if clock is not None:
            clock.t += takes
        return queue.pop(0) if queue else RanchState()

    return _tick


async def _no_sleep(_s: float) -> None:
    return None


def _sleep_recorder(record: list[float], clock: _Clock) -> Callable[[float], Awaitable[None]]:
    """A sleep that records what it was asked for and moves the fake clock by that much."""

    async def _sleep(s: float) -> None:
        record.append(s)
        clock.t += s

    return _sleep


TEST_TARGET = StoreTarget(name="test", url="postgresql+asyncpg://x", schema="sw_ops_test")


async def test_the_loop_halts_at_the_spend_ceiling_and_exits_4() -> None:
    """The first phase where the money runs with nobody watching. The ceiling HALTS: no
    further tick starts, the reason is logged with the run id, and the exit code is one a
    restart policy will not relaunch and an on-call person will not read as an outage.
    Overshoot is bounded at one tick, because a fan-out already in flight is already paid for."""
    with capture_logs() as logs:
        code = await run_loop(store=TEST_TARGET, interval_s=0, ceiling_usd=10.0, tick_fn=_fake_tick([RanchState(cost_usd=4.0)] * 5), sleep=_no_sleep)

    assert code == EXIT_SPEND_CEILING == 4
    halted = next(entry for entry in logs if entry["event"] == "loop_halted")
    assert halted["reason"] == "spend_ceiling" and halted["ticks"] == 3, "4, 8, 12: the third tick crossed 10 and no fourth started"
    assert halted["spent_usd"] == 12.0 and halted["ceiling_usd"] == 10.0
    assert "run_id" in halted


async def test_the_loop_refuses_a_ceiling_that_is_not_positive() -> None:
    """There is no value that means unlimited. Zero is a refusal, not infinity."""
    with pytest.raises(ValueError, match="SPEND_CEILING_USD"):
        await run_loop(store=TEST_TARGET, interval_s=0, ceiling_usd=0.0, tick_fn=_fake_tick([]), sleep=_no_sleep, max_ticks=1)
    assert await run_loop(store=TEST_TARGET, interval_s=0, ceiling_usd=0.0, spend=False, tick_fn=_fake_tick([]), sleep=_no_sleep, max_ticks=1) == EXIT_OK, "a free loop has nothing to cap"


async def test_a_free_loop_never_halts_on_cost() -> None:
    code = await run_loop(store=TEST_TARGET, interval_s=0, spend=False, ceiling_usd=1.0, tick_fn=_fake_tick([RanchState(cost_usd=5.0)] * 5), sleep=_no_sleep, max_ticks=5)
    assert code == EXIT_OK


async def test_cadence_is_start_to_start_and_an_overrun_is_named() -> None:
    """A 90-second storm tick must not push every later tick 90 seconds late. The next tick is
    scheduled from when this one STARTED, so the sleep is the interval minus the tick's own
    duration, and a tick longer than the interval starts the next immediately and says so."""
    clock = _Clock()
    slept: list[float] = []
    with capture_logs() as logs:
        await run_loop(store=TEST_TARGET, interval_s=10.0, spend=False, tick_fn=_fake_tick([RanchState()] * 3, clock=clock, takes=3.0), clock=clock, sleep=_sleep_recorder(slept, clock), max_ticks=3)
    assert slept == [7.0, 7.0], "10 minus the 3 the tick took, twice; no sleep after the last"

    clock = _Clock()
    slept = []
    with capture_logs() as logs:
        await run_loop(store=TEST_TARGET, interval_s=10.0, spend=False, tick_fn=_fake_tick([RanchState()] * 2, clock=clock, takes=14.0), clock=clock, sleep=_sleep_recorder(slept, clock), max_ticks=2)
    assert slept == [], "the tick overran, so the next started at once"
    overran = next(entry for entry in logs if entry["event"] == "tick_overran")
    assert overran["by_s"] == 4.0


async def test_the_loop_carries_held_and_backoff_from_one_tick_into_the_next() -> None:
    """The bookkeeping that makes M4 more than a while loop: what one tick could not answer
    and which upstream it found sick are the next tick's inputs."""
    calls: list[dict[str, object]] = []
    # Tick 2 reports the skip the way the real tick does, because `observe` reads it to know
    # the sensor api was not attempted rather than recovered.
    states = [RanchState(failed_stage="sweep", error="ConnectError", held=("a:b",)), RanchState(held=(), skipped_upstreams=("sensor",)), RanchState()]
    await run_loop(store=TEST_TARGET, interval_s=0, spend=False, tick_fn=_fake_tick(states, calls=calls), clock=_Clock(), sleep=_no_sleep, max_ticks=3)

    assert calls[0]["held"] == frozenset() and calls[0]["skip"] == ()
    assert calls[1]["held"] == frozenset({"a:b"}) and calls[1]["skip"] == ("sensor",), "tick 1 failed at sweep, so tick 2 skips the sensor api and carries the held key"
    assert calls[2]["held"] == frozenset() and calls[2]["skip"] == ("sensor",), "tick 2 attempted nothing on the sensor api, which says nothing about its recovery; the window is still open on a frozen clock"


async def test_the_first_interrupt_drains_the_in_flight_tick_and_exits_0() -> None:
    """Graceful shutdown on the platform this runs on. Python 3.11's Runner turns the first
    Ctrl+C into a cancel of the main task; `run_loop` catches it, lets the in-flight tick
    FINISH (its tokens are already paid for), and returns 0. A tick cancelled halfway writes
    no line, and a loop that exits without one looks like a loop that died."""
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[int] = []

    async def _slow_tick(**kw: object) -> RanchState:
        started.set()
        await release.wait()
        finished.append(int(kw["tick"]))  # type: ignore[call-overload]
        return RanchState(tick=int(kw["tick"]))  # type: ignore[call-overload]

    with capture_logs() as logs:
        runner = asyncio.create_task(run_loop(store=TEST_TARGET, interval_s=1000.0, spend=False, tick_fn=_slow_tick))
        await started.wait()
        runner.cancel()
        await asyncio.sleep(0)
        assert not runner.done(), "the loop is draining, not dead"
        release.set()
        code = await runner

    assert code == EXIT_OK and finished == [1], "the tick completed after the interrupt"
    assert any(entry["event"] == "loop_draining" for entry in logs)
    assert next(entry for entry in logs if entry["event"] == "loop_stopped")["ticks"] == 1


async def test_an_interrupt_during_the_cadence_sleep_stops_cleanly() -> None:
    ticked = asyncio.Event()

    async def _tick(**_kw: object) -> RanchState:
        ticked.set()
        return RanchState()

    runner = asyncio.create_task(run_loop(store=TEST_TARGET, interval_s=1000.0, spend=False, tick_fn=_tick))
    await ticked.wait()
    await asyncio.sleep(0.01)
    runner.cancel()
    assert await runner == EXIT_OK


async def test_a_stop_request_wakes_the_loop_out_of_its_sleep() -> None:
    """SIGTERM and SIGBREAK set the stop event from a signal handler. A loop asleep for the
    rest of a five-minute cadence has to notice within the tick, not at the next one."""
    stop = asyncio.Event()
    runner = asyncio.create_task(run_loop(store=TEST_TARGET, interval_s=1000.0, spend=False, tick_fn=_fake_tick([RanchState()]), stop=stop))
    await asyncio.sleep(0.01)
    stop.set()
    assert await asyncio.wait_for(runner, timeout=2.0) == EXIT_OK


async def test_a_tick_raising_outside_its_own_guard_writes_a_line_and_exits_1() -> None:
    """`run_tick` catches everything a stage can raise, so this is a bug in the scaffolding.
    It still gets a tick line, and then the loop stops rather than retrying a bug on a
    cadence, because that is the tight billing loop this module exists to prevent."""

    async def _bug(**_kw: object) -> RanchState:
        raise RuntimeError("log_tick blew up")

    with capture_logs() as logs:
        code = await run_loop(store=TEST_TARGET, interval_s=0, spend=False, tick_fn=_bug, sleep=_no_sleep)

    assert code == EXIT_UNRECOVERABLE == 1
    line = next(entry for entry in logs if entry["event"] == "tick")
    assert line["failed_stage"] == "tick" and "log_tick blew up" in line["error"]


# --- chaos, from inside the tick ---------------------------------------------------------- #
def _chaos_settings(**over: object) -> Settings:
    return get_settings().model_copy(update={"chaos_enabled": True, "chaos_allow_writes": False, "chaos_animal_cohort": "", **over})


def _healed(event_id: str, *, kind: str = KIND_SENSOR) -> ChaosEvent:
    return ChaosEvent(event_id=event_id, group_id="g", scenario="dead_radio", kind=kind, target_id="alkali-flat-water" if kind == KIND_SENSOR else "cow-0901", target_type="water-level", location="Alkali Flat", fault="offline", payload={}, seed=1, seq=0, status="expired", injected_at=T0, expires_at=T1)


@respx.mock
async def test_a_chaos_fault_that_healed_without_ever_being_read_is_reported_missed(monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """Cadence and TTL were chosen together (TTL is in ticks, multiplied by the cadence at
    injection), so at nominal cadence every fault straddles two sweeps. The miss that remains
    is a sweep in backoff while a short fault ages out. A silent miss is the failure, not the
    miss itself: the chaos log would be full of events the ranch never saw, and the ranch
    would have read calm the whole time."""
    monkeypatch.setattr("src.agent.executor.get_settings", _chaos_settings)

    async def _expire(_session: object, **_kw: object) -> tuple[ChaosEvent, ...]:
        return (_healed("chaos-1-0"), _healed("chaos-1-1", kind=KIND_ANIMAL))

    async def _inject(*_a: object, **_k: object) -> tuple[ChaosEvent, ...]:
        return ()

    monkeypatch.setattr("src.agent.executor.chaos.expire", _expire)
    monkeypatch.setattr("src.agent.executor.chaos.inject_for_tick", _inject)
    monkeypatch.setattr("src.agent.executor.chaos.plan", lambda **_kw: ())
    serve(respx.mock, CALM)

    with capture_logs() as logs:
        unseen = await run_tick(tick=2, store=target, now=T1, spend=False, chaos_injected=("chaos-1-0", "chaos-1-1"), chaos_seen=())
    assert unseen.chaos_missed == ("chaos-1-0",), "the sensor fault was never read; the animal event has no observer yet and is not counted against the sweep"
    assert unseen.chaos_healed == 2 and unseen.error is None
    missed = next(entry for entry in logs if entry["event"] == "chaos_event_missed")
    assert missed["event_id"] == "chaos-1-0"
    assert next(entry for entry in logs if entry["event"] == "tick")["chaos_missed"] == ["chaos-1-0"]

    respx.mock.reset()
    serve(respx.mock, CALM)
    seen = await run_tick(tick=3, store=target, now=T1, spend=False, chaos_injected=("chaos-1-0",), chaos_seen=("chaos-1-0",))
    assert seen.chaos_missed == (), "read once during its life is observed, not missed"

    respx.mock.reset()
    serve(respx.mock, CALM)
    foreign = await run_tick(tick=4, store=target, now=T1, spend=False, chaos_injected=(), chaos_seen=())
    assert foreign.chaos_missed == (), "an event some other run armed is not this run's miss to report"


@respx.mock
async def test_the_tick_arms_chaos_when_enabled_and_the_line_says_how_much(monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """The three lines M5 left owed: `inject_for_tick` wired into the tick, `chaos_fired` on the
    line, and the failure of either never failing the tick it decorates."""
    monkeypatch.setattr("src.agent.executor.get_settings", _chaos_settings)
    armed: list[int] = []

    async def _expire(_session: object, **_kw: object) -> tuple[ChaosEvent, ...]:
        return ()

    async def _inject(_session: object, *, tick: int, **_kw: object) -> tuple[ChaosEvent, ...]:
        armed.append(tick)
        return (_healed("chaos-1-9"),)

    monkeypatch.setattr("src.agent.executor.chaos.expire", _expire)
    monkeypatch.setattr("src.agent.executor.chaos.inject_for_tick", _inject)
    monkeypatch.setattr("src.agent.executor.chaos.plan", lambda **_kw: ())
    serve(respx.mock, CALM)
    with capture_logs() as logs:
        state = await run_tick(tick=5, store=target, now=T0, spend=False)
    assert armed == [5] and state.chaos_fired == 1 and state.chaos_injected == ("chaos-1-9",)
    assert next(entry for entry in logs if entry["event"] == "tick")["chaos_fired"] == 1

    async def _broken(*_a: object, **_k: object) -> tuple[ChaosEvent, ...]:
        raise RuntimeError("chaos store unreachable")

    monkeypatch.setattr("src.agent.executor.chaos.expire", _broken)
    respx.mock.reset()
    serve(respx.mock, CALM)
    with capture_logs() as logs:
        state = await run_tick(tick=6, store=target, now=T1, spend=False)
    assert state.error is None and state.sensors_read == 3, "chaos never fails the tick it decorates"
    assert any(entry["event"] == "chaos_step_failed" for entry in logs)


@respx.mock
async def test_chaos_disarmed_means_the_tick_never_touches_the_chaos_store(monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    async def _never(*_a: object, **_k: object) -> tuple[ChaosEvent, ...]:
        raise AssertionError("chaos is off; nothing in the tick may call it")

    monkeypatch.setattr("src.agent.executor.chaos.expire", _never)
    monkeypatch.setattr("src.agent.executor.chaos.inject_for_tick", _never)
    serve(respx.mock, CALM)
    state = await run_tick(tick=1, store=target, now=T0, spend=False)
    assert state.error is None and state.chaos_fired == 0


# =========================================================================== #
# 8. the gate: a proposed write pauses for a human, and the pause outlives the process. M6.
# =========================================================================== #
# Three layers, in the order they were built. The audit rail first, because it is what catches
# a decision path that skipped its log line. Then the planted-bad-proposal suite, one fixture
# per failure mode, asserting WHICH check fires: a suite that passes because any rail objected
# lets two rails swap jobs without anyone noticing. Then the pause itself, on `sw_ops_test`
# through the real LangGraph checkpointer, across two connections standing in for a restart.
#
# Nothing here reaches a model or the ranch. `perform` is injected, so an approve exercises the
# whole graph and the `Approval` handoff without a write on the wire.


@pytest.fixture
def gate_landed() -> None:
    """These rails were written before the flip with `GATE_LANDED` patched True. The flip landed
    on 2026-09-11, so the fixture now only asserts the real state; `GATE_LANDED` is read at call
    time everywhere it matters, which is what made the pre-flip patch (and this) the whole switch."""
    assert GATE_LANDED


@pytest.fixture
async def gate_target(target: StoreTarget) -> StoreTarget:
    """`target`, plus empty checkpointer tables, so one test's pause is not the next test's pending list."""
    engine = build_engine(target.url, schema=SCHEMA_TEST)
    try:
        async with engine.begin() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await conn.execute(text(f"truncate table {table}"))
    finally:
        await engine.dispose()
    return target


async def _perform_ok(tool: str, args: dict[str, object], approval: Approval) -> tuple[str, str]:
    return "written", json.dumps({"tool": tool, "args": args, "by": approval.decided_by})


def _proposal(key: str = "alkali-flat-water:water_low", *, tool: str = "restock_feed", args: dict[str, object] | None = None, tick: int = 1) -> WriteProposal:
    return WriteProposal(incident_key=key, agent="water_feed", tool=tool, args=args or {"sku": "alkali-flat-water-2", "quantity": 16.7}, tick=tick, run_id="run-1")


def _audit(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [entry for entry in logs if entry.get("phase") in ("proposed", "decided")]


# --- the rail ------------------------------------------------------------------------------ #
def test_the_audit_rail_flags_anything_that_is_not_exactly_a_pair() -> None:
    """Every `audit_id` appears twice, or once while its pause is still open. The helper
    returns the exceptions; the rail is that its keys minus the pending ids is empty."""
    lines = [{"audit_id": "a", "phase": "proposed"}, {"audit_id": "a", "phase": "decided"}, {"audit_id": "b", "phase": "proposed"}, {"audit_id": "c", "phase": "proposed"}, {"audit_id": "c", "phase": "decided"}, {"audit_id": "c", "phase": "decided"}, {"event": "tick"}]
    assert unpaired_audit_ids(lines) == {"b": 1, "c": 3}
    assert set(unpaired_audit_ids(lines)) - {"b"} == {"c"}, "b is a pause nobody answered yet; c is a decision logged twice"


# --- the planted-bad-proposal suite ---------------------------------------------------------- #
PAGE = TANK_PACKET.render()

BAD_PROPOSALS: list[tuple[str, object, str]] = [
    ("not an object at all", "restock_feed", "write_shape_invalid"),
    ("a tool with no args", {"tool": "restock_feed"}, "write_shape_invalid"),
    ("args that are not an object", {"tool": "restock_feed", "args": "sku=alkali-flat-water-2"}, "write_shape_invalid"),
    ("an argument the tool does not take", {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 16.7, "urgency": "high"}}, "write_shape_invalid"),
    ("a required argument missing", {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2"}}, "write_shape_invalid"),
    ("a quantity written as a string", {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": "16.7"}}, "write_shape_invalid"),
    ("a read tool", {"tool": "read_sensor", "args": {"sensorId": "alkali-flat-water"}}, "write_tool_not_allowed"),
    ("another agent's write", {"tool": "create_observation", "args": {"animalId": "cow-0901", "type": "injury", "severity": "high", "note": "x", "observedAt": "2026-09-10T14:00:00.000Z"}}, "write_tool_not_allowed"),
    ("a write in no slice at all", {"tool": "assign_to_pasture", "args": {"pastureId": "alkali-flat", "animalId": "cow-0901"}}, "write_tool_not_allowed"),
    ("an id the page never printed", {"tool": "restock_feed", "args": {"sku": "grass-hay", "quantity": 16.7}}, "ungrounded_write_arg"),
    ("a quantity the page never printed", {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 500}}, "ungrounded_write_arg"),
    ("a quantity that is a substring of one on the page", {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 6.7}}, "ungrounded_write_arg"),
]


@pytest.mark.parametrize(("why", "raw", "expected"), BAD_PROPOSALS, ids=[case[0] for case in BAD_PROPOSALS])
def test_a_bad_proposal_fires_exactly_the_check_that_owns_it(gate_landed: None, why: str, raw: object, expected: str) -> None:
    """One fixture per failure mode, and the assertion is WHICH code, alone. `16.7` grounds
    `16.7` and `6.7` does not: the grader compares number tokens, not substrings, for the same
    reason `ungrounded_numbers` above does."""
    violations, proposal = check_write_proposal(raw, agent="water_feed", page=PAGE)
    assert violations == [expected], why
    assert proposal is None, "a proposal that failed a check never reaches the gate"


@pytest.mark.parametrize(
    ("why", "args", "expected"),
    [
        ("an observation type outside the enum", {"animalId": "cow-0901", "type": "limping", "severity": "high", "note": "x", "observedAt": "2026-09-10T14:00:00.000Z"}, ["write_shape_invalid"]),
        ("a severity outside the enum", {"animalId": "cow-0901", "type": "injury", "severity": "critical", "note": "x", "observedAt": "2026-09-10T14:00:00.000Z"}, ["write_shape_invalid"]),
        ("a timestamp that is not ISO 8601", {"animalId": "cow-0901", "type": "injury", "severity": "high", "note": "x", "observedAt": "yesterday"}, ["write_shape_invalid"]),
        ("an animal the page never named", {"animalId": "cow-0777", "type": "injury", "severity": "high", "note": "x", "observedAt": "2026-09-10T14:00:00.000Z"}, ["ungrounded_write_arg"]),
        ("a clean observation", {"animalId": "cow-0901", "type": "injury", "severity": "high", "note": "down in the draw, not rising", "observedAt": "2026-09-10T14:00:00.000Z"}, []),
    ],
)
def test_the_care_write_is_checked_for_enum_timestamp_and_animal_id(gate_landed: None, why: str, args: dict[str, object], expected: list[str]) -> None:
    """`herd_health`'s write, on a page that names one animal. The free-text `note` is never
    graded: it cannot be on the page verbatim and grading it would reject every honest proposal."""
    page = "## The incident\n- animal: cow-0901, a mother cow, last observed 2026-09-10T14:00:00.000Z\n"
    violations, proposal = check_write_proposal({"tool": "create_observation", "args": args}, agent="herd_health", page=page)
    assert violations == expected, why
    assert (proposal is not None) == (expected == [])


def test_no_proposal_is_the_usual_answer_and_not_a_violation(gate_landed: None) -> None:
    for nothing in (None, {}, {"tool": "", "args": {}}, ""):
        assert check_write_proposal(nothing, agent="water_feed", page=PAGE) == ([], None)


def test_a_clean_proposal_survives_with_its_arguments_cleaned(gate_landed: None) -> None:
    violations, proposal = check_write_proposal({"tool": "restock_feed", "args": {"sku": " alkali-flat-water-2 ", "quantity": 16.7, "reason": "tank 2 is the working tank"}}, agent="water_feed", page=PAGE)
    assert violations == []
    assert proposal == {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 16.7, "reason": "tank 2 is the working tank"}}


def test_the_checks_fire_one_at_a_time_in_the_plans_order(gate_landed: None) -> None:
    """Shape, then key (the tool), then grounding. A proposal wrong in two ways names the first
    wrong thing only, so a rail's owner is never ambiguous."""
    both = {"tool": "create_observation", "args": {"animalId": "nobody", "type": "injury", "severity": "high", "note": "x", "observedAt": "2026-09-10T14:00:00.000Z"}}
    assert check_write_proposal(both, agent="water_feed", page=PAGE)[0] == ["write_tool_not_allowed"]
    shape_and_grounding = {"tool": "restock_feed", "args": {"sku": "grass-hay", "quantity": 500, "urgency": "now"}}
    assert check_write_proposal(shape_and_grounding, agent="water_feed", page=PAGE)[0] == ["write_shape_invalid"]


def test_a_failed_proposal_is_stripped_and_the_prose_still_ships(gate_landed: None) -> None:
    """The work order is a defensible answer about the incident; the one part that would have
    changed the ranch is what failed. Rejected with its violation, never retried, never blocking."""
    order = _order(_answer(proposed_write={"tool": "restock_feed", "args": {"sku": "grass-hay", "quantity": 16.7}}))
    assert order.status == "ok" and order.shippable
    assert order.proposed_write is None and order.audit_id == ""
    assert "ungrounded_write_arg" in order.violations
    assert not (set(order.violations) & BLOCKING_VIOLATIONS)


def test_a_clean_proposal_rides_the_work_order_to_the_gate(gate_landed: None) -> None:
    order = _order(_answer(proposed_write={"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 16.7}}))
    assert order.violations == () and order.proposed_write == {"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 16.7}}
    assert "PROPOSED WRITE: restock_feed(quantity=16.7, sku='alkali-flat-water-2')" in order.render()
    assert "PROPOSED WRITE" in render_shift_page([order]), "the person coming on shift sees what is waiting for a yes"


def test_before_the_flip_no_proposal_is_allowed_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam, from the proposal side. With `GATE_LANDED` False there is nowhere to pause, so
    even a perfect proposal is `write_tool_not_allowed` and the brief never mentions the field."""
    assert GATE_LANDED, "M6 flipped it; this rail patches it back to describe the seam"
    monkeypatch.setattr("src.tools.allowlists.GATE_LANDED", False)
    assert check_write_proposal({"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 16.7}}, agent="water_feed", page=PAGE)[0] == ["write_tool_not_allowed"]
    assert all("proposed_write" not in system_prompt(responder) for responder in RESPONDERS)


def test_the_brief_lists_only_this_agents_writes_rendered_from_the_allowlist(gate_landed: None) -> None:
    """Neutral, and generated: the names the model is told are the names `check` validates."""
    water = system_prompt("water_feed")
    assert "restock_feed(sku: an id printed on this page, quantity: a number printed on this page, reason: free text, optional)" in water
    assert "consume_feed(" in water and "create_observation" not in water
    herd = system_prompt("herd_health")
    assert "create_observation(" in herd and "update_care_task(" in herd and "restock_feed" not in herd
    assert "one of behavior/appetite/mobility/appearance/injury/general" in herd
    for reader in ("infrastructure", "compliance"):
        assert "proposed_write" not in system_prompt(reader), f"{reader} has no write in its slice and its brief must not invite one"
    assert "Almost always leave `tool` as an empty string" in water, "the brief says none is the usual answer; it does not sell the field"


def test_the_schema_requires_the_proposal_field_so_silence_is_explicit() -> None:
    assert "proposed_write" in WORK_ORDER_SCHEMA["required"]
    assert WORK_ORDER_SCHEMA["properties"]["proposed_write"]["required"] == ["tool", "args"]


def test_the_write_tool_argument_table_covers_all_eight_writes() -> None:
    assert set(WRITE_TOOL_ARGS) == set(WRITE_TOOLS)


# --- the key check, in the tick ------------------------------------------------------------- #
async def test_the_key_check_drops_a_proposal_for_an_incident_the_tick_never_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key is code's, so a foreign one is a bug on this side of the model. Dropped before
    the gate is opened, with `write_key_unknown` on the order, and the checkpointer is never touched."""

    def _never(*_a: object, **_k: object) -> object:
        raise AssertionError("the gate was opened for a proposal that failed the key check")

    monkeypatch.setattr("src.agent.executor.checkpointer", _never)
    foreign = WorkOrder(incident_key="somewhere-else:water_low", agent="water_feed", severity="critical", headline="h", assessment="a", actions=("go",), proposed_write={"tool": "restock_feed", "args": {"sku": "x", "quantity": 1}})
    with capture_logs() as logs:
        outcome = await _gate_step(TEST_TARGET, (foreign,), routed_keys=frozenset({"alkali-flat-water:water_low"}), tick=1, run_id="r")
    assert outcome.orders[0].proposed_write is None and "write_key_unknown" in outcome.orders[0].violations
    assert outcome.proposed == () and outcome.pending is None
    assert any(entry["event"] == "write_key_unknown" for entry in logs)


# --- the pause, on Postgres ------------------------------------------------------------------- #
async def test_a_proposal_pauses_and_a_new_connection_still_finds_it(gate_target: StoreTarget) -> None:
    """The gate outlives the process. Connection one proposes and is closed; connection two
    stands in for the restarted loop and lists the same pause with the same audit id."""
    with capture_logs() as logs:
        async with checkpointer(gate_target) as saver:
            outcome = await propose(build_gate(saver, perform=_perform_ok), _proposal())
    assert outcome.paused and outcome.audit_id and not outcome.dropped
    assert [entry["phase"] for entry in _audit(logs)] == ["proposed"]
    assert _audit(logs)[0]["audit_id"] == outcome.audit_id and _audit(logs)[0]["tool"] == "restock_feed" and _audit(logs)[0]["incident_key"] == "alkali-flat-water:water_low"

    async with checkpointer(gate_target) as saver:
        waiting = await pending(build_gate(saver, perform=_perform_ok))
    assert [(p.audit_id, p.tool, p.args, p.agent, p.tick) for p in waiting] == [(outcome.audit_id, "restock_feed", {"sku": "alkali-flat-water-2", "quantity": 16.7}, "water_feed", 1)]
    assert unpaired_audit_ids(logs) == {outcome.audit_id: 1}, "one line while paused: visible as a dangling record, not an absence"


async def test_the_same_write_for_the_same_incident_is_asked_once(gate_target: StoreTarget) -> None:
    async with checkpointer(gate_target) as saver:
        gate = build_gate(saver, perform=_perform_ok)
        first = await propose(gate, _proposal(tick=1))
        with capture_logs() as logs:
            second = await propose(gate, _proposal(tick=2, args={"sku": "alkali-flat-water-2", "quantity": 2.0}))
            other_tool = await propose(gate, _proposal(tick=2, tool="consume_feed"))
        waiting = await pending(gate)
    assert second.duplicate_of == first.audit_id and not second.paused, "same incident, same tool: a duplicate, even with different args"
    assert other_tool.paused, "a different write for the same incident is a different question"
    assert _audit(logs) and all(entry["audit_id"] == other_tool.audit_id for entry in _audit(logs)), "the duplicate wrote no audit line: it is not a new side effect"
    assert len(waiting) == 2 and any(entry["event"] == "write_proposal_duplicate" for entry in logs)


async def test_reject_then_approve_land_their_decided_lines_and_a_second_answer_is_refused(gate_target: StoreTarget) -> None:
    """The verification the plan names, minus the process kill (that is the live check): reject
    one, approve the other, prove both decided lines land with the fields `docs/logging.md`
    promises, and prove the rail is empty once nothing is pending."""
    performed: list[tuple[str, dict[str, object], Approval]] = []

    async def _perform(tool: str, args: dict[str, object], approval: Approval) -> tuple[str, str]:
        performed.append((tool, args, approval))
        return "written", '{"ok": true}'

    with capture_logs() as logs:
        async with checkpointer(gate_target) as saver:
            gate = build_gate(saver, perform=_perform)
            a = await propose(gate, _proposal("alkali-flat-water:water_low"))
            b = await propose(gate, _proposal("windmill-pasture-water:water_low"))
        assert set(unpaired_audit_ids(logs)) == {a.audit_id, b.audit_id}, "both pending, both dangling on purpose"

        async with checkpointer(gate_target) as saver:  # a different process, hours later
            gate = build_gate(saver, perform=_perform)
            rejected = await decide(gate, audit_id=a.audit_id, decision="reject", decided_by="scooter", reason="tank 2 is fine, no restock")
            approved = await decide(gate, audit_id=b.audit_id, decision="approve", decided_by="scooter")
            assert await pending(gate) == ()
            with pytest.raises(GateError, match="already decided"):
                await decide(gate, audit_id=a.audit_id, decision="approve", decided_by="scooter")
            with pytest.raises(GateError, match="no proposal"):
                await decide(gate, audit_id="nope", decision="approve", decided_by="scooter")
            with pytest.raises(GateError, match="needs a reason"):
                await decide(gate, audit_id=b.audit_id, decision="reject", decided_by="scooter")

    assert rejected["result"] == "not_executed" and rejected["decision"] == "reject"
    assert approved["result"] == "written" and approved["decided_by"] == "scooter"
    assert [(t, a_) for t, a_, _ in performed] == [("restock_feed", {"sku": "alkali-flat-water-2", "quantity": 16.7})], "exactly one write performed, the approved one, once"
    assert performed[0][2] == Approval(audit_id=b.audit_id, decided_by="scooter")

    decided = {entry["audit_id"]: entry for entry in _audit(logs) if entry["phase"] == "decided"}
    assert decided[a.audit_id]["decision"] == "reject" and decided[a.audit_id]["result"] == "not_executed" and decided[a.audit_id]["reason"] == "tank 2 is fine, no restock"
    assert decided[b.audit_id]["decision"] == "approve" and decided[b.audit_id]["result"] == "written" and decided[b.audit_id]["upstream"] == '{"ok": true}'
    for entry in decided.values():
        assert entry["decided_by"] == "scooter" and isinstance(entry["latency_to_decision_ms"], int) and entry["latency_to_decision_ms"] >= 0
    assert unpaired_audit_ids(logs) == {}, "every audit_id appears exactly twice once nothing is pending"


async def test_an_approved_write_that_dies_on_the_wire_still_gets_its_receipt(gate_target: StoreTarget) -> None:
    calls = 0

    async def _boom(tool: str, args: dict[str, object], approval: Approval) -> tuple[str, str]:
        nonlocal calls
        calls += 1
        raise RuntimeError("Feed API unreachable")

    with capture_logs() as logs:
        async with checkpointer(gate_target) as saver:
            gate = build_gate(saver, perform=_boom)
            paused = await propose(gate, _proposal())
            final = await decide(gate, audit_id=paused.audit_id, decision="approve", decided_by="scooter")
    assert final["result"] == "transport_RuntimeError" and calls == 1, "the receipt is written whatever the wire did, and nothing retries"
    assert unpaired_audit_ids(logs) == {}


async def test_a_proposal_the_checkpointer_cannot_persist_is_dropped_not_dangled() -> None:
    """The one shape the rail must never see is a `proposed` that nobody can ever answer. If the
    pause cannot be written, the pair is completed with `dropped` and the caller holds the incident."""

    class _DeadCheckpointer:
        async def alist(self, *_a: object, **_k: object) -> AsyncIterator[object]:
            return
            yield

    class _DeadGate:
        checkpointer = _DeadCheckpointer()

        async def ainvoke(self, *_a: object, **_k: object) -> object:
            raise ConnectionError("checkpointer down")

    with capture_logs() as logs:
        outcome = await propose(_DeadGate(), _proposal())  # type: ignore[arg-type]
    assert outcome.dropped and outcome.audit_id and not outcome.paused
    decided = [entry for entry in _audit(logs) if entry["phase"] == "decided"]
    assert decided[0]["decision"] == "dropped" and decided[0]["decided_by"] == "gate" and decided[0]["result"] == "checkpointer_unavailable"
    assert unpaired_audit_ids(logs) == {}


def _proposing_order(key: str) -> WorkOrder:
    return WorkOrder(incident_key=key, agent="water_feed", severity="critical", headline="Alkali Flat tank dry", assessment="alkali-flat-water reads 1.2 gal", actions=("haul water",), rules_cited=("WATER-01",), proposed_write={"tool": "restock_feed", "args": {"sku": "alkali-flat-water-2", "quantity": 16.7}})


@respx.mock
async def test_two_ticks_one_pending_write(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, gate_target: StoreTarget) -> None:
    """A pending write does not block the tick, and the next tick re-proposing the same write
    for the same incident is a duplicate to suppress, not a second question. Two ticks, one pause."""
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, _proposing_order)
    with capture_logs() as logs:
        first = await run_tick(tick=1, store=gate_target, now=T0, spend=True)
    assert first.error is None and len(first.writes_proposed) == 1 and first.writes_pending == 1 and first.writes_duplicate == 0
    order = first.work_orders[0]
    assert order.audit_id == first.writes_proposed[0] and order.shippable and order.proposed_write is not None
    assert first.held == (), "a pending write holds nothing: the incident is ongoing and the proposal waits"
    line = next(entry for entry in logs if entry["event"] == "tick")
    assert (line["writes_proposed"], line["writes_duplicate"], line["writes_pending"], line["writes_failed"]) == (1, 0, 1, 0)
    assert first.shift_report is not None, "the tick did not wait for an answer"

    respx.mock.reset()
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, _proposing_order)
    # Held so the incident is re-judged, which is the only way a second proposal can arise.
    second = await run_tick(tick=2, store=gate_target, now=T1, spend=True, held=("alkali-flat-water:water_low",))
    assert second.writes_proposed == () and second.writes_duplicate == 1 and second.writes_pending == 1
    assert second.work_orders[0].audit_id == first.writes_proposed[0], "the order points at the pause already waiting"

    async with checkpointer(gate_target) as saver:
        waiting = await pending(build_gate(saver, perform=_perform_ok))
    assert [p.audit_id for p in waiting] == [first.writes_proposed[0]]


@respx.mock
async def test_a_tick_with_nothing_to_propose_never_opens_the_gate(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    def _never(*_a: object, **_k: object) -> object:
        raise AssertionError("no proposal, no checkpointer connection")

    monkeypatch.setattr("src.agent.executor.checkpointer", _never)
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: to_work_order(packet=_packet("alkali-flat-water"), agent="water_feed", response=_response(_answer())))
    with capture_logs() as logs:
        state = await run_tick(tick=1, store=target, now=T0, spend=True)
    assert state.error is None and state.writes_pending is None
    assert next(entry for entry in logs if entry["event"] == "tick")["writes_pending"] is None, "a count nobody measured is null, not zero"


@respx.mock
async def test_the_gate_being_unreachable_holds_the_incident_and_the_tick_still_reports(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """The gate never fails the tick. The proposal is counted as failed, its incident is held
    with the gate's own reason (durably, so a restart re-proposes it too), and the shift page ships."""

    @asynccontextmanager
    async def _down(_target: StoreTarget) -> AsyncIterator[object]:
        raise ConnectionError("checkpointer unreachable")
        yield

    monkeypatch.setattr("src.agent.executor.checkpointer", _down)
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, _proposing_order)
    with capture_logs() as logs:
        state = await run_tick(tick=1, store=target, now=T0, spend=True)
    assert state.error is None and state.shift_report is not None
    assert state.writes_failed == 1 and state.writes_proposed == () and state.held == ("alkali-flat-water:water_low",)
    assert any(entry["event"] == "gate_unavailable" for entry in logs)
    async with store_session(url=target.url, schema=target.schema) as session:
        rows = (await session.execute(text("select incident_key, held_reason from incidents"))).all()
    assert [tuple(r) for r in rows] == [("alkali-flat-water:water_low", "gate_unavailable")]


# --- the held set, durable ---------------------------------------------------------------------- #
@respx.mock
async def test_the_held_set_survives_a_restart(first_sight: Settings, monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """`docs/issues.md` #3 closed. A tick that holds writes the reason on the row; a fresh
    process reads it back before its first tick; a tick that gets an answer clears it."""
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: _held_order(key, violations=("transport_error",)))
    crashed = await run_tick(tick=1, store=target, now=T0, spend=True)
    assert crashed.held == ("alkali-flat-water:water_low",)
    assert await restore_held(target) == frozenset({"alkali-flat-water:water_low"}), "what a restarted loop would start with"
    async with store_session(url=target.url, schema=target.schema) as session:
        assert (await session.execute(text("select held_reason from incidents"))).scalar_one() == "transport_error"

    respx.mock.reset()
    serve(respx.mock, LOW)
    _stub_spend(monkeypatch, lambda key: to_work_order(packet=_packet("alkali-flat-water"), agent="water_feed", response=_response(_answer())))
    answered = await run_tick(tick=2, store=target, now=T1, spend=True, held=await restore_held(target))
    assert answered.held == () and await restore_held(target) == frozenset()


async def test_the_loop_starts_from_what_the_previous_run_was_carrying() -> None:
    calls: list[dict[str, object]] = []

    async def _restore(_target: StoreTarget) -> frozenset[str]:
        return frozenset({"alkali-flat-water:water_low"})

    with capture_logs() as logs:
        await run_loop(store=TEST_TARGET, interval_s=0, spend=False, tick_fn=_fake_tick([RanchState()], calls=calls), sleep=_no_sleep, max_ticks=1, restore=_restore)
    assert calls[0]["held"] == frozenset({"alkali-flat-water:water_low"}), "the first tick re-routes what the last run could not get answered"
    assert any(entry["event"] == "held_restored" and entry["count"] == 1 for entry in logs)

    async def _unreadable(_target: StoreTarget) -> frozenset[str]:
        raise ConnectionError("ledger down")

    with capture_logs() as logs:
        code = await run_loop(store=TEST_TARGET, interval_s=0, spend=False, tick_fn=_fake_tick([RanchState()]), sleep=_no_sleep, max_ticks=1, restore=_unreadable)
    assert code == EXIT_OK and any(entry["event"] == "held_restore_failed" for entry in logs), "an unreadable ledger starts the loop empty rather than refusing to start"


# --- the checkpointer's own migration ------------------------------------------------------------ #
async def test_migration_0004_wrote_exactly_the_version_rows_the_library_expects(store: AsyncSession) -> None:
    """`setup()` would have created these tables silently on whichever database the loop was
    pointed at. Alembic did instead, and the version rows match, so a later `setup()` is a no-op."""
    versions = (await store.execute(text("select v from checkpoint_migrations order by v"))).scalars().all()
    assert list(versions) == list(range(1, len(BasePostgresSaver.MIGRATIONS)))
    tables = (await store.execute(text("select table_name from information_schema.tables where table_schema = :s and table_name like 'checkpoint%' order by 1"), {"s": SCHEMA_TEST})).scalars().all()
    assert list(tables) == ["checkpoint_blobs", "checkpoint_migrations", "checkpoint_writes", "checkpoints"]


async def test_the_checkpointer_refuses_a_database_behind_the_installed_library(gate_target: StoreTarget, monkeypatch: pytest.MonkeyPatch) -> None:
    """A library upgrade is a new alembic revision, never a silent `setup()` on prod."""
    monkeypatch.setattr(BasePostgresSaver, "MIGRATIONS", [*BasePostgresSaver.MIGRATIONS, "SELECT 1;"])
    with pytest.raises(CheckpointerNotMigratedError, match="library was upgraded"):
        async with checkpointer(gate_target):
            pass


async def test_the_checkpointer_only_ever_opens_an_agent_schema() -> None:
    assert psycopg_url("postgresql+asyncpg://u:p@h/db") == "postgresql://u:p@h/db"
    with pytest.raises(SchemaGuardError):
        async with checkpointer(StoreTarget(name="x", url="postgresql+asyncpg://x", schema="farm")):
            pass


# --- the CLI: the whole of what a human can do at M6 -------------------------------------------- #
async def test_the_gate_cli_lists_approves_and_rejects(gate_target: StoreTarget, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Same shape as `python -m src.tools.chaos`. If a decision cannot be expressed here, the
    M8 endpoint will not be able to express it either."""
    performed: list[str] = []

    async def _perform(tool: str, args: dict[str, object], approval: Approval) -> tuple[str, str]:
        performed.append(f"{tool} by {approval.decided_by}")
        return "written", "{}"

    monkeypatch.setattr("src.agent.gate.resolve_store", lambda *_a, **_k: gate_target)
    monkeypatch.setattr("src.agent.gate.perform_write", _perform)
    monkeypatch.setattr("src.agent.gate.configure_logging", lambda: "run")
    async with checkpointer(gate_target) as saver:
        gate = build_gate(saver, perform=_perform)
        a = await propose(gate, _proposal("alkali-flat-water:water_low"))
        b = await propose(gate, _proposal("windmill-pasture-water:water_low"))

    def run(argv: list[str]) -> Awaitable[int]:
        return asyncio.get_running_loop().run_in_executor(None, gate_main, argv)  # the CLI owns its own asyncio.run

    assert await run(["list"]) == 0
    out = capsys.readouterr().out
    assert "2 writes waiting" in out and a.audit_id in out and b.audit_id in out and "restock_feed(quantity=16.7, sku='alkali-flat-water-2')" in out

    assert await run(["approve", a.audit_id, "--by", "scooter"]) == 0 and performed == ["restock_feed by scooter"]
    assert await run(["reject", b.audit_id, "--by", "scooter", "--reason", "tank 2 is fine"]) == 0
    assert await run(["approve", b.audit_id, "--by", "scooter"]) == 1 and "already decided" in capsys.readouterr().out
    assert await run(["list"]) == 0 and "0 writes waiting" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        await run(["reject", a.audit_id])  # --reason is required by the parser itself


async def test_the_suite_never_points_a_file_handler_at_the_real_logs(store: AsyncSession) -> None:
    """`configure_logging` is idempotent, so whichever directory it saw first is where every file
    handler writes for the rest of the process, and `alembic/env.py` calls it from the session-scoped
    `migrated_store` before any function-scoped patch. Found at M6 as fixture lines in the real
    `logs/audit.jsonl`. `store` is requested so the migration, and therefore that first call, has run."""
    import logging

    from src.utils.logger import AUDIT_STREAM, configure_logging

    configure_logging()
    real = str((REPO_ROOT / "logs").resolve()).lower()
    for handler in logging.getLogger(AUDIT_STREAM).handlers:
        filename = str(getattr(handler, "baseFilename", "") or "")
        assert not filename.lower().startswith(real), f"a test process would write receipts into {filename}"
