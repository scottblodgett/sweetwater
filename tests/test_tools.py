"""The tool layer: the sweep, and the triage truth table.

Two suites in one file, per `docs/plan.md`:

  1. **the sweep** - errors come back as data, gates stay boolean, fan-out stays bounded,
     and nothing in this layer retries
  2. **triage** - severity is deterministic and code-owned, every type is decided
     explicitly, and nothing an unrecognized sensor sends can come out nominal

If a triage rail fails, a threshold moved or a type fell through a branch that should not
exist. Loosening one to make it pass is the failure mode `tests/CLAUDE.md` is written
against.

Nothing here touches the live ranch. `pytest` has to pass on a plane, so every upstream is
a respx route against a fake host.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from src.agent.agent import AGENTS
from src.agent.memory import (
    SCHEMA_TEST,
    active_chaos_events,
    build_engine,
    chaos_counts_by_status,
    expire_chaos_events,
    insert_chaos_events,
    reconcile,
    session_factory,
)
from src.agent.state import Incident
from src.agent.workers import citable_rules
from src.tools.allowlists import (
    DEPLOYED_TOOLS,
    GATE_LANDED,
    SLICES,
    UNASSIGNED_TOOLS,
    WRITE_TOOLS,
    Approval,
    WriteGateError,
    assert_callable,
    bound_tools_for,
    is_allowed,
    proposable_tools_for,
    tools_for,
)
from src.tools.chaos import (
    ANIMAL_STATUS_NORMAL,
    ANIMAL_STATUSES,
    KIND_ANIMAL,
    KIND_SENSOR,
    MODE_ANIMAL_STATUS,
    MODE_DEGRADED,
    MODE_DRIFT,
    MODE_GATE_OPEN,
    MODE_OFFLINE,
    MODE_PIN,
    MODE_SENTINEL,
    STATUS_ACTIVE,
    ChaosCatalogError,
    ChaosEvent,
    FaultSpec,
    PlannedFault,
    Scenario,
    active_overlay,
    apply_overlay,
    blocked_reason,
    expire,
    fire_animal_events,
    inject_for_tick,
    load_catalog,
    load_fixtures,
    parse_catalog,
    plan,
    restore_cohort,
)
from src.tools.chaos import reset_warn_once as _reset_chaos
from src.tools.evidence import (
    CONDITIONS_FOR_SOP,
    KNOWLEDGE_BASE,
    SOP_FOR_CATEGORY,
    EvidencePacket,
    HistoryPoint,
    PastureContext,
    PastureRoster,
    assemble,
    conditions_for,
    load_sop,
    parse_history,
    siblings_for,
    slugify,
)
from src.tools.herd import (
    HERD_MAX_PAGES,
    HERD_PAGE,
    HERD_PAGE_CONCURRENCY,
    AnimalRecord,
    CareTask,
    HerdError,
    HerdSweepResult,
    Observation,
    parse_timestamp,
    sweep_herd,
)
from src.tools.mcp_client import RanchMap, SensorRef, call_tool
from src.tools.sensors import SensorReading, SweepError, parse_sensor_payload, read_sensor, sweep
from src.tools.triage import (
    ALL_CATEGORIES,
    ANIMAL_CATEGORIES,
    CATEGORY_CARE_OVERDUE,
    CATEGORY_DECEASED,
    CATEGORY_DEGRADED,
    CATEGORY_FAULT,
    CATEGORY_INACTIVE,
    CATEGORY_OBSERVATION_HIGH,
    CATEGORY_OFFLINE,
    CATEGORY_UNKNOWN_TYPE,
    OBSERVATION_WINDOW,
    RULES,
    triage_herd,
    triage_reading,
    triage_sweep,
)
from src.tools.triage import reset_warn_once as _reset
from src.utils.config import Settings

BASE = "https://sensor.test"
FARM = "https://farm.test"
CARE = "https://care.test"
FROZEN_TS = "2026-09-10T14:30:00.000Z"

REF = SensorRef(sensor_id="alkali-flat-water", sensor_type="water-level", location="Alkali Flat", status="online")


@pytest.fixture(autouse=True)
def _fake_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake SENSOR_API, so a stray unmocked request fails loudly instead of quietly
    reaching the deployed ranch."""
    settings = Settings(sensor_api=BASE, farm_api=FARM, care_api=CARE, sweep_concurrency=4, upstream_timeout_ms=500, _env_file=None)
    monkeypatch.setattr("src.tools.sensors.get_settings", lambda: settings)
    monkeypatch.setattr("src.tools.herd.get_settings", lambda: settings)
    monkeypatch.setattr("src.tools.evidence.get_settings", lambda: settings)
    monkeypatch.setattr("src.utils.helpers.get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def _clean_warn_once() -> None:
    _reset()


def payload(value: object, *, status: str = "online") -> dict[str, object]:
    return {"data": {"id": REF.sensor_id, "type": REF.sensor_type, "locationName": "Alkali Flat", "status": status, "latestReading": {"value": value, "recordedAt": FROZEN_TS}}}


def reading(sensor_type: str, value: float | bool | None, *, sensor_id: str = "test-sensor", status: str = "online", location: str = "Alkali Flat") -> SensorReading:
    """Frozen timestamp, literal everything. No test asserts on a value it got from the clock."""
    return SensorReading(sensor_id=sensor_id, sensor_type=sensor_type, location=location, status=status, value=value, recorded_at=FROZEN_TS)


# =========================================================================== #
# 1a. parsing one payload
# =========================================================================== #
def test_parses_a_normal_reading() -> None:
    got = parse_sensor_payload(REF, payload(1.4))
    assert isinstance(got, SensorReading)
    assert (got.sensor_id, got.value, got.recorded_at, got.location) == ("alkali-flat-water", 1.4, FROZEN_TS, "Alkali Flat")


def test_a_gate_value_stays_boolean() -> None:
    """`isinstance(True, int)` is True in Python. Checking for a number first turns every
    gate into the number 1, and triage's one non-threshold type loses its only rule."""
    gate = SensorRef(sensor_id="coyote-draw-gate", sensor_type="gate", location="Coyote Draw")
    got = parse_sensor_payload(gate, {"data": {"id": "coyote-draw-gate", "type": "gate", "status": "online", "latestReading": {"value": True, "recordedAt": FROZEN_TS}}})
    assert isinstance(got, SensorReading)
    assert got.value is True and isinstance(got.value, bool)


def test_a_dark_sensor_is_a_successful_read_with_no_value() -> None:
    """`status: "offline"` with `latestReading: null` is a sensor with nothing to say, not
    a failed request. Triage owns what that means; this layer must not decide for it."""
    got = parse_sensor_payload(REF, {"data": {"id": REF.sensor_id, "type": REF.sensor_type, "status": "offline", "latestReading": None}})
    assert isinstance(got, SensorReading)
    assert got.value is None and got.status == "offline"


def test_a_structurally_empty_envelope_is_an_error_not_an_empty_reading() -> None:
    """The one known undetectable upstream fault: HTTP 200 with a perfect empty envelope.
    No retry layer can see it, so the defense is a plausibility check on content."""
    got = parse_sensor_payload(REF, {"data": {}})
    assert isinstance(got, SweepError) and got.category == "bad_response"


def test_a_non_numeric_value_reads_as_no_value() -> None:
    got = parse_sensor_payload(REF, payload("wet"))
    assert isinstance(got, SensorReading) and got.value is None


# =========================================================================== #
# 1b. errors are data, and nothing here retries
# =========================================================================== #
@respx.mock
async def test_a_500_comes_back_as_retriable_data() -> None:
    route = respx.get(f"{BASE}/sensors/{REF.sensor_id}").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    async with httpx.AsyncClient(base_url=BASE) as client:
        got = await read_sensor(client, REF)
    assert isinstance(got, SweepError) and got.category == "http_error" and got.retriable is True
    assert route.call_count == 1, "the tool layer must not retry; attempts and budget belong to the caller"


@respx.mock
async def test_a_422_comes_back_as_not_retriable() -> None:
    """The canonical bug this prevents: a helper that retries a 422 forever and cannot be
    told a budget by the only process that has one."""
    respx.get(f"{BASE}/sensors/{REF.sensor_id}").mock(return_value=httpx.Response(422))
    async with httpx.AsyncClient(base_url=BASE) as client:
        got = await read_sensor(client, REF)
    assert isinstance(got, SweepError) and got.retriable is False and got.status_code == 422


@respx.mock
async def test_a_timeout_is_classified_and_never_raised() -> None:
    respx.get(f"{BASE}/sensors/{REF.sensor_id}").mock(side_effect=httpx.ReadTimeout("timed out"))
    async with httpx.AsyncClient(base_url=BASE) as client:
        got = await read_sensor(client, REF)
    assert isinstance(got, SweepError) and got.category == "timeout" and got.retriable is True


@respx.mock
async def test_non_json_is_classified_rather_than_exploding() -> None:
    respx.get(f"{BASE}/sensors/{REF.sensor_id}").mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
    async with httpx.AsyncClient(base_url=BASE) as client:
        got = await read_sensor(client, REF)
    assert isinstance(got, SweepError) and got.category == "bad_response"


# =========================================================================== #
# 1c. the sweep
# =========================================================================== #
async def test_sweep_never_exceeds_the_concurrency_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """160 unbounded requests against API Gateway is a wall of Lambda cold starts and
    reads to the other side as a load test. If this fails, a `gather` replaced
    `gather_bounded`."""
    in_flight = 0
    peak = 0

    async def tracked(_client: httpx.AsyncClient, ref: SensorRef) -> SensorReading:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.005)  # long enough to interleave, short enough to not matter
        in_flight -= 1
        return SensorReading(sensor_id=ref.sensor_id, sensor_type=ref.sensor_type, location=ref.location, status="online", value=17.9, recorded_at=FROZEN_TS)

    monkeypatch.setattr("src.tools.sensors.read_sensor", tracked)
    refs = tuple(SensorRef(sensor_id=f"s-{i:03d}", sensor_type="water-level", location="Home Place") for i in range(160))

    result = await sweep(refs)

    assert len(result.readings) == 160 and result.errors == ()
    assert peak <= 4, f"concurrency ceiling breached: {peak} reads in flight against a limit of 4"
    assert peak > 1, "a ceiling that serializes the sweep would pass the assertion above and miss the tick budget"


async def test_sweep_keeps_going_when_some_sensors_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad sensor is not a failed sweep. 159 good readings are still the tick's job."""

    async def flaky(_client: httpx.AsyncClient, ref: SensorRef) -> SensorReading | SweepError:
        if ref.sensor_id.endswith("7"):
            return SweepError(sensor_id=ref.sensor_id, category="timeout", message="timed out")
        return SensorReading(sensor_id=ref.sensor_id, sensor_type=ref.sensor_type, location=ref.location, status="online", value=17.9)

    monkeypatch.setattr("src.tools.sensors.read_sensor", flaky)
    refs = tuple(SensorRef(sensor_id=f"s-{i:03d}", sensor_type="water-level", location="Home Place") for i in range(20))

    result = await sweep(refs)
    assert len(result.readings) == 18 and len(result.errors) == 2 and result.attempted == 20


