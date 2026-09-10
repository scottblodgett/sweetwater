"""The triage truth table, and the rails around it.

What these prove: severity is deterministic and code-owned, every type is decided
explicitly, and nothing an unrecognized sensor sends can come out nominal. If one of
these fails, a threshold moved or a type fell through a branch that should not exist.
Loosening one to make it pass is the failure mode `tests/CLAUDE.md` is written against.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from src.tools.sensors import SensorReading
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

FROZEN_TS = "2026-09-10T14:30:00.000Z"


def reading(sensor_type: str, value: float | bool | None, *, sensor_id: str = "test-sensor", status: str = "online", location: str = "Alkali Flat") -> SensorReading:
    """Frozen timestamp, literal everything. No test asserts on a value it got from the clock."""
    return SensorReading(sensor_id=sensor_id, sensor_type=sensor_type, location=location, status=status, value=value, recorded_at=FROZEN_TS)


@pytest.fixture(autouse=True)
def _clean_warn_once() -> None:
    _reset()


# --------------------------------------------------------------------------- #
# the truth table: one row per type, per tier
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# the sensor is not the ranch
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# warn_once
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# the prose, graded rather than asserted
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# the sweep view
# --------------------------------------------------------------------------- #
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
    """`routing.py` is tested against `ALL_CATEGORIES`, so this set has to be complete or
    a new band produces incidents nobody owns."""
    emitted = {f.category for row in TRUTH_TABLE if row[3] for f in triage_reading(reading(row[0], row[1]))}
    assert emitted <= ALL_CATEGORIES
    assert {CATEGORY_OFFLINE, CATEGORY_DEGRADED, CATEGORY_FAULT, CATEGORY_UNKNOWN_TYPE} <= ALL_CATEGORIES
