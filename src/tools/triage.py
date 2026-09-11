"""Severity, in code, from per-type thresholds. No model, at any tier, ever assigns it.

Measured in a previous life of this project: a model handed a verdict and asked to
justify it agreed with the verdict 94 times out of 97; a model asked to author the
verdict got it right 2 times in 100. So the split is absolute. Code owns what a machine
consumes (severity, category, the incident key). The model owns only what a human
judges, and it never sees a threshold comparison.

**Where these numbers come from.** The ranch mission in `docs/sweetwater-ranch.md` says
which failures are losses, and the observed per-type distributions in `docs/state.md`
say what normal looks like on this ranch. They are NOT lifted from the upstream sensor
service's source: a constant copied out of the frozen upstream's internals is a value
nothing here can verify, and it fails silently the day the other side retunes it.

Two structural rules, both of which exist because of a specific way this goes wrong:

  * **Every critical-capable band carries a warning tier underneath it.** A type that
    can only be fine or on fire produces a feed that is quiet until it is too late.
  * **An unrecognized sensor type trips `warn_once` and still opens a finding.** There
    is no default branch. A new sensor type reading as nominal because nobody taught
    triage about it is the failure mode this module is most likely to have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from src.agent.state import SUBJECT_ANIMAL, Finding, Severity
from src.tools.herd import ANIMAL_STATUS_ACTIVE, AnimalRecord, CareTask, HerdSweepResult, Observation
from src.tools.sensors import SensorReading
from src.utils.logger import get_logger

log = get_logger(__name__)

Direction = Literal["low", "high"]

# Status strings that mean "this sensor is answering honestly". `unknown` is here on
# purpose: the catalog omits status for some entries, and absence of information about a
# sensor's health is not evidence of a fault. The value itself is the evidence.
HEALTHY_STATUSES = frozenset({"online", "ok", "active", "unknown", ""})

# Sensor-condition categories, independent of type. A sensor that cannot be trusted is a
# finding about the sensor, never a finding about the ranch.
CATEGORY_OFFLINE = "sensor_offline"
CATEGORY_DEGRADED = "sensor_degraded"
CATEGORY_FAULT = "sensor_fault"
CATEGORY_UNKNOWN_TYPE = "unknown_sensor_type"
SENSOR_CATEGORIES = frozenset({CATEGORY_OFFLINE, CATEGORY_DEGRADED, CATEGORY_FAULT, CATEGORY_UNKNOWN_TYPE})

# M7A. Animal categories, from the herd sweep. The key is `animal:category` exactly as it is
# `sensor:category`, so `cow-0903:deceased` is one incident for as long as she reads deceased.
CATEGORY_DECEASED = "deceased"
CATEGORY_INACTIVE = "inactive"
CATEGORY_OBSERVATION_HIGH = "observation_high"
CATEGORY_CARE_OVERDUE = "care_overdue"
ANIMAL_CATEGORIES = frozenset({CATEGORY_DECEASED, CATEGORY_INACTIVE, CATEGORY_OBSERVATION_HIGH, CATEGORY_CARE_OVERDUE})

#: `sold` is the one non-active status that is a ranch running normally. Named so the branch that
#: rules it out is explicit rather than a fall-through, and so a test can point at the row.
ANIMAL_STATUS_SOLD = "sold"
ANIMAL_STATUS_DECEASED = "deceased"
ANIMAL_STATUS_INACTIVE = "inactive"

#: The ranch has history. cow-0777 carries a `high` `mobility` observation from 2026-08-09 and the
#: Care API lists per animal with no since filter, so the first sweep would open one incident per
#: old note and the debounce would not help, because an old observation is stable across sweeps.
#: A high observation opens a finding only inside this window; outside it, it is history that the
#: packet still carries and a rancher still reads.
OBSERVATION_WINDOW = timedelta(hours=24)
#: A `high` observation of one of these types is an animal that needs a person today. The rest
#: (`behavior`, `appetite`, `appearance`, `general`) are a warning: real, and on a clock of days.
CRITICAL_OBSERVATION_TYPES = frozenset({"injury", "mobility"})
OBSERVATION_SEVERITY_HIGH = "high"


@dataclass(frozen=True)
class Band:
    """One direction of one concern, with a warning tier and an optional critical tier.

    `critical=None` states outright that this band cannot reach critical, which is a
    decision rather than an omission. An open gate is the case: gates are opened on
    purpose during a move, so an open gate is always a question for a human and never an
    automatic emergency.
    """

    category: str
    direction: Direction
    warning: float
    critical: float | None
    warning_consequence: str
    critical_consequence: str = ""

    def severity_for(self, value: float) -> tuple[Severity, float | None]:
        if self.direction == "low":
            if self.critical is not None and value <= self.critical:
                return "critical", self.critical
            if value <= self.warning:
                return "warning", self.warning
        else:
            if self.critical is not None and value >= self.critical:
                return "critical", self.critical
            if value >= self.warning:
                return "warning", self.warning
        return "nominal", None

    def consequence(self, severity: Severity) -> str:
        if severity == "critical" and self.critical_consequence:
            return self.critical_consequence
        return self.warning_consequence


@dataclass(frozen=True)
class TypeRule:
    """Everything triage knows about one of the 13 sensor types.

    `sane` is a physical-plausibility window, not a threshold: outside it the reading is
    a broken probe rather than weather. The live ranch has a temperature sensor that
    answers **-500 with `status: "online"`**, and -500 must never be triaged as a cold
    snap. Expressing that as "impossible for this quantity" rather than "not -500"
    catches the next sentinel value too.
    """

    unit: str
    noun: str
    sane: tuple[float, float]
    bands: tuple[Band, ...] = ()
    boolean: bool = False
    # Set when a type deliberately opens no threshold findings, so the reason lives next
    # to the decision instead of in a commit message.
    no_alarm_because: str = ""


RULES: dict[str, TypeRule] = {
    # --- production: nobody dies of thirst or hunger --------------------------
    "water-level": TypeRule(
        unit=" gal",
        noun="stock-tank level",
        sane=(0.0, 5000.0),
        bands=(
            Band(
                category="water_low",
                direction="low",
                warning=6.0,
                critical=2.0,
                warning_consequence="This tank is drawing down and will not carry the cattle on it through a hot afternoon.",
                critical_consequence="This tank is effectively dry. Cattle on it have no water until someone hauls or restarts the well.",
            ),
        ),
    ),
    "feed-bin-weight": TypeRule(
        unit=" lbs",
        noun="feed-bin weight",
        sane=(0.0, 20000.0),
        bands=(
            Band(
                category="feed_low",
                direction="low",
                warning=500.0,
                critical=250.0,
                warning_consequence="This bin is into its reserve and wants filling before the next weather.",
                critical_consequence="This bin is nearly empty. The cattle it feeds miss a feeding unless it is filled today.",
            ),
        ),
    ),
    # --- the cross-cutting basics --------------------------------------------
    "temperature": TypeRule(
        unit=" F",
        noun="air temperature",
        sane=(-80.0, 140.0),
        bands=(
            Band(
                category="freeze_risk",
                direction="low",
                warning=25.0,
                critical=10.0,
                warning_consequence="Open water at this location will start skinning over; tank heaters and floats are the exposure.",
                critical_consequence="Hard freeze. Stock tanks here go solid and the cattle on them are cut off from water, not merely cold.",
            ),
            Band(
                category="heat_stress",
                direction="high",
                warning=90.0,
                critical=100.0,
                warning_consequence="Water consumption at this location climbs well above the tank refill rate on a day like this.",
                critical_consequence="Heat stress range. Water demand roughly doubles and any tank already low will not keep up.",
            ),
        ),
    ),
    "humidity": TypeRule(
        unit="%",
        noun="relative humidity",
        sane=(0.0, 100.0),
        no_alarm_because="Humidity alone changes no work order. It matters paired with wind and temperature (fire weather, heat index), and pairing across sensing worlds is the supervisor's job, not a per-sensor threshold.",
    ),
    "wind-speed": TypeRule(
        unit=" mph",
        noun="wind speed",
        sane=(0.0, 200.0),
        bands=(
            Band(
                category="high_wind",
                direction="high",
                warning=45.0,
                critical=65.0,
                warning_consequence="Wind this hard is what puts limbs and tumbleweed into fence and drifts snow across the two-tracks.",
                critical_consequence="Wind at this speed takes down fence and loose roofing. Expect containment problems behind it.",
            ),
        ),
    ),
    "snow-depth": TypeRule(
        unit=" in",
        noun="snow depth",
        sane=(0.0, 300.0),
        bands=(
            Band(
                category="deep_snow",
                direction="high",
                warning=18.0,
                critical=30.0,
                warning_consequence="Cattle stop pawing to feed at this depth and start relying entirely on what is hauled to them.",
                critical_consequence="Feed access is gone and so is vehicle access to the far pastures. Reserves and routes both need checking.",
            ),
        ),
    ),
    "gate": TypeRule(
        unit="",
        noun="gate",
        sane=(0.0, 1.0),
        boolean=True,
        bands=(
            Band(
                category="gate_open",
                direction="high",
                warning=1.0,
                critical=None,  # deliberate: a gate is opened on purpose during a move
                warning_consequence="An open gate is a containment question. Either a crew left it that way on purpose or cattle are drifting where they should not be.",
            ),
        ),
    ),
    # --- infrastructure: containment, power, reserves -------------------------
    "fence-voltage": TypeRule(
        unit=" kV",
        noun="fence energizer voltage",
        sane=(0.0, 20.0),
        bands=(
            Band(
                category="fence_down",
                direction="low",
                warning=4.0,
                critical=2.0,
                warning_consequence="This energizer is pushing less than it should. A partial short somewhere on the line, and it holds cattle poorly.",
                critical_consequence="This fence is not hot. Miles of wire are holding nothing and the cattle behind it are effectively unfenced.",
            ),
        ),
    ),
    "battery-charge": TypeRule(
        unit="%",
        noun="battery charge",
        sane=(0.0, 100.0),
        bands=(
            Band(
                category="power_low",
                direction="low",
                warning=30.0,
                critical=15.0,
                warning_consequence="This site is running down its battery faster than the panel is replacing it.",
                critical_consequence="This site is about to go dark, and with it the pump or the telemetry it powers. A blind tank reads exactly like a full one.",
            ),
        ),
    ),
    "fuel-level": TypeRule(
        unit="%",
        noun="bulk fuel level",
        sane=(0.0, 100.0),
        bands=(
            Band(
                category="fuel_low",
                direction="low",
                warning=25.0,
                critical=10.0,
                warning_consequence="Time to schedule a fuel delivery rather than discover this on a cold morning.",
                critical_consequence="Nearly out. Generators, feed trucks, and the loader all draw on this tank, and a delivery is not a same-day thing out here.",
            ),
        ),
    ),
    "wellhead-pressure": TypeRule(
        unit=" psi",
        noun="wellhead pressure",
        sane=(0.0, 5000.0),
        bands=(
            Band(
                category="wellhead_overpressure",
                direction="high",
                warning=400.0,
                critical=450.0,
                warning_consequence="Pressure is running above its normal band. Worth eyes on before it becomes a relief event.",
                critical_consequence="Overpressure. This is the no-spill, no-fine side of the ranch and it wants a human on it now.",
            ),
            Band(
                category="wellhead_underpressure",
                direction="low",
                warning=200.0,
                critical=150.0,
                warning_consequence="Pressure is sagging below its normal band, which reads as a leak or a failing pump.",
                critical_consequence="Pressure has fallen out of its band entirely. A loss of containment looks exactly like this from here.",
            ),
        ),
    ),
    # --- environmental / compliance: keep the payments ------------------------
    "soil-moisture": TypeRule(
        unit="%",
        noun="soil moisture",
        sane=(0.0, 100.0),
        bands=(
            Band(
                category="range_dry",
                direction="low",
                warning=12.0,
                critical=5.0,
                warning_consequence="Range here is drying past what the grazing plan assumes. Stocking on this ground is worth a second look.",
                critical_consequence="This ground is bare-dry. Grazing it at planned stocking is how a conservation payment turns into a finding.",
            ),
        ),
    ),
    "stream-flow": TypeRule(
        unit=" cfs",
        noun="stream flow",
        sane=(0.0, 500.0),
        bands=(
            Band(
                category="stream_flow_low",
                direction="low",
                warning=2.0,
                critical=0.5,
                warning_consequence="Riparian flow is thin. This gauge is what the habitat program is scored on.",
                critical_consequence="This reach is nearly dry. Both the stewardship payment and the water it represents are on the line.",
            ),
        ),
    ),
}

# Every category triage can ever emit. `agent.ROUTES` is tested against this set, so a new
# band cannot be added without an owner: an incident nobody owns is silently dropped.
ALL_CATEGORIES: frozenset[str] = frozenset(b.category for r in RULES.values() for b in r.bands) | SENSOR_CATEGORIES | ANIMAL_CATEGORIES


# --------------------------------------------------------------------------- #
# warn_once
# --------------------------------------------------------------------------- #
@dataclass
class _WarnOnce:
    """Once per process, per unrecognized value. A tick that logs 160 identical warnings
    is a tick nobody reads, but a type that is never mentioned is a type nobody adds."""

    seen: set[str] = field(default_factory=set)

    def fire(self, key: str, event: str, **fields: object) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        log.warning(event, **fields)
        return True


_unknown_types = _WarnOnce()
_unknown_statuses = _WarnOnce()
_unknown_animal_statuses = _WarnOnce()


def reset_warn_once() -> None:
    """Tests only. Warn-once state is per process, and a test asserting the first
    warning cannot depend on which test ran before it."""
    _unknown_types.seen.clear()
    _unknown_statuses.seen.clear()
    _unknown_animal_statuses.seen.clear()


# --------------------------------------------------------------------------- #
# prose, written in code
# --------------------------------------------------------------------------- #
def format_value(value: float | bool | None, unit: str, *, boolean: bool = False) -> str:
    if value is None:
        return "no reading"
    if isinstance(value, bool) or boolean:
        return "open" if value else "closed"
    return f"{value:g}{unit}"


def _sentence(reading: SensorReading, rule: TypeRule, band: Band, severity: Severity, threshold: float) -> str:
    """Deterministic prose. Names the sensor, quotes the reading, states the consequence.

    Written here rather than by a model for the same reason severity is: a sentence is
    what a human acts on, and one generated from a threshold comparison has nothing in
    it a model could add except the risk of inventing a number. What a model is for is
    the assessment that comes after this, with the ranch's context in hand.
    """
    direction = "at or below" if band.direction == "low" else "at or above"
    observed = format_value(reading.value, rule.unit, boolean=rule.boolean)
    if rule.boolean:
        return f"{reading.location}: {rule.noun} {reading.sensor_id} reads {observed}. {band.consequence(severity)}"
    return f"{reading.location}: {rule.noun} reads {observed} on {reading.sensor_id}, {direction} the {severity} line of {threshold:g}{rule.unit}. {band.consequence(severity)}"


# --------------------------------------------------------------------------- #
# triage
# --------------------------------------------------------------------------- #
def triage_reading(reading: SensorReading) -> list[Finding]:
    """Every finding this one reading supports. Empty list means nominal.

    A single sensor can produce more than one finding, and that is on purpose: a
    degraded probe reporting a dry tank is two different work orders for two different
    people. The incident key is `sensor:category`, so they stay separate all the way
    through the store.
    """
    findings: list[Finding] = []
    status = (reading.status or "").lower()
    rule = RULES.get(reading.sensor_type)

    def add(category: str, severity: Severity, summary: str, threshold: float | None = None) -> None:
        findings.append(
            Finding(
                subject_id=reading.sensor_id,
                subject_type=reading.sensor_type,
                location=reading.location,
                category=category,
                severity=severity,
                value=reading.value,
                unit=rule.unit if rule else "",
                threshold=threshold,
                observed_at=reading.recorded_at,
                summary=summary,
            )
        )

    noun = rule.noun if rule else reading.sensor_type

    # 1. Can this sensor be believed at all? A finding about the sensor is never a
    #    finding about the ranch, and conflating them sends the wrong person out.
    if status == "offline":
        add(CATEGORY_OFFLINE, "warning", f"{reading.location}: {noun} {reading.sensor_id} is dark, reporting offline with no reading. Nothing is known about this location until someone is on site.")
        return findings  # an offline sensor has no value to threshold
    if status == "degraded":
        add(CATEGORY_DEGRADED, "warning", f"{reading.location}: {noun} {reading.sensor_id} reports degraded. Its readings still arrive and are no longer trustworthy, which is the harder of the two failures to notice.")
    elif status not in HEALTHY_STATUSES:
        # Not nominal, ever. An unrecognized status that fell through to "fine" is the
        # same failure as an unrecognized type reading as nominal.
        _unknown_statuses.fire(status, "unknown_sensor_status", status=status, sensor_id=reading.sensor_id, hint="treated as degraded; add it to triage.py explicitly")
        add(CATEGORY_DEGRADED, "warning", f"{reading.location}: {noun} {reading.sensor_id} reports an unrecognized status of \"{reading.status}\". Treated as degraded rather than trusted.")

    # 2. A 200 that carried no value is a broken sensor, not a calm one.
    if reading.value is None:
        add(CATEGORY_FAULT, "warning", f"{reading.location}: {noun} {reading.sensor_id} answered without a reading while reporting status \"{reading.status}\". A sensor that returns nothing looks identical to one with nothing to report.")
        return findings

    # 3. No default branch. An unknown type opens a finding every tick and warns once.
    if rule is None:
        _unknown_types.fire(reading.sensor_type, "unknown_sensor_type", sensor_type=reading.sensor_type, sensor_id=reading.sensor_id, hint="add a TypeRule to src/tools/triage.py; it is being reported, not ignored")
        add(CATEGORY_UNKNOWN_TYPE, "warning", f"{reading.location}: {reading.sensor_id} reports type \"{reading.sensor_type}\", which triage has no thresholds for. Its reading of {reading.value} is unjudged, so this is being surfaced rather than passed as nominal.")
        return findings

    # 4. Shape before magnitude. `gate` is boolean and everything else is a number, so a
    #    number on a gate or a boolean anywhere else is a broken feed, and comparing it
    #    against a threshold would produce a confident, wrong severity.
    is_bool = isinstance(reading.value, bool)
    if is_bool != rule.boolean:
        expected = "a boolean" if rule.boolean else "a number"
        add(CATEGORY_FAULT, "warning", f"{reading.location}: {noun} {reading.sensor_id} returned {reading.value!r} where {expected} is expected for a {reading.sensor_type}. The feed shape is wrong, so no threshold was applied.")
        return findings

    numeric = float(reading.value)

    # 5. Physically impossible means broken probe, never extreme weather. The live ranch
    #    has a temperature sensor answering -500 with status "online".
    low, high = rule.sane
    if not low <= numeric <= high:
        add(CATEGORY_FAULT, "warning", f"{reading.location}: {noun} {reading.sensor_id} reads {numeric:g}{rule.unit}, outside anything physically possible for this quantity ({low:g} to {high:g}{rule.unit}). This is a failed probe and was not triaged as a real reading.")
        return findings

    # 6. Thresholds, per band, in the order they are declared.
    for band in rule.bands:
        severity, threshold = band.severity_for(numeric)
        if severity == "nominal" or threshold is None:
            continue
        add(band.category, severity, _sentence(reading, rule, band, severity, threshold), threshold)

    return findings


def _ranked(findings: list[Finding]) -> list[Finding]:
    order = {"critical": 0, "warning": 1, "nominal": 2}
    return sorted(findings, key=lambda f: (order.get(f.severity, 3), f.subject_id, f.category))


def triage_sweep(readings: tuple[SensorReading, ...] | list[SensorReading], *, herd: HerdSweepResult | None = None, now: datetime | None = None) -> list[Finding]:
    """Findings across a whole sweep, worst first, then by subject for a stable order.

    Sorted so the tick line and any human reading the log see the critical items first.
    Stable ordering also means two runs over the same sweep produce the same list, which
    is what lets the reconciliation tests assert on it. From M7A the herd's findings are
    ranked into the same list: a dead cow and a dry tank are one tick's work, not two lists.
    """
    findings = [f for reading in readings for f in triage_reading(reading)]
    if herd is not None:
        findings.extend(triage_herd(herd, now=now))
    return _ranked(findings)


# --------------------------------------------------------------------------- #
# the herd. M7A: animal categories, in code, same ownership rule
# --------------------------------------------------------------------------- #
def _who(record: AnimalRecord | None, animal_id: str) -> str:
    if record is None:
        return f"{animal_id} (record unavailable this tick)"
    name = f", tag {record.name}" if record.name else ""
    return f"{record.species} {animal_id}{name}"


def _where(record: AnimalRecord | None) -> str:
    return record.pasture_id if record and record.pasture_id else "pasture unrecorded"


def _newest_high_observation(observations: tuple[Observation, ...], *, since: datetime) -> Observation | None:
    """The newest `high` observation inside the window, or `None`. Anything without a parseable
    timestamp is history that cannot be dated and so cannot be inside the window."""
    for obs in observations:  # newest first
        if obs.severity != OBSERVATION_SEVERITY_HIGH:
            continue
        observed = obs.observed
        if observed is not None and observed >= since:
            return obs
    return None


def _animal_finding(record: AnimalRecord | None, animal_id: str, *, category: str, severity: Severity, summary: str, observed_at: str | None) -> Finding:
    return Finding(subject_id=animal_id, subject_type=SUBJECT_ANIMAL, location=record.pasture_id if record else "", category=category, severity=severity, observed_at=observed_at, summary=summary)


def triage_herd(herd: HerdSweepResult, *, now: datetime | None = None) -> list[Finding]:
    """Every animal finding this sweep supports. Empty list means the herd read normally.

    The truth table, in code, one row per animal category:

      * `deceased`  critical. Under M7's predicate this escalates to Tier 2 on its own, which is
                    where a dead cow belongs. Not tuned down to save a call.
      * `inactive`  warning. On the books, not sold, not dead, not standing in a pasture as far as
                    the record knows: unaccounted for, which the ranch mission names as a loss.
      * `sold`      **not a finding.** Ruled out here, explicitly. One non-active status is a ranch
                    running normally, and a work order about a cow that went to the sale barn on
                    purpose teaches the crew to ignore the category.
      * `observation_high`  a `high` observation inside `OBSERVATION_WINDOW`: critical for `injury`
                    and `mobility`, warning otherwise. One per animal, the newest. **Not opened on an
                    animal that already has a status finding**: the observation rides in that
                    packet, and two incidents about one dead cow is one work order too many.
      * `care_overdue`  warning. A pending care task past its `dueAt`. One per animal, the oldest.
                    The clock is the only threshold, and an overdue task is a present state rather
                    than history, so no window applies.

    A status outside the Farm API's enum cannot arrive (the sweep reads by status), so the branch
    for it warns once and opens nothing rather than inventing a category.
    """
    stamp = now or datetime.now(UTC)
    since = stamp - OBSERVATION_WINDOW
    findings: list[Finding] = []
    status_flagged: set[str] = set()

    for record in herd.animals:
        who, where = _who(record, record.animal_id), _where(record)
        status = record.status.lower()
        if status == ANIMAL_STATUS_DECEASED:
            status_flagged.add(record.animal_id)
            findings.append(_animal_finding(record, record.animal_id, category=CATEGORY_DECEASED, severity="critical", observed_at=record.updated_at or None, summary=f"{where}: {who} is recorded deceased on the Farm API. A dead animal is a loss to record and a cause to find, and the rest of the group in that pasture is the next question."))
        elif status == ANIMAL_STATUS_INACTIVE:
            status_flagged.add(record.animal_id)
            findings.append(_animal_finding(record, record.animal_id, category=CATEGORY_INACTIVE, severity="warning", observed_at=record.updated_at or None, summary=f"{where}: {who} is recorded inactive, which is not sold and not deceased. This animal is on the books and not accounted for."))
        elif status == ANIMAL_STATUS_SOLD:
            # A ranch running normally. Named as a branch rather than left to fall through, so the
            # decision is visible here and pointed at by a test.
            continue
        elif status == ANIMAL_STATUS_ACTIVE:
            pass  # a care-task animal, or a cohort animal restored; its observations and tasks decide
        else:
            _unknown_animal_statuses.fire(status, "unknown_animal_status", status=record.status, animal_id=record.animal_id, hint="not one of the Farm API's four statuses; nothing opened, add it to triage.py explicitly")

    for record in herd.animals:
        if record.animal_id in status_flagged:
            continue
        newest = _newest_high_observation(herd.observations_for(record.animal_id), since=since)
        if newest is None:
            continue
        severity: Severity = "critical" if newest.type in CRITICAL_OBSERVATION_TYPES else "warning"
        who, where = _who(record, record.animal_id), _where(record)
        consequence = "This animal needs a person at it today." if severity == "critical" else "Real, and on a clock of days rather than hours."
        findings.append(_animal_finding(record, record.animal_id, category=CATEGORY_OBSERVATION_HIGH, severity=severity, observed_at=newest.observed_at, summary=f'{where}: {who} carries a high-severity {newest.type} observation from {newest.observed_at}: "{newest.note}". {consequence}'))

    overdue_by_animal: dict[str, CareTask] = {}
    for task in herd.care_tasks:
        due = task.due
        if task.status != "pending" or due is None or due >= stamp:
            continue
        prior = overdue_by_animal.get(task.animal_id)
        if prior is None or (prior.due or stamp) > due:
            overdue_by_animal[task.animal_id] = task
    for animal_id, task in sorted(overdue_by_animal.items()):
        known = herd.record(animal_id)
        days = (stamp - (task.due or stamp)).days
        findings.append(_animal_finding(known, animal_id, category=CATEGORY_CARE_OVERDUE, severity="warning", observed_at=task.due_at, summary=f'{_where(known)}: care task {task.task_id} for {_who(known, animal_id)}, "{task.title}", was due {task.due_at} and is still pending, {days} day{"s" if days != 1 else ""} overdue. A promise the record shows was not kept.'))

    return _ranked(findings)