async def test_a_read_that_raises_still_comes_back_as_one_sensors_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gather_bounded` returns exceptions as values. An unexpected raise has to land as a
    per-sensor error, or one surprise takes down the whole tick."""

    async def explodes(_client: httpx.AsyncClient, _ref: SensorRef) -> SensorReading:
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr("src.tools.sensors.read_sensor", explodes)
    result = await sweep((REF,))
    assert result.readings == () and len(result.errors) == 1
    assert result.errors[0].category == "unexpected" and result.errors[0].retriable is False


async def test_an_empty_sweep_is_not_an_error() -> None:
    result = await sweep(())
    assert result.readings == () and result.errors == () and result.attempted == 0


# =========================================================================== #
# 2a. the triage truth table: one row per type, per tier
# =========================================================================== #
TRUTH_TABLE = [
    # type,                value,   expected severity, expected category
    ("water-level", 17.9, "nominal", None),
    ("water-level", 5.0, "warning", "water_low"),
    ("water-level", 1.4, "critical", "water_low"),
    ("feed-bin-weight", 1167.0, "nominal", None),
    ("feed-bin-weight", 420.0, "warning", "feed_low"),
    ("feed-bin-weight", 100.0, "critical", "feed_low"),
    ("temperature", 48.2, "nominal", None),
    ("temperature", 22.0, "warning", "freeze_risk"),
    ("temperature", -21.2, "critical", "freeze_risk"),
    ("temperature", 95.0, "warning", "heat_stress"),
    ("temperature", 101.1, "critical", "heat_stress"),
    ("humidity", 97.1, "nominal", None),
    ("humidity", 5.1, "nominal", None),
    ("wind-speed", 10.2, "nominal", None),
    ("wind-speed", 50.0, "warning", "high_wind"),
    ("wind-speed", 70.0, "critical", "high_wind"),
    ("snow-depth", 9.7, "nominal", None),
    ("snow-depth", 20.0, "warning", "deep_snow"),
    ("snow-depth", 36.0, "critical", "deep_snow"),
    ("gate", False, "nominal", None),
    ("gate", True, "warning", "gate_open"),
    ("fence-voltage", 6.1, "nominal", None),
    ("fence-voltage", 3.2, "warning", "fence_down"),
    ("fence-voltage", 0.4, "critical", "fence_down"),
    ("battery-charge", 82.1, "nominal", None),
    ("battery-charge", 24.0, "warning", "power_low"),
    ("battery-charge", 11.7, "critical", "power_low"),
    ("fuel-level", 80.3, "nominal", None),
    ("fuel-level", 18.0, "warning", "fuel_low"),
    ("fuel-level", 4.9, "critical", "fuel_low"),
    ("wellhead-pressure", 310.0, "nominal", None),
    ("wellhead-pressure", 430.4, "warning", "wellhead_overpressure"),
    ("wellhead-pressure", 460.0, "critical", "wellhead_overpressure"),
    ("wellhead-pressure", 189.5, "warning", "wellhead_underpressure"),
    ("wellhead-pressure", 140.0, "critical", "wellhead_underpressure"),
    ("soil-moisture", 28.9, "nominal", None),
    ("soil-moisture", 10.0, "warning", "range_dry"),
    ("soil-moisture", 2.0, "critical", "range_dry"),
    ("stream-flow", 7.2, "nominal", None),
    ("stream-flow", 1.5, "warning", "stream_flow_low"),
    ("stream-flow", 0.2, "critical", "stream_flow_low"),
]


@pytest.mark.parametrize(("sensor_type", "value", "severity", "category"), TRUTH_TABLE)
def test_truth_table(sensor_type: str, value: float | bool, severity: str, category: str | None) -> None:
    findings = triage_reading(reading(sensor_type, value))
    if category is None:
        assert findings == [], f"{sensor_type}={value} should be nominal, got {[f.category for f in findings]}"
        return
    matched = [f for f in findings if f.category == category]
    assert len(matched) == 1, f"{sensor_type}={value} expected one {category}, got {[f.category for f in findings]}"
    assert matched[0].severity == severity


def test_every_type_in_the_catalog_is_decided_explicitly() -> None:
    """13 types on the live ranch. A type triage has never heard of is a finding, not a
    default branch, but a type that EXISTS and has no rule is a gap in this table."""
    live_types = {
        "water-level",
        "temperature",
        "humidity",
        "gate",
        "soil-moisture",
        "feed-bin-weight",
        "fence-voltage",
        "battery-charge",
        "fuel-level",
        "wellhead-pressure",
        "stream-flow",
        "wind-speed",
        "snow-depth",
    }
    assert live_types == set(RULES), f"missing: {live_types - set(RULES)}, extra: {set(RULES) - live_types}"
    for name, rule in RULES.items():
        assert rule.bands or rule.no_alarm_because, f"{name} has no bands and no stated reason for having none"


def test_every_critical_band_has_a_warning_tier_underneath_it() -> None:
    """A type that can only be fine or on fire produces a feed that is quiet until it is
    too late. The warning tier is a design rule, so it gets a structural test rather than
    a per-type one."""
    for name, rule in RULES.items():
        for band in rule.bands:
            if band.critical is None:
                continue
            if band.direction == "low":
                assert band.warning > band.critical, f"{name}/{band.category}: warning must sit above critical on a low band"
            else:
                assert band.warning < band.critical, f"{name}/{band.category}: warning must sit below critical on a high band"


# =========================================================================== #
# 2b. the sensor is not the ranch
# =========================================================================== #
def test_a_faulted_probe_is_a_sensor_fault_not_a_cold_snap() -> None:
    """The live ranch answers **-500 with status "online"** on one temperature probe.
    Triaged as a reading it is the coldest place on earth and a critical freeze alarm
    that sends someone to check a tank that is fine."""
    findings = triage_reading(reading("temperature", -500.0, sensor_id="south-windbreak-temp"))
    assert [f.category for f in findings] == [CATEGORY_FAULT]
    assert findings[0].severity == "warning"
    assert "freeze" not in findings[0].summary.lower()


def test_a_dark_sensor_is_offline_and_nothing_else() -> None:
    """`status: "offline"` with `latestReading: null`. There is no value to threshold, and
    inventing one is how a dark tank reads as a full tank."""
    findings = triage_reading(reading("gate", None, sensor_id="coyote-draw-gate", status="offline"))
    assert [f.category for f in findings] == [CATEGORY_OFFLINE]


def test_a_degraded_sensor_is_flagged_and_still_triaged() -> None:
    """Two findings, two work orders: someone fixes the probe, someone else hauls water.
    The incident key is `sensor:category`, so they never collapse into one."""
    findings = triage_reading(reading("water-level", 1.0, sensor_id="windmill-pasture-water", status="degraded"))
    assert {f.category for f in findings} == {CATEGORY_DEGRADED, "water_low"}
    assert {f.key for f in findings} == {"windmill-pasture-water:sensor_degraded", "windmill-pasture-water:water_low"}


def test_a_200_with_no_reading_is_a_fault_not_a_calm_sensor() -> None:
    findings = triage_reading(reading("water-level", None, status="online"))
    assert [f.category for f in findings] == [CATEGORY_FAULT]


def test_a_number_on_a_gate_is_a_broken_feed() -> None:
    """`gate` values are boolean. Compared against a threshold, `1` would produce a
    confident and wrong severity, which is worse than no answer."""
    findings = triage_reading(reading("gate", 1.0))
    assert [f.category for f in findings] == [CATEGORY_FAULT]
    assert "no threshold was applied" in findings[0].summary


def test_a_boolean_on_a_numeric_type_is_a_broken_feed() -> None:
    findings = triage_reading(reading("water-level", True))
    assert [f.category for f in findings] == [CATEGORY_FAULT]


def test_an_unrecognized_status_is_never_nominal() -> None:
    findings = triage_reading(reading("water-level", 17.9, status="haunted"))
    assert [f.category for f in findings] == [CATEGORY_DEGRADED]


# =========================================================================== #
# 2c. warn_once
# =========================================================================== #
def test_unknown_type_trips_warn_once_and_still_opens_a_finding() -> None:
    """A new sensor type silently reading as nominal is this module's most likely bug, so
    there is no default branch: it warns once for the humans and reports every tick for
    the store.

    `capture_logs`, not `caplog`: these lines go through structlog, and in a test process
    that never called `configure_logging` they never reach the stdlib at all. A `caplog`
    assertion here passes on an empty list and proves nothing.
    """
    with capture_logs() as logs:
        first = triage_reading(reading("h2s-gas", 12.0, sensor_id="tank-battery-h2s"))
        second = triage_reading(reading("h2s-gas", 14.0, sensor_id="tank-battery-h2s"))

    assert [f.category for f in first] == [CATEGORY_UNKNOWN_TYPE]
    assert first[0].severity == "warning"
    assert [f.category for f in second] == [CATEGORY_UNKNOWN_TYPE], "every tick still reports it; only the log is once"
    assert sum(1 for line in logs if line.get("event") == "unknown_sensor_type") == 1


def test_warn_once_is_per_value_not_global() -> None:
    """Two new types must not silence each other. Once per unrecognized value."""
    with capture_logs() as logs:
        triage_reading(reading("h2s-gas", 12.0))
        triage_reading(reading("cathodic-protection", 0.8))
    assert sum(1 for line in logs if line.get("event") == "unknown_sensor_type") == 2


def test_an_unrecognized_status_also_warns_once() -> None:
    with capture_logs() as logs:
        triage_reading(reading("water-level", 17.9, status="haunted"))
        triage_reading(reading("water-level", 17.9, status="haunted"))
    assert sum(1 for line in logs if line.get("event") == "unknown_sensor_status") == 1


# =========================================================================== #
# 2d. the prose, graded rather than asserted
# =========================================================================== #
def test_the_sentence_names_the_real_sensor_and_quotes_the_real_reading() -> None:
    """Graded, not label-asserted. A finding can carry the right severity and still say
    nothing a human can act on, and a label-only test passes happily while the product
    is useless."""
    findings = triage_reading(reading("water-level", 1.4, sensor_id="alkali-flat-water", location="Alkali Flat"))
    summary = findings[0].summary
    assert "alkali-flat-water" in summary
    assert "1.4" in summary
    assert "Alkali Flat" in summary
    assert "2" in summary, "the threshold it crossed should be in the sentence"
    assert "—" not in summary, "no em dashes, anywhere, ever"


def test_a_gate_sentence_reads_as_open_not_as_true() -> None:
    findings = triage_reading(reading("gate", True, sensor_id="coyote-draw-gate", location="Coyote Draw"))
    assert "reads open" in findings[0].summary
    assert "True" not in findings[0].summary


# =========================================================================== #
# 2e. the sweep view
# =========================================================================== #
def test_sweep_orders_critical_first_and_is_stable() -> None:
    readings = [
        reading("water-level", 17.9, sensor_id="a-fine-tank"),
        reading("fuel-level", 18.0, sensor_id="home-place-diesel"),
        reading("water-level", 1.4, sensor_id="alkali-flat-water"),
        reading("fence-voltage", 0.4, sensor_id="east-allotment-fence"),
    ]
    findings = triage_sweep(readings)
    assert [f.severity for f in findings] == ["critical", "critical", "warning"]
    assert [f.subject_id for f in findings[:2]] == ["alkali-flat-water", "east-allotment-fence"]
    assert triage_sweep(readings) == findings, "the same sweep must triage identically twice"


def test_every_category_triage_can_emit_is_registered() -> None:
    """The routing table in `src/agent/agent.py` is tested against `ALL_CATEGORIES`, so this
    set has to be complete or a new band produces incidents nobody owns."""
    emitted = {f.category for row in TRUTH_TABLE if row[3] for f in triage_reading(reading(row[0], row[1]))}
    assert emitted <= ALL_CATEGORIES
    assert {CATEGORY_OFFLINE, CATEGORY_DEGRADED, CATEGORY_FAULT, CATEGORY_UNKNOWN_TYPE} <= ALL_CATEGORIES


# =========================================================================== #
# 3. the evidence packet
# =========================================================================== #
# What these rails protect: the packet is the whole cheap-path argument, so a packet that
# quietly loses a piece produces a work order that reads complete and grounds nothing.
# Three of them exist because the live packet failed exactly that way before it was printed.
def _incident(**over: object) -> Incident:
    base: dict[str, object] = {
        "key": "alkali-flat-water:water_low",
        "subject_id": "alkali-flat-water",
        "subject_type": "water-level",
        "location": "Alkali Flat",
        "category": "water_low",
        "severity": "critical",
        "status": "opened",
        "summary": "stock-tank level at Alkali Flat is 1.9 gal, below the critical line of 2 gal",
        "last_value": "1.9 gal",
        "unit": " gal",
        "threshold": 2.0,
        "first_seen_at": datetime(2026, 9, 10, 14, 0, tzinfo=UTC),
        "last_seen_at": datetime(2026, 9, 10, 14, 0, tzinfo=UTC),
    }
    return Incident(**(base | over))  # type: ignore[arg-type]


ROSTER = PastureRoster(
    pastures=(
        PastureContext(pasture_id="alkali-flat", name="Alkali Flat", acreage=2400, fence_type="barbed-wire", status="open", head_count=111),
        PastureContext(pasture_id="east-allotment", name="East BLM Allotment", acreage=8800, fence_type="barbed-wire", status="open", head_count=240),
    )
)


def test_a_location_the_map_spells_with_its_id_still_finds_its_pasture() -> None:
    """The bug the first printed packet had. `GET /sensors/:id` says `Alkali Flat` and the
    ranch map says `Alkali Flat (alkali-flat)`; naive slugging turns the second into
    `alkali-flat-alkali-flat`, matches nothing, and reports no cattle on ground with 111 head.
    """
    assert slugify("Alkali Flat") == "alkali-flat"
    assert slugify("Alkali Flat (alkali-flat)") == "alkali-flat"
    assert ROSTER.for_location("Alkali Flat (alkali-flat)")[0] is ROSTER.pastures[0]


def test_a_pasture_is_matched_by_id_not_by_display_name() -> None:
    """`East Allotment` the sensor location is `East BLM Allotment` the pasture. A name match
    drops it and says no cattle are there, which is the most dangerous way to be wrong."""
    found, note = ROSTER.for_location("East Allotment")
    assert found is not None and found.head_count == 240
    assert note == ""


def test_a_site_that_is_not_grazing_ground_says_so_rather_than_reading_as_empty() -> None:
    found, note = ROSTER.for_location("Home Place")
    assert found is None
    assert "site rather than grazing ground" in note, "an absence has to be a sentence; a silent None becomes 'zero head' in a work order"


def test_a_missing_roster_is_stated_not_silently_zero() -> None:
    found, note = PastureRoster(error="ConnectError").for_location("Alkali Flat")
    assert found is None and "unavailable" in note


def test_a_gate_reading_stays_boolean_through_history_too() -> None:
    """Same trap as the sweep: `isinstance(True, int)` is True, so a number check first turns
    every gate reading in the series into the number 1."""
    points = parse_history({"data": [{"value": True, "recordedAt": FROZEN_TS}, {"value": 3.5, "recordedAt": FROZEN_TS}, {"value": None, "recordedAt": FROZEN_TS}]})
    assert [p.value for p in points] == [True, 3.5, None]
    assert points[0].value is True


def test_siblings_are_this_ticks_readings_at_this_location_and_never_the_sensor_itself() -> None:
    readings = [
        reading("water-level", 1.9, sensor_id="alkali-flat-water"),
        reading("water-level", 16.7, sensor_id="alkali-flat-water-2"),
        reading("battery-level", 82.5, sensor_id="alkali-flat-battery"),
        reading("water-level", 40.0, sensor_id="home-place-water", location="Home Place"),
    ]
    siblings = siblings_for("Alkali Flat", "alkali-flat-water", readings)
    assert [s.sensor_id for s in siblings] == ["alkali-flat-battery", "alkali-flat-water-2"]
    assert "16.7 gal" in siblings[1].render()


def test_every_category_has_an_sop_file_that_exists() -> None:
    """The SOPs are derived from `docs/sweetwater-ranch.md` and nothing else may source them.
    A missing file is silent in the packet and turns rule 4 of the brief into an invitation
    to invent a rule id."""
    for category in SOP_FOR_CATEGORY:
        name, text = load_sop(category)
        assert name and text.strip(), f"{category} maps to {SOP_FOR_CATEGORY[category]} and it did not load"


def test_no_triage_category_reaches_a_model_without_a_rule_to_cite() -> None:
    """All 18 as of M3. Before it, `evidence.py` logged `packets_without_sop` for eleven of
    them, which made rail 4 of the brief vacuous for two agents of four: an agent told to cite
    a rule from a packet carrying no rules either invents one or cites nothing, and only one of
    those is detectable. From here a `packets_without_sop` warning means a NEW category."""
    assert set(SOP_FOR_CATEGORY) == set(ALL_CATEGORIES), f"no SOP for {sorted(set(ALL_CATEGORIES) - set(SOP_FOR_CATEGORY))}"


def test_a_rule_id_belongs_to_exactly_one_sop_file() -> None:
    """`workers.citable_rules` reads the ids out of the text the packet carried, so a duplicated
    id would be citable from a file that does not contain the rule the crew then goes looking
    for. Six files with six prefixes is what keeps that impossible."""
    seen: dict[str, str] = {}
    for filename in sorted(set(SOP_FOR_CATEGORY.values())):
        for rule in citable_rules((KNOWLEDGE_BASE / filename).read_text(encoding="utf-8")):
            assert rule not in seen, f"{rule} appears in both {seen.get(rule)} and {filename}"
            seen[rule] = filename
    assert len(seen) >= 18, f"only {len(seen)} citable rules across six files, which is thinner than the categories they cover"


def test_every_sop_says_where_its_rules_came_from_and_carries_no_em_dash() -> None:
    """Two conventions that only exist in prose and so have no other way to be enforced. The
    provenance line is the anti-invention rule pointed at the file itself, and the house style
    reaches the SOPs because a model reads them verbatim."""
    for filename in sorted(set(SOP_FOR_CATEGORY.values())):
        text = (KNOWLEDGE_BASE / filename).read_text(encoding="utf-8")
        assert "docs/sweetwater-ranch.md" in text, f"{filename} does not say what sourced it"
        assert "—" not in text, f"{filename} carries an em dash"


def test_a_category_that_does_not_exist_returns_nothing_rather_than_guessing_a_filename() -> None:
    assert load_sop("stampede") == ("", "")


def test_the_packet_warns_that_the_history_series_is_not_the_current_reading() -> None:
    """The seam the first printed packet exposed: triage judged 1.9 gal while the newest
    history point read 0.8 gal at a LATER timestamp, because `GET /sensors/:id/readings`
    synthesizes its series independently of `GET /sensors/:id`. Unlabeled, a model quotes the
    top of the list as 'now' and the work order carries a number no human ever saw."""
    packet = EvidencePacket(incident=_incident(), history=(HistoryPoint(recorded_at=FROZEN_TS, value=0.8),), sop_name="water.md", sop_text="## WATER-01 - haul today")
    page = packet.render()
    assert "does NOT contain it" in page
    assert "authoritative current value is the triaged reading" in page
    assert "1.9 gal" in page and "0.8 gal" in page


def test_an_absent_piece_of_the_packet_is_a_sentence_not_a_gap() -> None:
    page = EvidencePacket(incident=_incident(), history_note="HTTP 503").render()
    assert "none available: HTTP 503" in page
    assert "only one of them" not in page  # no SOP was loaded, so no rule text leaked in
    assert "no SOP exists for this category yet" in page
    assert "this sensor is the only one at this location" in page


async def test_assembling_nothing_costs_nothing() -> None:
    """A calm tick must not pay for a roster call it has no packet to put in."""
    with respx.mock(assert_all_called=False) as mock:
        assert await assemble([]) == ()
        assert not mock.calls


# --- M10 (#12): the conditions block, the feed page's missing fact ----------------------------- #
# What these protect: FEED-02 asks whether weather turns a low bin urgent and FEED-01 asks about the
# yard fuel, and neither fact was on the page, so the local judge said `insufficient_information` on
# purpose and Opus rewrote it. The block puts the nearest reading of each on the page from the same
# sweep at zero HTTP. A water page never carries it, and nothing on it can name a sensor the sweep
# did not read.
def _feed_incident(**over: object) -> Incident:
    return _incident(key="feed-bin-12:feed_low", subject_id="feed-bin-12", subject_type="feed-bin-weight", location="Feed Room", category="feed_low", severity="warning", summary="feed-bin weight at Feed Room is 389.3 lbs, below the warning line of 500 lbs", last_value="389.3 lbs", unit=" lbs", threshold=500.0, **over)


def _grid(**points: tuple[float, float]) -> RanchMap:
    return RanchMap(sensors=tuple(SensorRef(sensor_id=sid, sensor_type="x", location="x", coordinates={"x": x, "y": y}) for sid, (x, y) in points.items()))


CONDITION_SWEEP = [
    reading("feed-bin-weight", 389.3, sensor_id="feed-bin-12", location="Feed Room"),
    reading("wind-speed", 12.9, sensor_id="met-tower-wind", location="Wind Met Tower"),
    reading("wind-speed", 7.5, sensor_id="home-place-wind", location="Home Place"),
    reading("temperature", 4.0, sensor_id="home-place-temp", location="Home Place"),
    reading("temperature", 48.2, sensor_id="feed-room-temp", location="Feed Room"),
    reading("fuel-level", 4.9, sensor_id="home-place-diesel", location="Home Place"),
    reading("fuel-level", None, sensor_id="shop-propane", status="offline", location="Feed Room"),
]


def test_the_feed_page_carries_the_nearest_wind_temperature_snow_and_fuel_from_the_same_sweep() -> None:
    """Nearest by map distance, same location winning outright, a live value beating a dark sensor
    of the same type, and an absent type written as a sentence. `snow-depth` has no reading in this
    sweep, which is exactly the shape of a September ranch."""
    grid = _grid(**{"feed-bin-12": (10.0, 10.0), "met-tower-wind": (90.0, 90.0), "home-place-wind": (12.0, 10.0), "home-place-temp": (12.0, 10.0), "feed-room-temp": (10.0, 11.0), "home-place-diesel": (12.0, 10.0)})
    found, missing = conditions_for(_feed_incident(), CONDITION_SWEEP, grid, sensor_types=CONDITIONS_FOR_SOP["feed.md"])
    assert [c.sensor_id for c in found] == ["home-place-wind", "feed-room-temp", "home-place-diesel"], "the near wind sensor, the temperature in the room itself, and the diesel that has a value over the propane that is dark"
    assert missing == ("snow-depth",)
    page = EvidencePacket(incident=_feed_incident(), sop_name="feed.md", sop_text="## FEED-02 - schedule against the forecast", conditions=found, conditions_missing=missing).render()
    assert "## Conditions now, read in this same sweep" in page
    assert "wind speed (home-place-wind, at Home Place): 7.5 mph" in page
    assert "air temperature (feed-room-temp, here): 48.2 F" in page
    assert "bulk fuel level (home-place-diesel, at Home Place): 4.9%" in page
    assert "snow depth: no snow-depth sensor answered this sweep" in page
    assert "met-tower-wind" not in page and "shop-propane" not in page, "the far wind sensor and the dark propane tank were candidates, not the answer"
    assert "None of them is a forecast" in page


def test_the_conditions_block_falls_back_to_location_then_id_without_a_map_and_never_names_the_incident_sensor() -> None:
    found, missing = conditions_for(_feed_incident(), CONDITION_SWEEP, None, sensor_types=("temperature", "wind-speed", "feed-bin-weight"))
    assert [c.sensor_id for c in found] == ["feed-room-temp", "home-place-wind"], "same location first; then, unmapped, the lowest id among the wind sensors"
    assert missing == ("feed-bin-weight",), "the incident's own sensor is never its own condition; with no other bin in the sweep the type is absent"
    assert found[0].same_location and not found[1].same_location


def test_a_water_page_carries_no_conditions_block_and_an_empty_ask_renders_nothing() -> None:
    """The block is the feed SOP's, keyed by file in `CONDITIONS_FOR_SOP`. A water incident's page is
    byte-for-byte what it was before M10, and a test that read the feed page's phrasing into a water
    order would be the first sign the key had drifted to categories."""
    assert conditions_for(_incident(), CONDITION_SWEEP, None, sensor_types=CONDITIONS_FOR_SOP.get("water.md", ())) == ((), ())
    page = EvidencePacket(incident=_incident(), sop_name="water.md", sop_text="## WATER-01 - haul today").render()
    assert "Conditions now" not in page and "forecast" not in page
    assert set(CONDITIONS_FOR_SOP) == {"feed.md"}, "a second file joining the ask is a deliberate edit to this set and to this test"


# =========================================================================== #
# 3b. the herd sweep (M7A): three rules inherited from sensors.py, then the animal truth table,
#     then the cow's packet. All against fake Farm and Care hosts; the wire facts these encode
#     (per-animal observations, honoured status filter, roster with animalIds) are in docs/state.md
# =========================================================================== #
HERD_NOW = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)  # its own name: the chaos suite below rebinds NOW at import time
OLD_ISO = "2026-08-09T09:00:00.000Z"  # cow-0777's real mobility note is this old; outside the window


def animal(animal_id: str = "cow-0901", *, status: str = "deceased", pasture: str = "east-allotment", species: str = "cow") -> dict[str, object]:
    return {"id": animal_id, "name": f"SW-{animal_id[-4:]}", "species": species, "sex": "female", "status": status, "pastureId": pasture, "shelterId": None, "createdAt": "2026-08-10T14:00:00.000Z", "updatedAt": "2026-09-10T13:58:00.000Z"}


def obs(animal_id: str = "cow-0901", *, severity: str = "high", kind: str = "injury", at: str = "2026-09-10T13:58:00.000Z", note: str = "Found down at first light, throat and hindquarter torn, tracks and scat consistent with coyote.") -> dict[str, object]:
    return {"id": f"obs-{animal_id}-{at}", "animalId": animal_id, "type": kind, "severity": severity, "note": note, "observedAt": at, "createdAt": at}


def task(animal_id: str = "cow-0777", *, due: str = "2026-08-10T13:00:00.000Z", status: str = "pending", title: str = "Recheck down cow, call vet if not up by AM") -> dict[str, object]:
    return {"id": f"task-{animal_id}-0001", "animalId": animal_id, "title": title, "dueAt": due, "status": status, "notes": "", "createdAt": "2026-08-10T14:00:00.000Z"}


#: The wave the fakes see: `HERD_PAGE_CONCURRENCY` capped by the fixture's `sweep_concurrency=4`.
WAVE = min(HERD_PAGE_CONCURRENCY, 4)

HERD_ROSTER = {"data": [{"id": "east-allotment", "name": "East BLM Allotment", "acreage": 8800, "fenceType": "barbed-wire", "status": "open", "animalIds": ["cow-0901", "cow-0902", "cow-0903"]}, {"id": "home-place", "name": "Home Place", "acreage": 40, "fenceType": "pipe", "status": "open", "animalIds": ["cow-0777", "horse-01"]}], "meta": {"count": 2, "limit": 50, "offset": 0}}


def page(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"data": rows, "meta": {"count": len(rows), "limit": 500, "offset": 0}}


def serve_farm_and_care(mock: respx.MockRouter, *, non_active: dict[str, list[dict[str, object]]] | None = None, tasks: list[dict[str, object]] | None = None, observations: dict[str, list[dict[str, object]]] | None = None, records: dict[str, dict[str, object]] | None = None, roster: dict[str, object] | None = None, pages: dict[int, list[dict[str, object]]] | None = None) -> None:
    """The Farm and Care routes the herd sweep reads. The herd **list** is served as pages at
    `offset=0, 100, ...`: page 0 carries an active row for every roster id plus every `non_active`
    row and every extra `records` row, later pages are empty, unless `pages` says otherwise."""
    the_roster = roster if roster is not None else HERD_ROSTER
    mock.get(f"{FARM}/pastures").respond(200, json=the_roster)
    if pages is None:
        given = [row for rows in (non_active or {}).values() for row in rows] + list((records or {}).values())
        given_ids = {str(r["id"]) for r in given}
        by_pasture = {str(p["id"]): list(p.get("animalIds") or []) for p in the_roster["data"]}  # type: ignore[index, union-attr]
        actives = [animal(aid, status="active", pasture=pid, species="horse" if aid.startswith("horse") else "cow") for pid, ids in by_pasture.items() for aid in ids if aid not in given_ids]
        pages = {0: actives + given}
    for i in range(HERD_MAX_PAGES):
        mock.get(f"{FARM}/animals", params__contains={"offset": str(i * HERD_PAGE)}).respond(200, json=page(pages.get(i * HERD_PAGE, [])))
    mock.get(f"{CARE}/care-tasks").respond(200, json=page(tasks or []))
    for animal_id, rows in (observations or {}).items():
        mock.get(f"{CARE}/animals/{animal_id}/observations").respond(200, json=page(rows))
    mock.route(method="GET", host="care.test", path__regex=r"^/animals/[^/]+/observations$").respond(200, json=page([]))


# --- rule 1: errors are returned as data ------------------------------------------------------- #
async def test_a_failed_read_for_one_animal_is_data_and_only_that_animal_stops_answering() -> None:
    with respx.mock(assert_all_called=False) as mock:
        # registered first: respx matches routes in registration order, and the helper ends with a catch-all
        mock.get(f"{CARE}/animals/cow-0902/observations").respond(500, json={"error": "boom"})
        serve_farm_and_care(mock, non_active={"deceased": [animal("cow-0901"), animal("cow-0902")]})
        result = await sweep_herd()
    assert result.ok and result.failure == ""
    assert [e.subject for e in result.errors] == ["cow-0902"] and result.errors[0].category == "http_error" and result.errors[0].retriable
    assert "cow-0901" in result.answered and "cow-0903" in result.answered, "the list answered for everyone on it"
    assert "cow-0902" not in result.answered, "her own read failed, so she did not"
    assert {a.animal_id for a in result.animals} == {"cow-0901", "cow-0902"}, "the finding is still made from the status the list carried"


async def test_a_timeout_and_non_json_are_classified_never_raised() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{CARE}/animals/cow-0901/observations").mock(side_effect=httpx.ReadTimeout("timed out"))
        mock.get(f"{CARE}/animals/cow-0902/observations").respond(200, text="<html>gateway</html>")
        serve_farm_and_care(mock, non_active={"deceased": [animal("cow-0901"), animal("cow-0902")]})
        result = await sweep_herd()
    assert sorted((e.subject, e.category) for e in result.errors) == [("cow-0901", "timeout"), ("cow-0902", "bad_response")]
    assert result.ok and result.answered.isdisjoint({"cow-0901", "cow-0902"})


# --- rule 2: an empty herd catalog fails the stage --------------------------------------------- #
async def test_an_empty_herd_catalog_fails_the_stage_rather_than_reading_as_an_empty_herd() -> None:
    """200 with a perfect empty envelope is the one failure no retry layer can see, and an empty
    herd downstream reads exactly like every cow being fine. So it is a failed stage."""
    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, pages={})
        result = await sweep_herd()
    assert not result.ok and result.failure.startswith("farm:") and "empty_catalog" in result.failure
    assert result.answered == frozenset(), "nothing answered, so nothing resolves"


async def test_a_herd_that_fills_every_page_was_not_read_whole_and_fails_the_stage() -> None:
    """20 pages of 100 is the ceiling. A last page that comes back full means animals beyond it were
    never read, and "not seen as deceased" is not "not deceased" for them, so nobody answered."""
    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, pages={i * HERD_PAGE: [animal(f"cow-{i * HERD_PAGE + n:04d}", status="active") for n in range(HERD_PAGE)] for i in range(HERD_MAX_PAGES)})
        result = await sweep_herd()
    assert not result.ok and "ceiling" in result.failure and result.answered == frozenset()
    assert result.requests == 1 + HERD_MAX_PAGES + 1, "every page was needed, in waves"


async def test_paging_goes_out_in_waves_and_stops_at_the_first_short_page() -> None:
    """The Farm API 500s at 12 pages in flight and is clean at 6, measured. So the pages go out six at
    a time, and the wave after a short page is never sent: a 1,195-head herd is two waves, not twenty pages."""
    def full(offset: int) -> list[dict[str, object]]:
        return [animal(f"cow-{offset + n:04d}", status="active") for n in range(HERD_PAGE)]

    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, pages={**{o: full(o) for o in range(0, 700, 100)}, 700: [animal("cow-0777", status="active")]})
        result = await sweep_herd()
        offsets = sorted(int(c.request.url.params["offset"]) for c in mock.calls if c.request.url.path == "/animals")
    assert result.ok and len(result.answered) == 7 * HERD_PAGE + 1
    assert offsets == [i * HERD_PAGE for i in range(WAVE * 2)], "two waves; the third wave was never sent"


async def test_a_farm_page_failing_means_no_animal_answered_but_the_dead_one_is_still_reported() -> None:
    """"Not on the list as deceased" only means "not deceased" if the whole list came back. One page
    down and no animal can be vouched for; the animals the other pages carried are still findings,
    because a dead cow is a dead cow whichever page was sick."""
    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, non_active={"deceased": [animal("cow-0901")]})
        mock.get(f"{FARM}/animals", params__contains={"offset": "300"}).respond(503, json={"error": {"category": "upstream"}})  # an identical pattern replaces the helper's route
        result = await sweep_herd()
    assert not result.ok and "page:300" in result.failure
    assert result.answered == frozenset() and [a.animal_id for a in result.animals] == ["cow-0901"]


async def test_the_roster_is_context_not_the_catalog() -> None:
    """The first live kill showed the Farm API nulls a dead cow's pasture, so she leaves every roster.
    The list vouches for her; the roster only decorates her packet, and a roster outage is a note."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FARM}/pastures").respond(503, json={"error": {"category": "upstream"}})
        serve_farm_and_care(mock, non_active={"deceased": [animal("cow-0905", pasture="")]}, roster={"data": [{"id": "x", "animalIds": ["cow-0001"]}], "meta": {"count": 1}})
        mock.get(f"{FARM}/pastures").respond(503, json={"error": {"category": "upstream"}})
        result = await sweep_herd()
    assert result.ok and "cow-0905" in result.answered and result.roster.error == "HTTP 503"
    assert [e.subject for e in result.errors] == ["roster"]


# --- rule 3: the stage returns the subjects that answered --------------------------------------- #
async def test_a_care_api_outage_resolves_no_animal() -> None:
    """The Farm reads all answered and every cow is active, which looks exactly like a healthy herd.
    It is not evidence of one: an `ongoing` care_overdue or observation_high incident can only be
    closed by a task list and an observation list that answered. `answered` is empty."""
    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, non_active={"deceased": [animal("cow-0901")]})
        mock.get(f"{CARE}/care-tasks").respond(503, json={"error": {"category": "upstream"}})
        result = await sweep_herd()
    assert not result.ok and result.failure.startswith("care:")
    assert result.answered == frozenset()
    assert [a.animal_id for a in result.animals] == ["cow-0901"], "the deceased finding still opens; only resolution is withheld"
    assert result.observations == {}, "no observation was read for anyone, so the packet will state that absence"


async def test_a_healthy_herd_answers_for_the_whole_list_and_reads_observations_only_for_the_changed_set() -> None:
    """The request bill is the design. 1,195 head on the live ranch and observations list per animal,
    so the sweep reads them only for animals whose state changed: the non-active set plus the animals
    on a pending care task. Everything else answered through the list, and no per-animal Farm read."""
    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, non_active={"deceased": [animal("cow-0901")], "sold": [animal("cow-0903", status="sold")]}, tasks=[task("cow-0777")], observations={"cow-0901": [obs()], "cow-0777": [obs("cow-0777", kind="mobility", at=OLD_ISO)]})
        result = await sweep_herd()
        observation_calls = [c for c in mock.calls if c.request.url.path.endswith("/observations")]
        farm_calls = [c.request.url.path for c in mock.calls if c.request.url.host == "farm.test"]
    assert result.ok
    assert result.answered == frozenset({"cow-0901", "cow-0902", "cow-0903", "cow-0777", "horse-01"})
    assert sorted(c.request.url.path for c in observation_calls) == ["/animals/cow-0777/observations", "/animals/cow-0901/observations", "/animals/cow-0903/observations"], "the changed set and nobody else"
    assert set(farm_calls) == {"/pastures", "/animals"} and farm_calls.count("/animals") == WAVE, "the roster and one wave of pages (page 0 was short), and never a per-animal Farm read"
    assert result.requests == 1 + WAVE + 1 + 3
    assert {a.animal_id: a.status for a in result.animals} == {"cow-0901": "deceased", "cow-0903": "sold", "cow-0777": "active"}
    assert result.herd_mates("east-allotment", excluding="cow-0901") == (result.record("cow-0903"),)


async def test_a_watched_animal_off_every_roster_answers_through_the_list_and_one_off_the_list_does_not() -> None:
    """Found on the first live kill: the Farm API nulls `pastureId` on a deceased PATCH, so the restored
    cow is active and on no roster. The list still carries her and that is the vouching; her record is
    in the changed set so her packet has one. A watched animal that is not on the list is gone, not fine."""
    with respx.mock(assert_all_called=False) as mock:
        serve_farm_and_care(mock, records={"cow-0905": animal("cow-0905", status="active", pasture="")})
        result = await sweep_herd(watch=("cow-0905", "cow-0906"))
    assert result.ok
    assert "cow-0905" in result.answered and result.record("cow-0905") is not None, "on the list, so she answered clean"
    assert "cow-0906" not in result.answered and [(e.subject, e.category) for e in result.errors] == [("cow-0906", "not_in_herd")], "gone from the ranch is not the same as fine"
    assert triage_herd(result, now=HERD_NOW) == []


# --- the animal truth table, one row per category --------------------------------------------- #
def _herd(*records: AnimalRecord, observations: dict[str, tuple[Observation, ...]] | None = None, tasks: tuple[CareTask, ...] = ()) -> HerdSweepResult:
    return HerdSweepResult(animals=tuple(records), observations=observations or {}, care_tasks=tasks, answered=frozenset(r.animal_id for r in records))


def _rec(animal_id: str = "cow-0901", *, status: str = "deceased", pasture: str = "east-allotment") -> AnimalRecord:
    return AnimalRecord(animal_id=animal_id, name=f"SW-{animal_id[-4:]}", species="cow", sex="female", status=status, pasture_id=pasture, shelter_id="", updated_at="2026-09-10T13:58:00.000Z")


def _obs(animal_id: str = "cow-0901", *, severity: str = "high", kind: str = "injury", at: str = "2026-09-10T13:58:00.000Z") -> Observation:
    return Observation(observation_id=f"o-{at}", animal_id=animal_id, type=kind, severity=severity, note="Found down at first light.", observed_at=at)


def _task(animal_id: str = "cow-0777", *, due: str = "2026-08-10T13:00:00.000Z", status: str = "pending") -> CareTask:
    return CareTask(task_id=f"task-{animal_id}", animal_id=animal_id, title="Recheck down cow", due_at=due, status=status, notes="")


ANIMAL_TRUTH_TABLE = [
    ("deceased", _herd(_rec(status="deceased")), [(CATEGORY_DECEASED, "critical")]),
    ("inactive is unaccounted for", _herd(_rec(status="inactive")), [(CATEGORY_INACTIVE, "warning")]),
    ("sold is a ranch running normally", _herd(_rec(status="sold")), []),
    ("active with nothing else is nothing", _herd(_rec(status="active")), []),
    ("high injury inside the window is critical", _herd(_rec(status="active"), observations={"cow-0901": (_obs(kind="injury"),)}), [(CATEGORY_OBSERVATION_HIGH, "critical")]),
    ("high mobility inside the window is critical", _herd(_rec(status="active"), observations={"cow-0901": (_obs(kind="mobility"),)}), [(CATEGORY_OBSERVATION_HIGH, "critical")]),
    ("high behavior inside the window is a warning", _herd(_rec(status="active"), observations={"cow-0901": (_obs(kind="behavior"),)}), [(CATEGORY_OBSERVATION_HIGH, "warning")]),
    ("medium is not a finding", _herd(_rec(status="active"), observations={"cow-0901": (_obs(severity="medium"),)}), []),
    ("high outside the 24h window is history, not a finding", _herd(_rec(status="active"), observations={"cow-0901": (_obs(at=OLD_ISO),)}), []),
    ("a high note on a dead cow rides in the deceased packet, not a second incident", _herd(_rec(status="deceased"), observations={"cow-0901": (_obs(),)}), [(CATEGORY_DECEASED, "critical")]),
    ("the newest of several notes decides", _herd(_rec(status="active"), observations={"cow-0901": (_obs(kind="behavior", at="2026-09-10T13:58:00.000Z"), _obs(kind="injury", at="2026-09-10T09:00:00.000Z"))}), [(CATEGORY_OBSERVATION_HIGH, "warning")]),
    ("an overdue pending task is a warning", _herd(_rec("cow-0777", status="active", pasture="home-place"), tasks=(_task(),)), [(CATEGORY_CARE_OVERDUE, "warning")]),
    ("a task not yet due is nothing", _herd(_rec("cow-0777", status="active"), tasks=(_task(due="2026-10-01T09:00:00.000Z"),)), []),
    ("a completed task past its date is nothing", _herd(_rec("cow-0777", status="active"), tasks=(_task(status="completed"),)), []),
    ("two overdue tasks on one animal are one finding", _herd(_rec("cow-0777", status="active"), tasks=(_task(), CareTask(task_id="task-2", animal_id="cow-0777", title="Second", due_at="2026-09-01T09:00:00.000Z", status="pending", notes=""))), [(CATEGORY_CARE_OVERDUE, "warning")]),
    ("a task on an animal whose record failed still opens, with its pasture unknown", _herd(tasks=(_task(),)), [(CATEGORY_CARE_OVERDUE, "warning")]),
    ("a dead cow with an overdue task is two different jobs", _herd(_rec(status="deceased"), tasks=(_task("cow-0901"),)), [(CATEGORY_DECEASED, "critical"), (CATEGORY_CARE_OVERDUE, "warning")]),
]


@pytest.mark.parametrize(("label", "herd", "expected"), ANIMAL_TRUTH_TABLE, ids=[row[0] for row in ANIMAL_TRUTH_TABLE])
def test_animal_truth_table(label: str, herd: HerdSweepResult, expected: list[tuple[str, str]]) -> None:
    findings = triage_herd(herd, now=HERD_NOW)
    assert [(f.category, f.severity) for f in findings] == expected, label
    for f in findings:
        assert f.subject_type == "animal" and f.key.endswith(f":{f.category}") and f.subject_id in f.summary


def test_deceased_is_critical_on_purpose_and_the_window_is_a_day() -> None:
    """Under M7's predicate critical escalates to Tier 2, which is where a dead cow belongs. Do not
    tune that down to save a call. And the window is what keeps the ranch's history (cow-0777's
    August mobility note) from opening an incident on the first sweep, forever, because the debounce
    does nothing against an observation that is stable across sweeps."""
    assert triage_herd(_herd(_rec(status="deceased")), now=HERD_NOW)[0].severity == "critical"
    assert OBSERVATION_WINDOW == timedelta(hours=24)
    inside = triage_herd(_herd(_rec(status="active"), observations={"cow-0901": (_obs(at="2026-09-09T14:00:00.000Z"),)}), now=HERD_NOW)
    outside = triage_herd(_herd(_rec(status="active"), observations={"cow-0901": (_obs(at="2026-09-09T13:59:59.000Z"),)}), now=HERD_NOW)
    assert len(inside) == 1 and outside == []


def test_the_care_overdue_sentence_names_the_task_the_animal_and_the_days() -> None:
    [finding] = triage_herd(_herd(_rec("cow-0777", status="active", pasture="home-place"), tasks=(_task(),)), now=HERD_NOW)
    assert finding.location == "home-place" and finding.observed_at == "2026-08-10T13:00:00.000Z"
    assert "task-cow-0777" in finding.summary and "cow cow-0777" in finding.summary and "31 days overdue" in finding.summary and '"Recheck down cow"' in finding.summary


def test_an_unknown_animal_status_opens_nothing_and_warns_once() -> None:
    """Structurally unreachable (the sweep reads by status), kept explicit because a fall-through
    that opens `inactive` on a status it does not understand would be a lie about the animal."""
    from src.tools.triage import reset_warn_once

    reset_warn_once()
    with capture_logs() as logs:
        assert triage_herd(_herd(_rec(status="quarantined")), now=HERD_NOW) == []
        assert triage_herd(_herd(_rec(status="quarantined")), now=HERD_NOW) == []
    assert [e["event"] for e in logs if e["event"] == "unknown_animal_status"] == ["unknown_animal_status"]


def test_the_herd_findings_rank_into_one_list_with_the_sensor_findings() -> None:
    herd = _herd(_rec(status="deceased"), _rec("cow-0777", status="active"), tasks=(_task(),))
    findings = triage_sweep([reading("water-level", 1.4, sensor_id="alkali-flat-water"), reading("fuel-level", 18.0, sensor_id="home-place-diesel")], herd=herd, now=HERD_NOW)
    assert [(f.subject_id, f.severity) for f in findings] == [("alkali-flat-water", "critical"), ("cow-0901", "critical"), ("cow-0777", "warning"), ("home-place-diesel", "warning")]


def test_every_animal_category_is_registered_and_has_an_sop() -> None:
    assert ANIMAL_CATEGORIES <= ALL_CATEGORIES
    assert {SOP_FOR_CATEGORY[c] for c in ANIMAL_CATEGORIES} == {"herd.md"}
    assert parse_timestamp("2026-09-10T13:58:00.000Z") == datetime(2026, 9, 10, 13, 58, tzinfo=UTC) and parse_timestamp("yesterday") is None


# --- the cow's packet: the record, its pasture, its notes, its tasks, its herd-mates, and NO reading -- #
def _animal_incident(**over: object) -> Incident:
    base: dict[str, object] = {
        "key": "cow-0901:deceased",
        "subject_id": "cow-0901",
        "subject_type": "animal",
        "location": "east-allotment",
        "category": "deceased",
        "severity": "critical",
        "status": "opened",
        "summary": "east-allotment: cow cow-0901, tag SW-0901 is recorded deceased on the Farm API.",
        "first_seen_at": datetime(2026, 9, 10, 14, 0, tzinfo=UTC),
        "last_seen_at": datetime(2026, 9, 10, 14, 0, tzinfo=UTC),
        "owner": "herd_health",
    }
    return Incident(**(base | over))  # type: ignore[arg-type]


async def test_a_cows_packet_carries_no_sensor_reading_and_costs_no_http() -> None:
    """The one line worth defending in the whole design. The pasture's tank is on the sweep, at the
    same location, at 1.4 gal, and `evidence.py` already documents that a code-assembled packet may
    cross an allowlist. The cow's packet still may not carry it: herd_health sees the dead cow,
    water_feed sees the dry tank, and only the supervisor may fuse them. No route is mocked on the
    sensor host, so any history or sibling read for the cow would raise here."""
    herd = HerdSweepResult(
        roster=PastureRoster(pastures=(PastureContext(pasture_id="east-allotment", name="East BLM Allotment", acreage=8800, fence_type="barbed-wire", status="open", head_count=111, animal_ids=tuple(f"cow-{i:04d}" for i in range(111))),)),
        animals=(_rec(status="deceased"), _rec("cow-0903", status="sold")),
        observations={"cow-0901": (_obs(), _obs(kind="general", severity="low", at="2026-08-05T09:00:00.000Z"))},
        care_tasks=(_task("cow-0901"),),
        answered=frozenset({"cow-0901", "cow-0903"}),
    )
    tank_here = reading("water-level", 1.4, sensor_id="east-allotment-water", location="east-allotment")
    with respx.mock(assert_all_called=False) as mock:
        [packet] = await assemble([_animal_incident()], readings=[tank_here], herd=herd)
        assert not mock.calls, "an animal packet is built from what the sweep already read"

    page = packet.render()
    assert packet.history == () and packet.siblings == () and packet.animal is not None
    assert "east-allotment-water" not in page and "1.4" not in page and "gal" not in page, "the tank stays in water_feed's packet"
    assert "cow-0901" in page and "tag SW-0901" in page and "status: deceased" in page
    assert "Found down at first light." in page and "[high] injury" in page, "the observation is quoted as written"
    assert "task-cow-0901" in page and "Recheck down cow" in page
    assert "111 head on it" in page, "the pasture from the roster the sweep already fetched"
    assert "cow cow-0903: status sold" in page, "herd-mates whose state changed this sweep"
    assert "No sensor reading is on this page by design" in page
    assert packet.sop_name == "herd.md" and "HERD-01" in packet.sop_text


async def test_a_cows_packet_states_every_absence() -> None:
    """A Care outage left no observations and the Farm record read failed: the page says both, so
    the model works around a stated gap instead of filling it."""
    herd = HerdSweepResult(animals=(), observations={}, care_tasks=(_task("cow-0901"),), errors=(HerdError(subject="cow-0901", category="http_error", message="HTTP 503"),), failure="care: care_tasks http_error HTTP 503")
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FARM}/pastures").respond(200, json=HERD_ROSTER)
        [packet] = await assemble([_animal_incident(category="care_overdue", key="cow-0901:care_overdue", severity="warning", location="")], herd=herd)
    page = packet.render()
    assert "record unavailable: HTTP 503" in page
    assert "the Care API read for this animal failed (HTTP 503)" in page
    assert "no pasture is recorded for this animal" in page
    assert packet.sop_name == "herd.md"


async def test_a_mixed_tick_reads_history_for_the_sensors_and_nothing_for_the_cow() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{FARM}/pastures").respond(200, json=HERD_ROSTER)
        mock.get(f"{BASE}/sensors/alkali-flat-water/readings").respond(200, json={"data": [{"value": 1.2, "recordedAt": FROZEN_TS}]})
        packets = await assemble([_incident(), _animal_incident()], herd=_herd(_rec(status="deceased")))
        paths = sorted(c.request.url.path for c in mock.calls)
    assert paths == ["/pastures", "/sensors/alkali-flat-water/readings"]
    assert [p.incident.is_animal for p in packets] == [False, True] and packets[0].history != () and packets[1].history == ()


# =========================================================================== #
# 4. the tool slices
# =========================================================================== #
#: Spelled out here rather than imported from `allowlists._SENSOR_READS`, so the rails
#: assert the fact (three agents share the sensor reads) instead of agreeing with whatever
#: the implementation happens to have grouped into a constant.
SENSOR_READS = frozenset({"list_sensors", "read_sensor", "get_sensor_readings"})



# What these rails protect: isolation enforced in CODE, not requested in a prompt. The
# counts are the spec, so a slice cannot grow by one tool without a deliberate edit to a
# number a human reads. The write rails protect the M3-to-M6 seam: the writes are declared
# so the counts are real, and withheld so nothing reaches the deployed Care API before
# `interrupt()` exists. Loosening any of these to make something else pass is the exact
# failure mode `tests/CLAUDE.md` is written against.
def test_every_agent_has_exactly_the_tools_it_should() -> None:
    """The counts are the spec. 7 / 6 / 5 / 5 / 0, writes included."""
    assert {agent: len(tools_for(agent)) for agent in SLICES} == {"water_feed": 7, "herd_health": 6, "infrastructure": 5, "compliance": 5, "chaos": 0}


def test_no_agent_names_a_tool_outside_its_set() -> None:
    """The same rail from the other direction: nothing invented, nothing undeclared."""
    for agent, slice_ in SLICES.items():
        assert slice_ <= DEPLOYED_TOOLS, f"{agent} names a tool the deployed server does not have: {sorted(slice_ - DEPLOYED_TOOLS)}"
    assert not is_allowed("herd_health", "read_sensor"), "herd_health cannot read a sensor; that is the line worth defending"
    assert not is_allowed("infrastructure", "consume_feed"), "nothing but water_feed touches feed"
    assert not is_allowed("compliance", "list_shelters")
    assert not is_allowed("chaos", "read_sensor"), "chaos has its own hands in tools/chaos.py, not a slice of the 19"


def test_the_slices_cover_exactly_the_five_agents() -> None:
    """`allowlists` cannot import `agent.py` without a cycle, so this rail is the sync."""
    assert set(SLICES) == set(AGENTS)


def test_an_unknown_agent_gets_nothing_and_says_so() -> None:
    """A permissive default is how a typo becomes a model holding all 19 tools."""
    with capture_logs() as logs:
        assert tools_for("water-feed") == frozenset()
    assert [entry["event"] for entry in logs] == ["no_slice_for_agent"]


def test_herd_health_owns_the_care_api_and_only_the_care_api() -> None:
    """Its idleness at M3 is a routing fact, not a missing slice. The slice is real."""
    assert tools_for("herd_health") & SENSOR_READS == frozenset()
    assert "create_observation" in tools_for("herd_health")


def test_three_agents_share_the_sensor_reads_on_purpose() -> None:
    """Not a leak. Carving this up would mean editing a frozen server."""
    sharing = {agent for agent, slice_ in SLICES.items() if SENSOR_READS <= slice_}
    assert sharing == {"water_feed", "infrastructure", "compliance"}


# --- the M3-to-M6 seam: declared, and withheld -------------------------------------- #
def test_every_write_tool_is_declared_in_exactly_one_slice_or_none() -> None:
    """Without this the withhold is vacuous: a write nobody declares is a write nobody withholds."""
    for tool in WRITE_TOOLS:
        owners = [agent for agent, slice_ in SLICES.items() if tool in slice_]
        assert len(owners) <= 1, f"{tool} is a write declared in more than one slice: {owners}"
    assert {tool for tool in WRITE_TOOLS if any(tool in s for s in SLICES.values())} == {"consume_feed", "restock_feed", "create_observation", "update_care_task"}


def test_the_four_placement_tools_belong_to_nobody() -> None:
    """Deployed, write-capable, and in no slice. Moving one in has to be deliberate."""
    assert UNASSIGNED_TOOLS == {"assign_to_pasture", "assign_to_shelter", "remove_from_pasture", "remove_from_shelter"}
    assert UNASSIGNED_TOOLS <= WRITE_TOOLS, "an unassigned tool that is not covered by the write guard is the one a future edit gets wrong"


def test_after_the_flip_a_write_is_proposable_but_never_performable_without_an_approval() -> None:
    """The seam, after M6. The flip changed what a model may propose; it did not change what a model
    may be handed (M10: a bound tool is callable, and the investigator's loop binds this list for
    real) and it did not change who may perform. A write still raises without an `Approval`, and
    the only thing that mints one is `src/agent/gate.py` after a human resumed the pause with `approve`."""
    assert GATE_LANDED, "M6 flipped it, after the pause and the audit stream were proven"
    for agent in SLICES:
        assert bound_tools_for(agent) == tools_for(agent) - WRITE_TOOLS, "never a write in a bound list, on either side of the flip"
        assert proposable_tools_for(agent) == tools_for(agent) & WRITE_TOOLS
    assert proposable_tools_for("water_feed") == {"consume_feed", "restock_feed"}
    assert proposable_tools_for("herd_health") == {"create_observation", "update_care_task"}
    assert proposable_tools_for("infrastructure") == proposable_tools_for("compliance") == proposable_tools_for("chaos") == frozenset()
    for tool in sorted(WRITE_TOOLS):
        with pytest.raises(WriteGateError, match="Approval"):
            assert_callable(tool)
        assert_callable(tool, approval=Approval(audit_id="a" * 32, decided_by="scooter"))  # no raise


def test_before_the_flip_nothing_was_handed_over_and_it_said_so_out_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kept as the description of the M3-to-M6 seam, because `GATE_LANDED` is one name and the
    day somebody flips it back to debug something, this is what the rails should say."""
    monkeypatch.setattr("src.tools.allowlists.GATE_LANDED", False)
    with capture_logs() as logs:
        for agent in SLICES:
            assert bound_tools_for(agent) & WRITE_TOOLS == frozenset()
            assert proposable_tools_for(agent) == frozenset()
    assert len(bound_tools_for("water_feed")) == 5 and len(bound_tools_for("herd_health")) == 4
    assert [entry["event"] for entry in logs] == ["write_tools_withheld", "write_tools_withheld"], "a silent subtraction is indistinguishable from a slice that was never right"
    with pytest.raises(WriteGateError, match="GATE_LANDED is False"):
        assert_callable("restock_feed", approval=Approval(audit_id="a" * 32, decided_by="scooter"))


def test_a_read_only_slice_is_withheld_from_nothing() -> None:
    assert bound_tools_for("infrastructure") == tools_for("infrastructure")


def test_calling_a_write_tool_raises_rather_than_returning_an_error_envelope() -> None:
    """The belt behind the filtered list. Every MCP call goes through `assert_callable`."""
    for tool in sorted(WRITE_TOOLS):
        with pytest.raises(WriteGateError, match="Approval"):
            assert_callable(tool)


def test_a_read_tool_the_agent_does_not_own_is_refused_too() -> None:
    assert_callable("read_sensor", agent="infrastructure")  # owns it, no raise
    with pytest.raises(WriteGateError, match="no read_sensor in its slice"):
        assert_callable("read_sensor", agent="herd_health")


async def test_call_tool_refuses_a_write_before_it_reaches_the_wire() -> None:
    """The guard has to fire before the session is touched, or it is not a guard."""

    class ExplodingSession:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            raise AssertionError("call_tool reached the deployed ranch with a write tool")

    with pytest.raises(WriteGateError):
        await call_tool(ExplodingSession(), "create_observation", {"animalId": "cow-0777"})  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="reached the deployed ranch"):
        await call_tool(ExplodingSession(), "create_observation", {"animalId": "cow-0777"}, approval=Approval(audit_id="a" * 32, decided_by="scooter"))  # type: ignore[arg-type]

# =========================================================================== #
# 5. chaos: the guard, the catalog, the seed, and the overlay
#
# The order below is the order these were written in, and it is not cosmetic. The write path
# is the only code in this repo that mutates a real deployed API, so its guard is proven
# first and everything else follows.
#
# Two verifications this suite deliberately does NOT contain, because both need M3's
# supervisor and faking either would be worse than deferring it:
#   * a storm front producing ONE fused work order rather than four unrelated ones
#   * `herd_health` discovering the coyote kill through its own tools, with no overlay
# Both are recorded as deferred in `docs/journey.md` and are owed at the M3 boundary.
# =========================================================================== #
#: Two hosts, not one. `/animals` is on the FARM API and only the observation is on CARE. M5
#: shipped this as a single base URL and it would have 404'd on the first real coyote kill.
FARM = "https://farm.test"
CARE = "https://care.test"

COHORT = ("cow-0901", "cow-0902", "cow-0903", "cow-0904", "cow-0905")


def chaos_settings(**over: object) -> Settings:
    """Settings for the chaos rails. Writes off and cohort empty unless a test says otherwise."""
    base: dict[str, object] = {"farm_api": FARM, "care_api": CARE, "sensor_api": BASE, "sweep_concurrency": 4, "upstream_timeout_ms": 500, "chaos_enabled": True, "chaos_seed": 1, "chaos_allow_writes": False, "chaos_animal_cohort": "", "_env_file": None}
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def animal_event(*, animal_id: str = "cow-0901", status: str = "deceased", event_id: str = "chaos-1-4") -> ChaosEvent:
    stamp = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    return ChaosEvent(
        event_id=event_id, group_id="chaos-1-t5-coyote_kill", scenario="coyote_kill", kind=KIND_ANIMAL, target_id=animal_id, target_type="animal", location="", fault=MODE_ANIMAL_STATUS,
        payload={"status": status, "observation_type": "injury", "severity": "high", "observation": "Found down at first light, predation signs."}, seed=1, seq=4, status=STATUS_ACTIVE, injected_at=stamp, expires_at=stamp + timedelta(minutes=15),
    )


def sensor_event(*, mode: str, target_id: str = "test-sensor", sensor_type: str = "water-level", payload: dict[str, object] | None = None, seq: int = 0, ttl_minutes: int = 15, injected_at: datetime | None = None) -> ChaosEvent:
    stamp = injected_at or datetime(2026, 9, 10, 14, 30, tzinfo=UTC)
    return ChaosEvent(
        event_id=f"chaos-1-{seq}", group_id="chaos-1-t1-test", scenario="test_scenario", kind=KIND_SENSOR, target_id=target_id, target_type=sensor_type, location="Alkali Flat", fault=mode,
        payload=dict(payload or {}), seed=1, seq=seq, status=STATUS_ACTIVE, injected_at=stamp, expires_at=stamp + timedelta(minutes=ttl_minutes),
    )


@pytest.fixture(autouse=True)
def _clean_chaos_warn_once() -> None:
    _reset_chaos()


# --------------------------------------------------------------------------- #
# 3a. the write guard. Proven before the write path is trusted, and first here.
# --------------------------------------------------------------------------- #
async def test_with_writes_disabled_no_animal_is_ever_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rail the whole animal path is built behind. `CHAOS_ALLOW_WRITES=0` must not merely
    refuse the request at the last moment: **no HTTP client is constructed at all.**

    Both halves matter. A guard that opens a connection and then declines to use it is one
    refactor away from sending the PATCH anyway, and this is the only code in the repo that
    can change a real animal in a real deployed database.
    """
    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("the write path opened an HTTP client with CHAOS_ALLOW_WRITES=0")

    monkeypatch.setattr("src.tools.chaos.upstream_client", _explode)
    settings = chaos_settings(chaos_allow_writes=False, chaos_animal_cohort=",".join(COHORT))

    outcomes = await fire_animal_events([animal_event(), animal_event(animal_id="cow-0902", event_id="chaos-1-9")], settings=settings)

    assert len(outcomes) == 2
    assert not any(o.performed for o in outcomes)
    assert {o.reason for o in outcomes} == {"writes_disabled"}


async def test_the_cohort_confines_the_write_even_when_writes_are_on() -> None:
    """Two independent conditions, not one with a second opinion. With writes ON, an animal
    outside `CHAOS_ANIMAL_COHORT` is still refused, so a bug in scenario targeting cannot
    scale past the handful of animals named in `.env`."""
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    assert blocked_reason("cow-0901", settings=settings) is None
    assert blocked_reason("cow-4242", settings=settings) == "outside_cohort"

    with respx.mock(assert_all_called=False) as mock:
        outcomes = await fire_animal_events([animal_event(animal_id="cow-4242")], settings=settings)
    assert [(o.performed, o.reason) for o in outcomes] == [(False, "outside_cohort")]
    assert not mock.calls


async def test_writes_on_with_an_empty_cohort_refuses_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dangerous misconfiguration: somebody sets `CHAOS_ALLOW_WRITES=1` and forgets the
    cohort. An empty allowlist has to mean nothing, never everything."""
    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("an empty cohort was read as permission")

    monkeypatch.setattr("src.tools.chaos.upstream_client", _explode)
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort="")
    assert blocked_reason("cow-0901", settings=settings) == "cohort_empty"
    outcomes = await fire_animal_events([animal_event()], settings=settings)
    assert [(o.performed, o.reason) for o in outcomes] == [(False, "cohort_empty")]


async def test_a_blocked_write_still_leaves_a_paired_audit_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The receipt that nothing happened is the evidence the guard held. A `proposed` with no
    `decided` beside it is the one audit shape `tests/CLAUDE.md` refuses, and "blocked" is a
    decision, not an absence."""
    monkeypatch.setattr("src.tools.chaos.upstream_client", lambda *_a, **_k: None)
    settings = chaos_settings(chaos_allow_writes=False, chaos_animal_cohort=",".join(COHORT))
    with capture_logs() as logs:
        await fire_animal_events([animal_event()], settings=settings)

    proposed = [line for line in logs if line.get("phase") == "proposed"]
    decided = [line for line in logs if line.get("phase") == "decided"]
    assert len(proposed) == 1 and len(decided) == 1
    assert proposed[0]["audit_id"] == decided[0]["audit_id"]
    assert decided[0]["decision"] == "blocked" and decided[0]["result"] == "writes_disabled"


async def test_a_permitted_write_patches_on_farm_then_observes_on_care() -> None:
    """The only test in this file that lets a write through, and it goes to fake hosts.

    The asymmetry being exercised: this path writes for real because `herd_health` has to
    discover a coyote kill through its own tools. An overlay it cannot see is not a discovery,
    so there is no cheaper way to prove that behaviour.

    **The two routes are on two different hosts on purpose.** M5 shipped both against
    `CARE_API` and this rail is what makes the split structural: the PATCH is mocked only on
    FARM and the POST only on CARE, so collapsing them back into one client fails here rather
    than 404ing in front of an audience. Every field asserted below was read off the deployed
    services' own 422 responses on 2026-09-10.
    """
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    with respx.mock(assert_all_called=True) as mock:
        patched = mock.patch(f"{FARM}/animals/cow-0901").respond(200, json={"data": {"id": "cow-0901", "status": "deceased"}})
        observed = mock.post(f"{CARE}/animals/cow-0901/observations").respond(201, json={"data": {"id": "obs-1"}})
        outcomes = await fire_animal_events([animal_event()], settings=settings)

    assert [(o.performed, o.reason) for o in outcomes] == [(True, "written")]
    assert json.loads(patched.calls[0].request.content) == {"status": "deceased"}
    body = json.loads(observed.calls[0].request.content)
    assert body == {"type": "injury", "severity": "high", "note": "Found down at first light, predation signs.", "observedAt": "2026-09-10T14:30:00.000Z"}
    assert "notes" not in body and "observedBy" not in body, "`notes` 422s and `observedBy` is silently dropped; a body that looks like it recorded an author and did not is worse than one that never claimed to"


async def test_the_observation_is_stamped_when_the_ranch_broke_not_when_the_write_ran() -> None:
    """`observedAt` is required upstream, and the note a human reads has to carry the time the
    event happened. Milliseconds and a `Z`, matching the upstream services exactly, because
    lexicographic ordering is relied upon."""
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    event = replace(animal_event(), injected_at=datetime(2026, 9, 10, 5, 6, 7, 89000, tzinfo=UTC))
    with respx.mock(assert_all_called=True) as mock:
        mock.patch(f"{FARM}/animals/cow-0901").respond(200, json={"data": {}})
        observed = mock.post(f"{CARE}/animals/cow-0901/observations").respond(201, json={"data": {}})
        await fire_animal_events([event], settings=settings)
    assert json.loads(observed.calls[0].request.content)["observedAt"] == "2026-09-10T05:06:07.089Z"


async def test_a_status_that_landed_and_a_note_that_did_not_is_a_partial_not_a_retry() -> None:
    """The animal really is changed upstream. Reporting this as a failure invites a caller to
    retry the pair and write the status twice."""
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    with respx.mock(assert_all_called=True) as mock:
        mock.patch(f"{FARM}/animals/cow-0901").respond(200, json={"data": {}})
        mock.post(f"{CARE}/animals/cow-0901/observations").respond(500, json={"error": {"message": "boom"}})
        outcomes = await fire_animal_events([animal_event()], settings=settings)

    assert outcomes[0].performed is True
    assert outcomes[0].reason == "observation_failed_500"


async def test_a_rejected_status_never_reaches_the_observation() -> None:
    """A 422 on the PATCH means the animal is unchanged, so posting the note anyway would put a
    coyote kill in a live cow's history with nothing behind it. The pair is ordered for this
    reason: the status is the fact, the note is the story about the fact."""
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    with respx.mock(assert_all_called=False) as mock:
        mock.patch(f"{FARM}/animals/cow-0901").respond(422, json={"error": {"code": "VALIDATION_ERROR"}})
        observed = mock.post(f"{CARE}/animals/cow-0901/observations").respond(201, json={"data": {}})
        outcomes = await fire_animal_events([animal_event()], settings=settings)

    assert (outcomes[0].performed, outcomes[0].reason) == (False, "patch_failed_422")
    assert not observed.called


async def test_restoring_the_cohort_goes_through_the_same_guard_as_breaking_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sensor overlay expires and the sensor is honest again. A real PATCH is not undone by
    a row changing status, so restore is a deliberate command - and it is not a back door."""
    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("restore reached the network with writes disabled")

    monkeypatch.setattr("src.tools.chaos.upstream_client", _explode)
    settings = chaos_settings(chaos_allow_writes=False, chaos_animal_cohort=",".join(COHORT))
    outcomes = await restore_cohort(settings=settings)
    assert len(outcomes) == len(COHORT)
    assert {o.reason for o in outcomes} == {"writes_disabled"}


async def test_restore_refuses_a_status_the_farm_api_would_reject() -> None:
    """M5 shipped `--status healthy` as the default and `healthy` is not in the enum, so the
    reset command would have 422'd five times and left the cohort dead. It raises before the
    network now, and the normal state is `active`."""
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    with pytest.raises(ValueError, match="not one of"):
        await restore_cohort(status="healthy", settings=settings)
    assert ANIMAL_STATUS_NORMAL == "active" and ANIMAL_STATUS_NORMAL in ANIMAL_STATUSES


async def test_a_sensor_overlay_event_never_reaches_the_write_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two paths do not leak into each other. A `sensor_overlay` row is a lie stored
    locally and must never turn into an upstream mutation."""
    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("a sensor overlay event reached the Care API")

    monkeypatch.setattr("src.tools.chaos.upstream_client", _explode)
    settings = chaos_settings(chaos_allow_writes=True, chaos_animal_cohort=",".join(COHORT))
    assert await fire_animal_events([sensor_event(mode=MODE_OFFLINE)], settings=settings) == ()


# --------------------------------------------------------------------------- #
# 3b. the catalog validates loudly
# --------------------------------------------------------------------------- #
def test_the_shipped_catalog_parses_and_every_scenario_is_usable() -> None:
    """`data/examples.json` is data, and nothing else in the gate reads it. If it stops
    parsing, this is the only place that notices before a demo does."""
    scenarios = load_catalog()
    assert len(scenarios) >= 12
    assert {s.kind for s in scenarios} <= {KIND_SENSOR, KIND_ANIMAL}
    assert any(s.correlated for s in scenarios), "no correlated scenario, so the fused-work-order story has nothing to fire"
    assert any(s.kind == KIND_ANIMAL for s in scenarios), "no animal scenario, so herd_health has nothing to discover"
    for s in scenarios:
        assert s.ttl_ticks > 0 and s.weight > 0 and s.faults


def test_the_catalog_comes_back_sorted_by_id_so_file_order_cannot_change_a_seed() -> None:
    """Weights decide what fires. File order must not, or reordering the catalog for
    readability silently rewrites every replay."""
    ids = [s.id for s in load_catalog()]
    assert ids == sorted(ids)


def _one_scenario(**over: object) -> dict[str, object]:
    base: dict[str, object] = {"id": "s1", "title": "t", "kind": KIND_SENSOR, "weight": 1, "ttl_ticks": 2, "faults": [{"mode": MODE_PIN, "sensor_type": "water-level", "value": 0.5}]}
    base.update(over)
    return {"scenarios": [base]}


def _animal_scenario(**over: object) -> dict[str, object]:
    """An animal scenario whose four upstream-validated fields can be broken one at a time.

    These are checked at LOAD against the enums the deployed services enforce, because the
    alternative is a 422 during a demo: the write is the only path here that can be rejected by
    a service rather than by this repo, and nobody is reading the log while an audience waits.
    """
    fault: dict[str, object] = {"mode": MODE_ANIMAL_STATUS, "status": "deceased", "observation_type": "injury", "severity": "high", "observation": "Found down at first light."}
    fault.update(over)
    return {"scenarios": [{"id": "kill", "title": "t", "kind": KIND_ANIMAL, "weight": 1, "ttl_ticks": 2, "faults": [fault]}]}


@pytest.mark.parametrize(
    ("broken", "fragment"),
    [
        ({"scenarios": []}, "configured to do nothing"),
        (_one_scenario(faults=[{"mode": "explode_the_barn", "sensor_type": "water-level"}]), "not valid for kind"),
        (_one_scenario(kind="telepathy"), "which is not one of"),
        (_one_scenario(weight=0), "should be deleted, not weighted to zero"),
        (_one_scenario(ttl_ticks=0), "goes quiet an hour in"),
        (_one_scenario(faults=[{"mode": MODE_PIN, "sensor_type": "water-level"}]), "without a numeric `value`"),
        (_one_scenario(faults=[{"mode": MODE_DRIFT, "sensor_type": "battery-charge", "from": 40}]), "`from` and `to`"),
        (_one_scenario(faults=[{"mode": MODE_OFFLINE}]), "no `sensor_type`"),
        (_one_scenario(faults=[{"mode": MODE_PIN, "sensor_type": "water-level", "value": 1, "count": 0}]), "count 0"),
        ({"scenarios": [{"id": "s1", "ttl_ticks": 1, "faults": [{"mode": MODE_OFFLINE, "sensor_type": "gate"}]}, {"id": "s1", "ttl_ticks": 1, "faults": [{"mode": MODE_OFFLINE, "sensor_type": "gate"}]}]}, "duplicate scenario id"),
        (_one_scenario(kind=KIND_ANIMAL, faults=[{"mode": MODE_PIN, "sensor_type": "water-level", "value": 1}]), "not valid for kind"),
        (_animal_scenario(status="dead"), "the Farm API accepts only"),
        (_animal_scenario(observation_type="mauling"), "the Care API accepts only"),
        (_animal_scenario(severity="catastrophic"), "the Care API accepts only"),
        (_animal_scenario(observation="   "), "what a human reads"),
    ],
)
def test_a_broken_catalog_raises_at_load_rather_than_no_opping_at_inject_time(broken: object, fragment: str) -> None:
    """The natural failure of a data-driven injector is a scenario that parses, fires, and
    does nothing. On stage that is indistinguishable from a calm ranch, so validation is loud
    and it happens at load."""
    with pytest.raises(ChaosCatalogError) as err:
        parse_catalog(broken)
    assert fragment in str(err.value)


# --------------------------------------------------------------------------- #
# 3c. determinism is the product
# --------------------------------------------------------------------------- #
def fixture_catalog() -> tuple[SensorRef, ...]:
    entries = load_fixtures()["catalog"]
    return tuple(SensorRef(sensor_id=str(e["sensor_id"]), sensor_type=str(e["sensor_type"]), location=str(e.get("location") or ""), status="online") for e in entries)


def as_rows(planned: tuple[PlannedFault, ...]) -> list[dict[str, object]]:
    return [{"seq": p.seq, "tick": p.tick, "scenario": p.scenario, "group_id": p.group_id, "kind": p.kind, "target_id": p.target_id, "target_type": p.target_type, "location": p.location, "fault": p.fault, "ttl_ticks": p.ttl_ticks, "payload": p.payload} for p in planned]


def test_seed_one_replays_the_golden_plan_exactly() -> None:
    """The rail that makes "same seed, same demo, twice" a fact rather than an intention.

    Checked in as data in `data/examples.json` rather than computed here, so this fails if the
    draw order, the weights, the catalog sort, or the group-id scheme changes. Every one of
    those is a real change to what a recorded demo does, and it should require a deliberate
    regeneration of the fixture rather than passing quietly.
    """
    fixtures = load_fixtures()
    got = as_rows(plan(seed=1, catalog=fixture_catalog(), ticks=int(fixtures["ticks"]), cohort=tuple(fixtures["cohort"])))
    assert got == fixtures["plan_seed_1"]


def test_the_plan_ignores_the_order_the_catalog_arrived_in() -> None:
    """`ranch://sensors/map` and `GET /sensors` do not promise the same order, and a demo must
    not change shape based on which source answered."""
    catalog = fixture_catalog()
    assert as_rows(plan(seed=1, catalog=catalog, ticks=13, cohort=COHORT)) == as_rows(plan(seed=1, catalog=tuple(reversed(catalog)), ticks=13, cohort=COHORT))


def test_a_longer_plan_extends_a_shorter_one_rather_than_rewriting_it() -> None:
    """Every draw happens in tick order, so `event_id` is stable across runs of different
    lengths. That is what lets a re-injected demo be idempotent instead of doubled."""
    catalog = fixture_catalog()
    short = as_rows(plan(seed=1, catalog=catalog, ticks=4, cohort=COHORT))
    longer = as_rows(plan(seed=1, catalog=catalog, ticks=13, cohort=COHORT))
    assert longer[: len(short)] == short


def test_a_different_seed_is_a_different_demo() -> None:
    catalog = fixture_catalog()
    assert as_rows(plan(seed=1, catalog=catalog, ticks=13, cohort=COHORT)) != as_rows(plan(seed=2, catalog=catalog, ticks=13, cohort=COHORT))


def test_an_animal_event_stays_in_the_plan_whether_or_not_writes_are_permitted() -> None:
    """`plan()` reads no settings at all. A plan that changes shape when a flag flips is a plan
    whose seed no longer identifies a demo; the guard belongs at the write, where it can leave
    a receipt."""
    planned = plan(seed=1, catalog=fixture_catalog(), ticks=13, cohort=COHORT)
    assert any(p.kind == KIND_ANIMAL for p in planned)
    assert all(p.target_id in COHORT for p in planned if p.kind == KIND_ANIMAL)


def test_an_empty_cohort_plans_no_animal_event_and_says_so() -> None:
    with capture_logs() as logs:
        planned = plan(seed=1, catalog=fixture_catalog(), ticks=13, cohort=())
    assert not [p for p in planned if p.kind == KIND_ANIMAL]
    assert any(line["event"] == "chaos_no_cohort_target" for line in logs)


def test_a_scenario_asking_for_a_type_the_ranch_does_not_have_warns_and_skips_it() -> None:
    """The catalog names sensor TYPES, never ids, so it survives a ranch that adds a pasture.
    The cost is that a type can be absent, and that has to be loud rather than a silent
    scenario that fires and does nothing."""
    scenarios = (Scenario(id="ghost", title="ghost", kind=KIND_SENSOR, weight=1, ttl_ticks=2, faults=(FaultSpec(mode=MODE_OFFLINE, sensor_type="unicorn-detector"),)),)
    with capture_logs() as logs:
        assert plan(seed=1, catalog=fixture_catalog(), ticks=3, scenarios=scenarios) == ()
    assert any(line["event"] == "chaos_no_target_for_type" for line in logs)


def test_a_co_located_scenario_that_cannot_pair_records_the_miss_rather_than_faking_it() -> None:
    """`well_failure` claims a causal story: a wellhead and the tank it fills, at one place.
    Given a catalog where that pairing is impossible the fault is still useful, but it is no
    longer the story it advertises, so the row says so."""
    scenarios = (Scenario(id="well_failure", title="well", kind=KIND_SENSOR, weight=1, ttl_ticks=2, correlated=True, co_located=True, faults=(FaultSpec(mode=MODE_PIN, sensor_type="pressure", params={"value": 118.0}), FaultSpec(mode=MODE_PIN, sensor_type="water-level", params={"value": 0.7}))),)
    catalog = (SensorRef(sensor_id="a-press", sensor_type="pressure", location="Home Place", status="online"), SensorRef(sensor_id="b-water", sensor_type="water-level", location="North Pasture", status="online"))
    planned = plan(seed=1, catalog=catalog, ticks=1, scenarios=scenarios)
    assert len(planned) == 2
    assert "co_located" not in planned[0].payload  # the anchor cannot miss its own location
    assert planned[1].payload["co_located"] is False


def test_a_co_located_scenario_that_can_pair_does_not_flag_a_miss() -> None:
    scenarios = (Scenario(id="well_failure", title="well", kind=KIND_SENSOR, weight=1, ttl_ticks=2, correlated=True, co_located=True, faults=(FaultSpec(mode=MODE_PIN, sensor_type="pressure", params={"value": 118.0}), FaultSpec(mode=MODE_PIN, sensor_type="water-level", params={"value": 0.7}))),)
    catalog = (SensorRef(sensor_id="a-press", sensor_type="pressure", location="Home Place", status="online"), SensorRef(sensor_id="b-water", sensor_type="water-level", location="Home Place", status="online"))
    planned = plan(seed=1, catalog=catalog, ticks=1, scenarios=scenarios)
    assert {p.location for p in planned} == {"Home Place"}
    assert not any("co_located" in p.payload for p in planned)
    assert len({p.group_id for p in planned}) == 1, "a correlated scenario is one group, or the fusion story has nothing to key on"


def test_ticks_between_events_thins_the_plan_without_reshuffling_it() -> None:
    """The demo knob: one event every N ticks, so a long run does not saturate. It changes
    WHEN a scenario fires and must not change the sequence of scenarios drawn."""
    catalog = fixture_catalog()
    every_tick = plan(seed=1, catalog=catalog, ticks=6, cohort=COHORT, ticks_between_events=1)
    every_third = plan(seed=1, catalog=catalog, ticks=6, cohort=COHORT, ticks_between_events=3)
    assert {p.tick for p in every_third} == {3, 6}
    assert [p.scenario for p in every_third][:1] == [every_tick[0].scenario]


# --------------------------------------------------------------------------- #
# 3d. every fault mode lands in a category triage already owns
#
# Chaos invents no category and no severity. Each mode below produces a reading that
# `triage.py` already has a rule for, which is what keeps a faulted ranch and a genuinely
# broken one indistinguishable from the monitor's side. If chaos needed its own triage
# branch, the exercise would be testing the fixture instead of the monitor.
# --------------------------------------------------------------------------- #
def overlay_one(reading_in: SensorReading, event: ChaosEvent, *, now: datetime | None = None) -> SensorReading:
    return apply_overlay([reading_in], [event], now=now)[0]


def test_offline_makes_the_dark_sensor_triage_already_knows() -> None:
    faked = overlay_one(reading("water-level", 16.4), sensor_event(mode=MODE_OFFLINE))
    assert faked.status == "offline" and faked.value is None
    assert [f.category for f in triage_reading(faked)] == [CATEGORY_OFFLINE]


def test_degraded_leaves_the_value_alone_because_that_is_the_harder_failure_to_notice() -> None:
    """A degraded sensor still answers. The number is merely no longer trustworthy, and a
    human scanning a dashboard reads it as fine."""
    faked = overlay_one(reading("water-level", 16.4), sensor_event(mode=MODE_DEGRADED))
    assert faked.status == "degraded" and faked.value == 16.4
    assert CATEGORY_DEGRADED in [f.category for f in triage_reading(faked)]


def test_a_sentinel_reads_as_a_broken_probe_and_never_as_weather() -> None:
    """-500 F is the fault the live ranch already has: a temperature sensor answering an
    impossible number with `status: "online"`. Chaos reproduces the real one."""
    faked = overlay_one(reading("temperature", 61.0), sensor_event(mode=MODE_SENTINEL, sensor_type="temperature", payload={"value": -500.0}))
    assert faked.value == -500.0 and faked.status == "online"
    assert [f.category for f in triage_reading(faked)] == [CATEGORY_FAULT]


def test_a_pin_lands_inside_a_real_threshold_band() -> None:
    faked = overlay_one(reading("water-level", 16.4), sensor_event(mode=MODE_PIN, payload={"value": 0.8}))
    findings = triage_reading(faked)
    assert [(f.category, f.severity) for f in findings] == [("water_low", "critical")]


def test_gate_open_stays_boolean_through_the_overlay() -> None:
    """The one type whose rule is not a threshold comparison. A gate faked to the number 1 is
    a broken feed, not an open gate, and triage would say so."""
    faked = overlay_one(reading("gate", False), sensor_event(mode=MODE_GATE_OPEN, sensor_type="gate"))
    assert faked.value is True and isinstance(faked.value, bool)
    assert [f.category for f in triage_reading(faked)] == ["gate_open"]


def test_a_drift_ramps_from_nominal_through_warning_into_critical() -> None:
    """The showcase mode, and the reason it exists. A step change is one incident that opens
    once; a ramp is an incident that gets WORSE while a human watches, which is the only way
    `ongoing` and a severity escalation ever get exercised.
    """
    injected = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
    event = sensor_event(mode=MODE_DRIFT, sensor_type="battery-charge", payload={"from": 45.0, "to": 8.0}, ttl_minutes=60, injected_at=injected)
    honest = reading("battery-charge", 88.0)

    at_start = overlay_one(honest, event, now=injected)
    at_half = overlay_one(honest, event, now=injected + timedelta(minutes=30))
    at_end = overlay_one(honest, event, now=injected + timedelta(minutes=60))
    past_end = overlay_one(honest, event, now=injected + timedelta(minutes=600))

    assert (at_start.value, at_end.value) == (45.0, 8.0)
    assert at_half.value == pytest.approx(26.5)
    assert past_end.value == 8.0, "progress is clamped, so a stale event does not ramp past its endpoint"

    assert triage_reading(at_start) == []
    assert [(f.category, f.severity) for f in triage_reading(at_half)] == [("power_low", "warning")]
    assert [(f.category, f.severity) for f in triage_reading(at_end)] == [("power_low", "critical")]


def test_an_unknown_fault_mode_warns_once_and_changes_nothing() -> None:
    """Same discipline as an unrecognized sensor type in triage. There is no default branch in
    `apply_fault`, so a typo'd mode cannot quietly become "do nothing" without saying so."""
    honest = reading("water-level", 16.4)
    with capture_logs() as logs:
        first = overlay_one(honest, sensor_event(mode="telekinesis"))
        second = overlay_one(honest, sensor_event(mode="telekinesis", seq=1))
    assert first == honest and second == honest
    assert len([line for line in logs if line["event"] == "chaos_unknown_fault_mode"]) == 1


