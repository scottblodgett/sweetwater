"""One tick end to end: catalog, sweep, triage, reconcile, route.

The upstream is respx against a fake host and the ledger is the local `sw_ops_test`
schema, so this passes on a plane. What it proves that the per-module tests cannot:
the five stages agree on the same objects, the tick line is written exactly once, and a
failure inside a stage is reported rather than raised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
import structlog
from sqlalchemy import text

from src.agent.memory import SCHEMA_TEST, StoreTarget, build_engine
from src.agent.tick import run_tick, summarize
from src.tools.mcp_client import RanchMap, SensorRef
from src.utils.config import Settings

BASE = "https://sensor.test"
T0 = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
T1 = datetime(2026, 9, 10, 14, 35, tzinfo=UTC)

TANK = SensorRef(sensor_id="alkali-flat-water", sensor_type="water-level", location="Alkali Flat", status="online")
FENCE = SensorRef(sensor_id="east-allotment-fence", sensor_type="fence-voltage", location="East Allotment", status="online")
BIN = SensorRef(sensor_id="home-place-bin", sensor_type="feed-bin-weight", location="Home Place", status="online")
CATALOG = RanchMap(sensors=(TANK, FENCE, BIN))


@pytest.fixture(autouse=True)
def _fake_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(sensor_api=BASE, sweep_concurrency=4, upstream_timeout_ms=500, _env_file=None)
    monkeypatch.setattr("src.tools.sensors.get_settings", lambda: settings)
    monkeypatch.setattr("src.utils.helpers.get_settings", lambda: settings)


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> RanchMap:
    """The catalog read is stubbed; the value reads are not.

    Deliberate split: `fetch_catalog` talks MCP over stdio-free transport that respx does
    not model, while the 160 value reads are plain HTTP and are the part with the
    interesting failure modes.
    """

    async def _catalog() -> tuple[RanchMap, str]:
        return CATALOG, "mcp_resource"

    monkeypatch.setattr("src.agent.tick.fetch_catalog", _catalog)
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


def reading(ref: SensorRef, value: object, *, status: str = "online") -> dict[str, object]:
    return {"data": {"id": ref.sensor_id, "type": ref.sensor_type, "locationName": ref.location, "status": status, "latestReading": {"value": value, "recordedAt": "2026-09-10T14:30:00.000Z"}}}


def serve(mock: respx.MockRouter, values: dict[str, object], *, broken: set[str] = frozenset()) -> None:
    for ref in CATALOG.sensors:
        route = mock.get(f"{BASE}/sensors/{ref.sensor_id}")
        if ref.sensor_id in broken:
            route.mock(return_value=httpx.Response(503, json={"error": {"category": "upstream"}}))
        else:
            route.mock(return_value=httpx.Response(200, json=reading(ref, values[ref.sensor_id])))


# --------------------------------------------------------------------------- #
# the happy path, twice
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# the tick line, and failure
# --------------------------------------------------------------------------- #
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

    monkeypatch.setattr("src.agent.tick.fetch_catalog", _empty)

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

    monkeypatch.setattr("src.agent.tick.sweep", _boom)

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
