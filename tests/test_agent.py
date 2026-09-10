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

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import agent
from src.agent.agent import AGENTS, DEFAULT_OWNER, HERD_HEALTH, ROUTES, owner_for, owners_for, route, unrouted_categories
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
from src.agent.state import Finding, Incident
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

    first = await run_tick(tick=1, store=target, now=T0)
    assert first.error is None and first.failed_stage is None
    assert (first.sensors_read, first.sensors_failed) == (3, 0)
    assert [f.category for f in first.findings] == ["water_low"]
    assert len(first.opened) == 1 and first.ongoing == () and first.resolved == ()
    assert first.routed == {"water_feed": ("alkali-flat-water:water_low",)}

    second = await run_tick(tick=2, store=target, now=T1)
    assert len(second.opened) == 0 and len(second.ongoing) == 1
    assert second.ongoing[0].occurrences == 2
    # Nothing new opened, so nothing is handed to a model. This is the entire cost story.
    assert second.routed == {}


@respx.mock
async def test_a_fault_that_heals_resolves_on_the_next_tick(catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    await run_tick(tick=1, store=target, now=T0)

    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 9.0, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    healed = await run_tick(tick=2, store=target, now=T1)

    assert healed.findings == () and len(healed.resolved) == 1
    assert healed.resolved[0].status == "resolved" and healed.resolved[0].resolved_at == T1


@respx.mock
async def test_a_sensor_that_did_not_answer_holds_its_incident_open(catalog: RanchMap, target: StoreTarget) -> None:
    """The rail that matters most in an outage. "No finding" and "no reading" are different
    facts, and conflating them means one upstream failure closes every incident on the
    ranch and reports an all-clear at the worst possible moment."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    await run_tick(tick=1, store=target, now=T0)

    respx.mock.reset()
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0}, broken={"alkali-flat-water"})
    dark = await run_tick(tick=2, store=target, now=T1)

    assert (dark.sensors_read, dark.sensors_failed) == (2, 1)
    assert dark.findings == (), "a failed read produces no finding; it is not a fault"
    assert dark.resolved == (), "the tank stopped answering, so nothing is known to have healed"


@respx.mock
async def test_exactly_one_tick_line_is_written_and_it_carries_the_counts(catalog: RanchMap, target: StoreTarget) -> None:
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})

    with structlog.testing.capture_logs() as logs:
        await run_tick(tick=7, store=target, now=T0)

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
        state = await run_tick(tick=1, store=target, now=T0)

    assert state.failed_stage == "catalog" and state.error is not None
    assert state.opened == () and state.resolved == ()
    assert len([line for line in logs if line["event"] == "tick"]) == 1, "a failed tick still writes exactly one line"


async def test_a_failing_stage_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch, catalog: RanchMap, target: StoreTarget) -> None:
    """A tick reports its own failure. An exception escaping `run_tick` would take the loop
    down at M4, and one bad afternoon for one stage is not an outage."""

    async def _boom(*_a: object, **_k: object) -> object:
        raise httpx.ConnectError("sensor api unreachable")

    monkeypatch.setattr("src.agent.executor.sweep", _boom)

    state = await run_tick(tick=1, store=target, now=T0)

    assert state.failed_stage == "sweep"
    assert state.error is not None and "ConnectError" in state.error
    assert "FAILED at sweep" in summarize(state)


@respx.mock
async def test_the_summary_line_names_the_reading_a_human_would_ask_about(catalog: RanchMap, target: StoreTarget) -> None:
    """`--once` is run by a person watching a console. A summary that says "1 finding" and
    nothing else sends them to the JSON to learn anything at all."""
    serve(respx.mock, {"alkali-flat-water": 1.2, "east-allotment-fence": 6.4, "home-place-bin": 900.0})
    state = await run_tick(tick=1, store=target, now=T0)
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