def test_a_pin_with_no_numeric_value_warns_and_leaves_the_reading_honest() -> None:
    """Belt and braces. `parse_catalog` already refuses this, but a row can also come from the
    database, and a fault that cannot apply must not silently corrupt the reading."""
    honest = reading("water-level", 16.4)
    with capture_logs() as logs:
        assert overlay_one(honest, sensor_event(mode=MODE_PIN, payload={})) == honest
    assert any(line["event"] == "chaos_fault_unusable" for line in logs)


def test_the_overlay_logs_the_honest_value_beside_the_faked_one() -> None:
    """The log is the only place the truth still exists after the substitution. A faked reading
    that leaves no trace is a demo nobody can audit afterward."""
    with capture_logs() as logs:
        overlay_one(reading("water-level", 16.4), sensor_event(mode=MODE_PIN, payload={"value": 0.8}))
    applied = [line for line in logs if line["event"] == "chaos_overlay_applied"]
    assert len(applied) == 1
    assert applied[0]["honest_value"] == 16.4 and applied[0]["faked_value"] == 0.8


def test_the_overlay_touches_only_its_own_target() -> None:
    readings = [reading("water-level", 16.4, sensor_id="fx-water-01"), reading("water-level", 16.4, sensor_id="fx-water-02")]
    faked = apply_overlay(readings, [sensor_event(mode=MODE_PIN, target_id="fx-water-02", payload={"value": 0.8})])
    assert [r.value for r in faked] == [16.4, 0.8]


