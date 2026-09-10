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

Never Supabase. The upstream is respx against a fake host and the ledger is the local
`sw_ops_test` schema, so `pytest` passes on a plane.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx
import structlog
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
from src.agent.executor import run_tick, summarize
from src.agent.memory import (
    ALLOWED_SCHEMAS,
    RANCH_SCHEMAS,
    SCHEMA,
    SCHEMA_TEST,
    SchemaGuardError,
    StoreTarget,
    assert_agent_schema,
    assert_droppable_schema,
    assert_local_test_url,
    build_engine,
    connect_args_for,
    counts_by_status,
    incidents,
    metadata,
    open_incidents,
    reconcile,
)
from src.agent.state import Finding, Incident, RanchState, WorkOrder
from src.agent.workers import AGENT_CONCURRENCY, citable_rules, fan_out, run_agent, run_water_feed, to_work_order
from src.models.llm_client import THINKING_BUDGET, ModelResponse, call_tier2, resolve_provider
from src.prompts.agent_prompts import MANDATES, SUPERVISOR_MANDATE
from src.prompts.system_prompts import SHIFT_REPORT_SCHEMA, WORK_ORDER_SCHEMA, system_prompt
from src.tools.allowlists import DEPLOYED_TOOLS
from src.tools.evidence import EvidencePacket, HistoryPoint, PastureContext, SiblingReading
from src.tools.mcp_client import RanchMap, SensorRef, flatten_exception, parse_ranch_map
from src.tools.triage import ALL_CATEGORIES
from src.utils.config import Settings
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
async def test_a_tick_opens_incidents_and_the_next_tick_calls_them_ongoing(catalog: RanchMap, target: StoreTarget) -> None:
    """The whole point of the store, exercised through the tick rather than around it. A
    persisting fault re-alarming every five minutes is how a service teaches its client to
    ignore it."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})

    first = await run_tick(tick=1, store=target, now=T0, spend=False)
    assert first.error is None and first.failed_stage is None
    assert (first.sensors_read, first.sensors_failed) == (3, 0)
    assert [f.category for f in first.findings] == ["water_low"]
    assert len(first.opened) == 1 and first.ongoing == () and first.resolved == ()
    assert first.routed == {"water_feed": ("alkali-flat-water:water_low",)}

    second = await run_tick(tick=2, store=target, now=T1, spend=False)
    assert len(second.opened) == 0 and len(second.ongoing) == 1
    assert second.ongoing[0].occurrences == 2
    # Nothing new opened, so nothing is handed to a model. This is the entire cost story.
    assert second.routed == {}


@respx.mock
async def test_a_fault_that_heals_resolves_on_the_next_tick(catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    await run_tick(tick=1, store=target, now=T0, spend=False)

    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 9.0, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    healed = await run_tick(tick=2, store=target, now=T1, spend=False)

    assert healed.findings == () and len(healed.resolved) == 1
    assert healed.resolved[0].status == "resolved" and healed.resolved[0].resolved_at == T1


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
    assert dark.resolved == (), "the tank stopped answering, so nothing is known to have healed"


@respx.mock
async def test_exactly_one_tick_line_is_written_and_it_carries_the_counts(catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})

    with structlog.testing.capture_logs() as logs:
        await run_tick(tick=7, store=target, now=T0, spend=False)

    lines = [line for line in logs if line["event"] == "tick"]
    assert len(lines) == 1
    line = lines[0]
    assert (line["sensors_read"], line["sensors_failed"], line["findings"], line["opened"]) == (3, 0, 1, 1)
    assert line["failed_stage"] is None and line["error"] is None
    assert line["agents_routed"] == ["water_feed"] and line["catalog_source"] == "mcp_resource"
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
async def test_the_summary_line_names_the_reading_a_human_would_ask_about(catalog: RanchMap, target: StoreTarget) -> None:
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
# 3. the store: opened -> ongoing -> resolved, against a real Postgres
# =========================================================================== #
async def test_a_new_finding_opens_exactly_once(store: AsyncSession) -> None:
    first = await reconcile(store, [finding()], tick=1, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T0)
    assert first.counts == {"opened": 1, "ongoing": 0, "resolved": 0}
    assert first.opened[0].key == "alkali-flat-water:water_low"
    assert first.opened[0].tick_opened == 1 and first.opened[0].occurrences == 1

    second = await reconcile(store, [finding()], tick=2, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T1)
    assert second.counts == {"opened": 0, "ongoing": 1, "resolved": 0}, "a persisting fault is ongoing, never re-alarmed"

    third = await reconcile(store, [finding()], tick=3, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T2)
    assert third.counts == {"opened": 0, "ongoing": 1, "resolved": 0}
    assert third.ongoing[0].occurrences == 3
    assert third.ongoing[0].tick_opened == 1 and third.ongoing[0].tick_last_seen == 3
    assert third.ongoing[0].first_seen_at == T0 and third.ongoing[0].last_seen_at == T2

    # One row for the whole story, not three.
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 1


async def test_a_finding_that_stops_appearing_resolves(store: AsyncSession) -> None:
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.counts == {"opened": 0, "ongoing": 0, "resolved": 1}
    assert result.resolved[0].resolved_at == T1
    assert await open_incidents(store) == (), "a resolved incident is no longer open"
    assert await counts_by_status(store) == {"resolved": 1}


async def test_the_same_fault_returning_after_a_resolve_is_a_new_incident(store: AsyncSession) -> None:
    """History survives. The same tank drying out in March and again in July is two work
    orders, and collapsing them would overwrite the March story."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)
    again = await reconcile(store, [finding()], tick=3, read_sensor_ids=ALL_SENSORS, now=T2)

    assert again.counts == {"opened": 1, "ongoing": 0, "resolved": 0}
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 2
    assert again.opened[0].tick_opened == 3


