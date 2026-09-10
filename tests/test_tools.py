"""The tool layer: the sweep, and the triage truth table.

Two suites in one file, per `docs/Plan.md`:

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
from datetime import UTC, datetime

import httpx
import pytest
import respx
from structlog.testing import capture_logs

from src.agent.state import Incident
from src.tools.evidence import (
    SOP_FOR_CATEGORY,
    EvidencePacket,
    HistoryPoint,
    PastureContext,
    PastureRoster,
    assemble,
    load_sop,
    parse_history,
    siblings_for,
    slugify,
)
from src.tools.mcp_client import SensorRef
from src.tools.sensors import SensorReading, SweepError, parse_sensor_payload, read_sensor, sweep
from src.tools.triage import (
    ALL_CATEGORIES,
    CATEGORY_DEGRADED,
    CATEGORY_FAULT,
    CATEGORY_OFFLINE,
    CATEGORY_UNKNOWN_TYPE,
    RULES,
    triage_reading,
    triage_sweep,
)
from src.tools.triage import reset_warn_once as _reset
from src.utils.config import Settings

BASE = "https://sensor.test"
FROZEN_TS = "2026-09-10T14:30:00.000Z"

REF = SensorRef(sensor_id="alkali-flat-water", sensor_type="water-level", location="Alkali Flat", status="online")


@pytest.fixture(autouse=True)
def _fake_upstreams(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake SENSOR_API, so a stray unmocked request fails loudly instead of quietly
    reaching the deployed ranch."""
    settings = Settings(sensor_api=BASE, sweep_concurrency=4, upstream_timeout_ms=500, _env_file=None)
    monkeypatch.setattr("src.tools.sensors.get_settings", lambda: settings)
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
    assert [f.sensor_id for f in findings[:2]] == ["alkali-flat-water", "east-allotment-fence"]
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
        "sensor_id": "alkali-flat-water",
        "sensor_type": "water-level",
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


def test_every_category_water_feed_owns_has_an_sop_file_that_exists() -> None:
    """The SOPs are derived from `docs/sweetwater-ranch.md` and nothing else may source them.
    A missing file is silent in the packet and turns rule 4 of the brief into an invitation
    to invent a rule id."""
    for category in SOP_FOR_CATEGORY:
        name, text = load_sop(category)
        assert name and text.strip(), f"{category} maps to {SOP_FOR_CATEGORY[category]} and it did not load"


def test_a_category_with_no_sop_returns_nothing_rather_than_guessing_a_filename() -> None:
    assert load_sop("fence_down") == ("", "")


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