def test_an_animal_event_is_never_laid_over_a_reading() -> None:
    """The two paths are separate by construction. An `animal_event` in the active set is a
    receipt for an upstream write, not something to apply to a sensor."""
    readings = [reading("water-level", 16.4, sensor_id="cow-0901")]
    assert apply_overlay(readings, [animal_event()]) == tuple(readings)


def test_a_fault_on_a_sensor_this_sweep_did_not_read_changes_nothing() -> None:
    """Orphans are allowed on purpose: cross-service ids are plain strings and chaos adds no
    existence check. An overlay row for a sensor that has since gone away is inert."""
    readings = [reading("water-level", 16.4, sensor_id="fx-water-01")]
    assert apply_overlay(readings, [sensor_event(mode=MODE_OFFLINE, target_id="fx-ghost-99")]) == tuple(readings)


def test_two_faults_on_one_sensor_compose_in_seq_order() -> None:
    """Deterministic composition. The database allows one active fault per (target, mode), so
    two different modes on one sensor is legal and has to resolve the same way every run."""
    honest = reading("water-level", 16.4)
    events = [sensor_event(mode=MODE_PIN, payload={"value": 0.8}, seq=1), sensor_event(mode=MODE_DEGRADED, seq=0)]
    faked = apply_overlay(honest and [honest], events)[0]
    assert (faked.status, faked.value) == ("degraded", 0.8)