async def test_a_sensor_that_did_not_answer_resolves_nothing(store: AsyncSession) -> None:
    """The failure this prevents: the Sensor API has a bad five minutes, the sweep returns
    errors instead of readings, every incident on the ranch closes, and the feed reports an
    all-clear at the exact moment nobody can see anything."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)

    blind = await reconcile(store, [], tick=2, read_sensor_ids=(), now=T1)

    assert blind.counts == {"opened": 0, "ongoing": 0, "resolved": 0}
    assert blind.skipped_unread == ("alkali-flat-water:water_low",)
    still_open = await open_incidents(store)
    assert len(still_open) == 1 and still_open[0].status == "opened"
    assert still_open[0].last_seen_at == T0, "an unread tick must not restamp the incident as freshly seen"


async def test_other_sensors_still_resolve_while_one_is_dark(store: AsyncSession) -> None:
    """Per sensor, not per tick. One dark sensor must not freeze the whole ledger."""
    await reconcile(store, [finding(), finding("east-allotment-fence", category="fence_down")], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=("east-allotment-fence",), now=T1)

    assert [i.sensor_id for i in result.resolved] == ["east-allotment-fence"]
    assert result.skipped_unread == ("alkali-flat-water:water_low",)


async def test_severity_follows_the_current_reading_on_an_ongoing_incident(store: AsyncSession) -> None:
    """Code owns severity at every tick, not just the first one. A tank that fills back to
    a warning is still one incident, described accurately."""
    await reconcile(store, [finding(severity="critical", value=1.4)], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [finding(severity="warning", value=5.0)], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.ongoing[0].severity == "warning"
    assert result.ongoing[0].last_value == "5"
    row = (await store.execute(text("select severity, last_value, status from incidents"))).one()
    assert tuple(row) == ("warning", "5", "ongoing")


async def test_two_categories_on_one_sensor_are_two_incidents(store: AsyncSession) -> None:
    """A degraded probe reporting a dry tank is two work orders for two different people.
    The key is `sensor:category`, so they never collapse."""
    result = await reconcile(store, [finding(), finding(category="sensor_degraded", severity="warning")], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    assert {i.key for i in result.opened} == {"alkali-flat-water:water_low", "alkali-flat-water:sensor_degraded"}


async def test_a_gate_value_is_stored_as_open_not_as_true(store: AsyncSession) -> None:
    gate = Finding(sensor_id="coyote-draw-gate", sensor_type="gate", location="Coyote Draw", category="gate_open", severity="warning", value=True, summary="Coyote Draw: gate coyote-draw-gate reads open.")
    result = await reconcile(store, [gate], tick=1, read_sensor_ids=("coyote-draw-gate",), now=T0)
    assert result.opened[0].last_value == "open"


async def test_the_owner_is_recorded_when_routing_supplies_one(store: AsyncSession) -> None:
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
async def test_a_tick_told_not_to_spend_stops_at_the_end_of_the_free_pass(catalog: RanchMap, target: StoreTarget) -> None:
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