async def test_the_sweep_applies_the_overlay_and_reports_how_many_lies_it_told() -> None:
    """The overlay is applied inside `sweep()` and nowhere else, so nothing downstream needs to
    know chaos exists. `overlay_events` rides on the result rather than hiding in the sweep,
    because a tick line that cannot say the ranch was being lied to is a mystery, not a demo."""
    with respx.mock(base_url=BASE) as mock:
        mock.get(f"/sensors/{REF.sensor_id}").respond(200, json=payload(16.4))
        result = await sweep([REF], overlay=[sensor_event(mode=MODE_PIN, target_id=REF.sensor_id, payload={"value": 0.8})])

    assert result.overlay_events == 1
    assert [r.value for r in result.readings] == [0.8]
    assert [(f.category, f.severity) for f in triage_sweep(result.readings)] == [("water_low", "critical")]


async def test_the_sweep_names_which_overlay_events_its_readings_actually_carried() -> None:
    """M4's miss check reads `overlay_observed`. An active fault on a sensor that did not answer
    was never observed, and if it heals before the next sweep the chaos log says it happened
    while the ranch read calm the whole time. The sweep is the only place that knows which
    sensors answered, so it is the sweep that says which faults were seen."""
    other = replace(REF, sensor_id="dark-sensor")
    with respx.mock(base_url=BASE) as mock:
        mock.get(f"/sensors/{REF.sensor_id}").respond(200, json=payload(16.4))
        mock.get(f"/sensors/{other.sensor_id}").respond(503, json={"error": {"category": "upstream"}})
        result = await sweep([REF, other], overlay=[sensor_event(mode=MODE_PIN, target_id=REF.sensor_id, payload={"value": 0.8}, seq=0), sensor_event(mode=MODE_PIN, target_id=other.sensor_id, payload={"value": 0.8}, seq=1)])

    assert result.overlay_events == 2, "both were active"
    assert result.overlay_observed == ("chaos-1-0",), "but only the one whose sensor answered was seen"


async def test_an_empty_overlay_is_an_honest_sweep_and_says_zero() -> None:
    with respx.mock(base_url=BASE) as mock:
        mock.get(f"/sensors/{REF.sensor_id}").respond(200, json=payload(16.4))
        result = await sweep([REF], overlay=[])
    assert result.overlay_events == 0 and [r.value for r in result.readings] == [16.4]
    assert triage_sweep(result.readings) == []


async def test_chaos_switched_off_costs_a_boolean_and_never_a_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rail that protects the OTHER session's numbers. `CHAOS_ENABLED=0` has to be free:
    an overlay firing underneath a cost measurement turns the measurement into noise, and a
    per-tick database round trip for an empty answer is a tax on every calm tick forever."""
    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("a switched-off overlay resolved a store")

    monkeypatch.setattr("src.tools.chaos.get_settings", lambda: chaos_settings(chaos_enabled=False))
    monkeypatch.setattr("src.tools.chaos.resolve_store", _explode)
    assert await active_overlay() == ()


async def test_an_overlay_that_cannot_be_read_leaves_the_sweep_honest_rather_than_failing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chaos is a fixture. A fixture that can fail the tick it decorates is worse than no
    fixture, so this is the one place in the repo that swallows a broad exception on purpose -
    and it says so in the log rather than returning a quiet empty tuple."""
    def _explode(*_a: object, **_k: object) -> None:
        raise RuntimeError("sw_ops is unreachable")

    monkeypatch.setattr("src.tools.chaos.get_settings", lambda: chaos_settings(chaos_enabled=True))
    monkeypatch.setattr("src.tools.chaos.resolve_store", _explode)
    with capture_logs() as logs:
        assert await active_overlay() == ()
    assert any(line["event"] == "chaos_overlay_unavailable" for line in logs)


# --------------------------------------------------------------------------- #
# 3e. the ledger, against a real `sw_ops_test`
#
# These rails skip when no local Postgres is up, per `tests/CLAUDE.md`, so `pytest` passes on
# a plane. They are the only place the partial unique index, `ON CONFLICT DO NOTHING`, and
# JSONB round-tripping are proven, and none of those can be proven against a fake.
# --------------------------------------------------------------------------- #
@pytest.fixture
async def chaos_store(migrated_store: str) -> AsyncIterator[AsyncSession]:
    """An empty `chaos_events` AND an empty `incidents`, on one session.

    A fresh engine per test for the same reason `store` does it: pytest-asyncio gives each
    test its own loop and an AsyncEngine is bound to the loop it first connected on. Defined
    here rather than in `conftest.py` deliberately - the M3 session is closing a phase in the
    shared fixtures right now, and a table only these rails use does not need to be there.
    """
    engine = build_engine(migrated_store, schema=SCHEMA_TEST)
    async with engine.begin() as conn:
        await conn.execute(text("truncate table chaos_events restart identity"))
        await conn.execute(text("truncate table incidents restart identity"))
    try:
        async with session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


NOW = datetime(2026, 9, 10, 14, 30, tzinfo=UTC)


async def test_injecting_one_tick_writes_the_rows_and_they_come_back_active(chaos_store: AsyncSession) -> None:
    planned = plan(seed=1, catalog=fixture_catalog(), ticks=2, cohort=COHORT)
    events = await inject_for_tick(chaos_store, tick=2, planned=planned, now=NOW, run_id="run-x", max_active=6, tick_seconds=300)

    assert [e.tick_injected for e in events] == [2]
    assert events[0].status == STATUS_ACTIVE and events[0].run_id == "run-x"
    assert events[0].expires_at == NOW + timedelta(seconds=300 * planned[1].ttl_ticks)
    assert events[0].payload, "the fault parameters have to survive the JSONB round trip, or `apply_fault` has nothing to read"

    live = await active_chaos_events(chaos_store, now=NOW, kind=KIND_SENSOR)
    assert tuple(ChaosEvent.from_row(r) for r in live) == events


async def test_replaying_the_same_seed_is_idempotent_rather_than_doubling_the_demo(chaos_store: AsyncSession) -> None:
    """`event_id` is derived from `(seed, seq)` rather than from a uuid, so the database itself
    refuses the second copy. Determinism made structural: re-running a demo cannot stack two
    identical faults on one tank."""
    planned = plan(seed=1, catalog=fixture_catalog(), ticks=2, cohort=COHORT)
    first = await inject_for_tick(chaos_store, tick=2, planned=planned, now=NOW, max_active=6, tick_seconds=300)
    with capture_logs() as logs:
        second = await inject_for_tick(chaos_store, tick=2, planned=planned, now=NOW + timedelta(seconds=1), max_active=6, tick_seconds=300)

    assert first and second == ()
    assert any(line["event"] == "chaos_insert_deduplicated" for line in logs)
    counts = await chaos_counts_by_status(chaos_store)
    assert counts == {STATUS_ACTIVE: len(first)}


async def test_a_second_fault_of_the_same_mode_on_the_same_target_is_refused_while_the_first_is_active(chaos_store: AsyncSession) -> None:
    """The partial unique index, and the reason it is partial. One ACTIVE fault per
    (target, mode); the history still survives, because the same tank faulted this morning and
    again tonight is two rows - which is exactly what a resolved incident followed by a new one
    looks like."""
    one = sensor_event(mode=MODE_PIN, target_id="fx-water-02", payload={"value": 0.8}, seq=0)
    two = sensor_event(mode=MODE_PIN, target_id="fx-water-02", payload={"value": 0.4}, seq=1)
    assert len(await insert_chaos_events(chaos_store, [one.to_row()])) == 1
    assert await insert_chaos_events(chaos_store, [two.to_row()]) == ()

    await expire_chaos_events(chaos_store, now=NOW, force_all=True)
    assert len(await insert_chaos_events(chaos_store, [two.to_row()])) == 1, "once the first has healed, the same tank may break again"


async def test_the_ceiling_admits_a_correlated_scenario_whole_or_not_at_all(chaos_store: AsyncSession) -> None:
    """A storm front trimmed to two of its four faults is no longer the scenario it claims to
    be, and the fused-work-order behaviour it exists to demonstrate would silently stop being
    tested. So the ceiling defers the whole group rather than truncating the list."""
    scenarios = (Scenario(id="storm", title="storm", kind=KIND_SENSOR, weight=1, ttl_ticks=3, correlated=True, faults=(FaultSpec(mode=MODE_PIN, sensor_type="water-level", count=3, params={"value": 0.8}),)),)
    planned = plan(seed=1, catalog=fixture_catalog(), ticks=1, scenarios=scenarios)
    assert len(planned) == 3

    with capture_logs() as logs:
        events = await inject_for_tick(chaos_store, tick=1, planned=planned, now=NOW, max_active=2, tick_seconds=300)
    assert events == ()
    assert any(line["event"] == "chaos_group_deferred" for line in logs)

    events = await inject_for_tick(chaos_store, tick=1, planned=planned, now=NOW, max_active=3, tick_seconds=300)
    assert len(events) == 3


async def test_a_full_ranch_injects_nothing_until_healing_catches_up(chaos_store: AsyncSession) -> None:
    """The ceiling is what keeps something left to break. Without it every sensor is faulted
    twenty minutes in and the ranch stops being able to surprise anyone."""
    await insert_chaos_events(chaos_store, [sensor_event(mode=MODE_OFFLINE, target_id=f"fx-{i}", seq=i).to_row() for i in range(4)])
    planned = plan(seed=1, catalog=fixture_catalog(), ticks=2, cohort=COHORT)
    with capture_logs() as logs:
        assert await inject_for_tick(chaos_store, tick=2, planned=planned, now=NOW, max_active=4, tick_seconds=300) == ()
    assert any(line["event"] == "chaos_at_ceiling" for line in logs)


async def test_an_expired_event_stops_being_applied_even_before_anything_sweeps_expiry(chaos_store: AsyncSession) -> None:
    """Belt and braces: `active_chaos_events` filters on `expires_at` as well as on `status`, so
    a sensor heals on time whether or not an expiry pass has run yet. A fault that outlives its
    TTL because nobody swept is a ranch that never gets better."""
    await insert_chaos_events(chaos_store, [sensor_event(mode=MODE_PIN, target_id="fx-water-02", payload={"value": 0.8}, ttl_minutes=15).to_row()])
    assert len(await active_chaos_events(chaos_store, now=NOW + timedelta(minutes=10))) == 1
    assert await active_chaos_events(chaos_store, now=NOW + timedelta(minutes=20)) == ()


async def test_expiry_flips_the_row_and_stamps_when_it_healed(chaos_store: AsyncSession) -> None:
    await insert_chaos_events(chaos_store, [sensor_event(mode=MODE_PIN, target_id="fx-water-02", payload={"value": 0.8}, ttl_minutes=15).to_row()])
    healed = await expire(chaos_store, now=NOW + timedelta(minutes=20))
    assert [e.status for e in healed] == ["expired"]
    assert healed[0].expired_at == NOW + timedelta(minutes=20)
    assert await chaos_counts_by_status(chaos_store) == {"expired": 1}


async def test_force_expire_heals_the_whole_ranch_on_demand(chaos_store: AsyncSession) -> None:
    """The demo reset. `--all` exists because the thing you need between two run-throughs is a
    calm ranch, not a fifteen-minute wait."""
    await insert_chaos_events(chaos_store, [sensor_event(mode=MODE_OFFLINE, target_id=f"fx-{i}", seq=i, ttl_minutes=600).to_row() for i in range(3)])
    assert await expire(chaos_store, now=NOW) == ()
    assert len(await expire(chaos_store, now=NOW, force_all=True)) == 3


# --------------------------------------------------------------------------- #
# 3f. one scenario, end to end
# --------------------------------------------------------------------------- #
async def test_one_scenario_from_injection_through_a_resolved_incident(chaos_store: AsyncSession) -> None:
    """Inject, sweep, triage catches it, the TTL expires, reconcile resolves it.

    This is the rail M5 exists to produce. Every other chaos test proves one piece in
    isolation; this one proves the pieces compose, and specifically that **triage never learns
    chaos exists**. The sweep is faked at the HTTP boundary and answers honestly both times;
    the only difference between the two halves is whether an overlay row is active.

    The resolve half is the half that matters. An injector without healing gives you a ledger
    that only grows, and `resolved` is the bucket that makes the feed feel like a ranch rather
    than a list of complaints.
    """
    tank = SensorRef(sensor_id="fx-water-02", sensor_type="water-level", location="South Draw", status="online")
    honest = {"data": {"id": tank.sensor_id, "type": tank.sensor_type, "locationName": tank.location, "status": "online", "latestReading": {"value": 16.4, "recordedAt": FROZEN_TS}}}

    planned = [p for p in plan(seed=1, catalog=fixture_catalog(), ticks=2, cohort=COHORT) if p.tick == 2]
    assert [(p.scenario, p.target_id, p.fault) for p in planned] == [("dry_tank", "fx-water-02", MODE_PIN)], "the golden plan moved; regenerate the fixture on purpose or fix the draw"

    # --- tick 2: chaos fires, and the monitor sees a dry tank on a live ranch that is fine ---
    injected = await inject_for_tick(chaos_store, tick=2, planned=planned, now=NOW, run_id="e2e", max_active=6, tick_seconds=300)
    overlay = await active_chaos_events(chaos_store, now=NOW, kind=KIND_SENSOR)
    assert len(injected) == 1 and len(overlay) == 1

    with respx.mock(base_url=BASE) as mock:
        mock.get(f"/sensors/{tank.sensor_id}").respond(200, json=honest)
        faulted = await sweep([tank], overlay=[ChaosEvent.from_row(r) for r in overlay])

    findings = triage_sweep(faulted.readings)
    assert [(f.category, f.severity, f.value) for f in findings] == [("water_low", "critical", 0.8)]
    glimpsed = await reconcile(chaos_store, findings, tick=2, run_id="e2e", read_subject_ids=[tank.sensor_id], now=NOW)
    assert glimpsed.counts["pending"] == 1 and glimpsed.counts["opened"] == 0, "one bad sweep is pending, which is why every scenario's TTL is at least two ticks"
    opened = await reconcile(chaos_store, findings, tick=3, run_id="e2e", read_subject_ids=[tank.sensor_id], now=NOW + timedelta(seconds=300))
    assert opened.counts == {"opened": 1, "ongoing": 0, "resolved": 0, "pending": 0, "dismissed": 0}, "the fault is still there on the next sweep, so it opens"

    # --- tick 5: the TTL is up, the overlay is gone, and the same honest sweep reads nominal --
    later = NOW + timedelta(seconds=300 * (planned[0].ttl_ticks + 1))
    healed = await expire(chaos_store, now=later)
    assert [e.event_id for e in healed] == [injected[0].event_id]

    with respx.mock(base_url=BASE) as mock:
        mock.get(f"/sensors/{tank.sensor_id}").respond(200, json=honest)
        clean = await sweep([tank], overlay=[ChaosEvent.from_row(r) for r in await active_chaos_events(chaos_store, now=later, kind=KIND_SENSOR)])

    assert clean.overlay_events == 0 and [r.value for r in clean.readings] == [16.4]
    assert triage_sweep(clean.readings) == []
    resolved = await reconcile(chaos_store, [], tick=5, run_id="e2e", read_subject_ids=[tank.sensor_id], now=later)
    assert resolved.counts == {"opened": 0, "ongoing": 0, "resolved": 1, "pending": 0, "dismissed": 0}
    assert resolved.resolved[0].subject_id == tank.sensor_id
