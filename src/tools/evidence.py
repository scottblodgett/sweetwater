"""Assemble the evidence packet in code, so a model judges one page.

The cheap path is a cheaper architecture, not just a cheaper model. The free pass already
knows exactly which sensor is bad and holds the ranch map, so telling a sub-agent to go
find out what is wrong pays a model to rediscover what code already knows.

The packet: the incident, that sensor's recent history, its sibling sensors at the same
location, the animals in that pasture, and the relevant SOP. One call, no tool loop. The
model is not navigating, it is judging a page, which is demonstrably the thing local models
are good at and open-ended tool loops are how a confident false all-clear gets produced.

**What this costs per tick, measured rather than assumed.** Three shapes, and two of them
are free:

  * **Siblings cost nothing.** The sweep already read all 160 sensors this tick, so the
    map says who shares the location and the sweep says what they read. Re-reading them
    would be actively worse: `GET /sensors/:id` synthesizes a fresh value on every call, so
    the sibling in the packet would disagree with the sibling triage judged, and a human
    comparing the two would be reconciling two different afternoons.
  * **The pasture roster costs one call per tick, flat.** `GET /pastures?limit=50` returns
    all 18 pastures with their `animalIds` inline, so head count comes free and the cost
    does not grow with incident count. Same map-not-paging shape as the sensor catalog, and
    it arrives here by the same necessity.
  * **History costs one call per newly-opened incident**, and it is the only term that
    scales. It is also the term worth paying for: a single value is a snapshot, and two
    hours of it is the difference between "this tank is low" and "this tank lost four
    gallons an hour and will be empty before dark."

So a tick that opens 7 to 11 water incidents makes 169 to 173 upstream calls against the
161 the free pass already made. Bounded by `SWEEP_CONCURRENCY` like everything else.

**Animals live on the farm API, not the care API.** `GET /pastures` and `GET /animals` are
both on `FARM_API`; `CARE_API` serves observations and care tasks. Verified over the wire on
2026-09-10, because the natural guess is wrong and costs a 404 to find out.

One thing that looks like a boundary violation and is not: this packet hands `water_feed`
pasture and animal context that its own tool allowlist (`src/tools/allowlists.py`) does not
include. That is the design. Code assembles the page; the allowlist governs what the agent
may go touch on its own. An agent that could fetch this itself would be navigating.

**The one place that precedent is refused: a cow's packet carries no sensor reading.** M7A. It
would be easy to put the pasture's tank level in the dead cow's page, and the precedent above
says a code-assembled packet may cross an allowlist. Do not. `herd_health` sees the dead cow and
`water_feed` sees the dry tank and only the supervisor can fuse them; put the tank in the cow's
packet and fusion stops being tested. The cow's packet is the record, its pasture, its recent
observations, its open care tasks, and its herd-mates in that pasture. Nothing that starts with a
number from a sensor. A test renders one against a sweep full of readings and asserts none leaked.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from src.agent.state import Incident
from src.tools.herd import (
    PASTURE_LIMIT,
    AnimalRecord,
    CareTask,
    HerdSweepResult,
    Observation,
    PastureContext,
    PastureRoster,
    fetch_pasture_roster,
    parse_pastures,
    slugify,
)
from src.tools.mcp_client import RanchMap
from src.tools.sensors import SensorReading
from src.tools.triage import RULES, format_value
from src.utils.config import REPO_ROOT, get_settings
from src.utils.helpers import gather_bounded, upstream_client
from src.utils.logger import get_logger

log = get_logger(__name__)

KNOWLEDGE_BASE = REPO_ROOT / "data" / "knowledge_base"

#: Two hours at one reading every ten minutes. Long enough to show a trend and short
#: enough that a human reads the whole thing, which is the actual constraint on a page
#: somebody acts on at 2am.
HISTORY_LIMIT = 12

#: How many of an animal's observations the page carries, newest first. The ranch's history is
#: the point here: triage windows a `high` note to 24 hours, but a vet reading about cow-0777
#: wants the August mobility note on the same page as today's.
ANIMAL_OBSERVATION_LIMIT = 10

#: Category to SOP file. Explicit rather than derived from the category name, because a
#: missing SOP must read as "no rule exists for this yet" and not as a filename typo that
#: silently returns nothing. All 18 categories are covered as of M3, so a
#: `packets_without_sop` warning now means a **new** category arrived without a rule.
#:
#: **Six files rather than four, and the split is a cost decision as much as an editorial
#: one.** `src/tools/CLAUDE.md` measured the SOP as the majority of a packet's input tokens,
#: so every rule in the file a packet carries is billed whether or not it applies. One
#: `infrastructure.md` covering fences, gates, batteries, fuel, wind, the wells, and sensor
#: health would be roughly 350 lines, and an open gate would pay for the H2S approach rule
#: every tick. It also reads worse: the standing orders a person needs for a gate are three
#: rules, not thirty. So `infrastructure` is one agent reading three files depending on what
#: broke, which is exactly the "one file per sensing world" shape the canon already
#: describes - the wells are the safety and regulatory world, and the instruments are their
#: own maintenance world.
SOP_FOR_CATEGORY: dict[str, str] = {
    # production: nobody dies of thirst or hunger
    "water_low": "water.md",
    "freeze_risk": "water.md",
    "heat_stress": "water.md",
    "feed_low": "feed.md",
    "deep_snow": "feed.md",
    # the plant that holds cattle, powers a remote site, and fuels the trucks
    "fence_down": "infrastructure.md",
    "gate_open": "infrastructure.md",
    "power_low": "infrastructure.md",
    "fuel_low": "infrastructure.md",
    "high_wind": "infrastructure.md",
    # safety and regulatory: no spill, no fault, no fine
    "wellhead_overpressure": "wellhead.md",
    "wellhead_underpressure": "wellhead.md",
    # the instruments themselves. Every sensing world's boxes, worked by one agent
    "sensor_offline": "sensors.md",
    "sensor_degraded": "sensors.md",
    "sensor_fault": "sensors.md",
    "unknown_sensor_type": "sensors.md",
    # environmental: keep the payments, prove the stewardship
    "range_dry": "compliance.md",
    "stream_flow_low": "compliance.md",
    # the animals. M7A: the herd sweep's four categories, one file, `herd_health` only
    "deceased": "herd.md",
    "inactive": "herd.md",
    "observation_high": "herd.md",
    "care_overdue": "herd.md",
}

#: M10, `docs/open-issues.md` #12. The SOP files whose rules ask about facts that live on other
#: sensors, and which sensor types answer them. `feed.md` says weather is what turns a low bin from
#: routine into urgent (FEED-02) and asks for the bulk fuel behind the fill (FEED-01). Neither was
#: on the feed page, so the local judge honestly said `insufficient_information` and Opus wrote
#: around the gap, and the cheap path paid twice. The sweep already read every one of these
#: sensors this tick, so the page carries the nearest of each at **zero HTTP**. Keyed by SOP file
#: rather than by category because the rules that ask are in the file, and a category that joins
#: the file inherits the ask.
CONDITIONS_FOR_SOP: dict[str, tuple[str, ...]] = {
    "feed.md": ("wind-speed", "temperature", "snow-depth", "fuel-level"),
}


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HistoryPoint:
    recorded_at: str
    value: float | bool | None


@dataclass(frozen=True)
class SiblingReading:
    """One other sensor at the same location, from this tick's sweep. Never re-read."""

    sensor_id: str
    sensor_type: str
    status: str
    value: float | bool | None

    def render(self) -> str:
        rule = RULES.get(self.sensor_type)
        unit = rule.unit if rule else ""
        noun = rule.noun if rule else self.sensor_type
        shown = format_value(self.value, unit, boolean=bool(rule and rule.boolean))
        suffix = "" if self.status.lower() in {"online", "ok", "active", ""} else f", status {self.status}"
        return f"  - {noun} ({self.sensor_id}): {shown}{suffix}"


@dataclass(frozen=True)
class ConditionReading:
    """M10. One reading from elsewhere on the ranch that a standing order asks about: the nearest
    wind, temperature, snow, or yard fuel sensor to the incident, from this tick's sweep. Never
    re-read, for the same reason a sibling is not: a second draw would disagree with the first.

    `same_location` says whether it shares the incident's location; a map distance in grid units
    means nothing to a foreman, so the page says "here" or names the place and leaves it there.
    """

    sensor_type: str
    sensor_id: str
    location: str
    status: str
    value: float | bool | None
    same_location: bool = False

    def render(self) -> str:
        rule = RULES.get(self.sensor_type)
        unit = rule.unit if rule else ""
        noun = rule.noun if rule else self.sensor_type
        shown = format_value(self.value, unit, boolean=bool(rule and rule.boolean))
        where = "here" if self.same_location else f"at {self.location}"
        suffix = "" if self.status.lower() in {"online", "ok", "active", ""} else f", status {self.status}"
        return f"  - {noun} ({self.sensor_id}, {where}): {shown}{suffix}"


@dataclass(frozen=True)
class AnimalContext:
    """What the herd sweep already knows about one animal. Zero extra HTTP: every field here was
    read in the free pass, so a cow's packet costs the tick nothing the sweep did not already pay.

    `record` is `None` when the Farm read for this animal failed and the finding came off a care
    task alone; the page says so rather than guessing a species.
    """

    record: AnimalRecord | None
    record_note: str = ""
    observations: tuple[Observation, ...] = ()
    observations_note: str = ""
    care_tasks: tuple[CareTask, ...] = ()
    herd_mates: tuple[AnimalRecord, ...] = ()

    def render(self, *, animal_id: str) -> list[str]:
        lines: list[str] = []
        lines.append("## The animal, as the Farm API records it")
        lines.append("")
        if self.record:
            r = self.record
            lines.append(f"- id: {r.animal_id}" + (f", tag {r.name}" if r.name else ""))
            lines.append(f"- species: {r.species}" + (f", {r.sex}" if r.sex else ""))
            lines.append(f"- status: {r.status}" + (f", last updated {r.updated_at}" if r.updated_at else ""))
            lines.append(f"- pasture: {r.pasture_id or 'none recorded'}" + (f", shelter {r.shelter_id}" if r.shelter_id else ""))
        else:
            lines.append(f"- id: {animal_id}")
            lines.append(f"  (record unavailable: {self.record_note or 'not read this tick'}. Species, status, and pasture are unknown; say so.)")
        lines.append("")

        lines.append(f"## This animal's last {ANIMAL_OBSERVATION_LIMIT} observations on the Care API, newest first")
        lines.append("")
        if self.observations:
            lines.append("  (Quote these as written. An observation is a person, at an animal, on a date; there is no re-reading it.)")
            for obs in self.observations[:ANIMAL_OBSERVATION_LIMIT]:
                lines.append(f'  - {obs.observed_at}: [{obs.severity}] {obs.type}: "{obs.note}"')
        else:
            lines.append(f"  (none: {self.observations_note or 'no observation has ever been recorded for this animal'})")
        lines.append("")

        lines.append("## Open care tasks for this animal")
        lines.append("")
        if self.care_tasks:
            for task in self.care_tasks:
                lines.append(f'  - {task.task_id}: "{task.title}", due {task.due_at}, status {task.status}' + (f", notes: {task.notes}" if task.notes else ""))
        else:
            lines.append("  (none pending)")
        lines.append("")
        return lines


@dataclass(frozen=True)
class EvidencePacket:
    """One page. Everything a human on shift would need, and nothing they would have to go get.

    Absences are stated, never omitted. `history_note` carrying "unavailable" is a fact the
    model has to work around; an empty history list with no explanation is one it will fill
    in for itself, which is exactly the invented premise the brief forbids.

    Two pages live here from M7A, chosen by `incident.is_animal`. A sensor page carries history
    and siblings; an animal page carries `animal` and **never** history or siblings, which is the
    line described at the top of this module.
    """

    incident: Incident
    history: tuple[HistoryPoint, ...] = ()
    history_note: str = ""
    siblings: tuple[SiblingReading, ...] = ()
    pasture: PastureContext | None = None
    pasture_note: str = ""
    sop_name: str = ""
    sop_text: str = ""
    coordinates: dict[str, float] | None = None
    animal: AnimalContext | None = None
    #: M10 (#12). The nearest reading of each type the SOP asks about, and the types nobody
    #: answered for. Populated only for the SOP files in `CONDITIONS_FOR_SOP`; a water page never
    #: carries the block, and a test says so.
    conditions: tuple[ConditionReading, ...] = ()
    conditions_missing: tuple[str, ...] = ()

    def render(self) -> str:
        """The page, as the model receives it. Also what a human reads to check the model.

        Deliberately one function: a packet whose printed form differs from the prompted
        form makes every debugging session a guess. If it reads badly here it reads badly
        to the model.
        """
        if self.incident.is_animal:
            return self._render_animal()
        inc = self.incident
        rule = RULES.get(inc.subject_type)
        unit = rule.unit if rule else inc.unit
        noun = rule.noun if rule else inc.subject_type
        where = f"{inc.location}" + (f" at map point {self.coordinates.get('x')},{self.coordinates.get('y')}" if self.coordinates else "")

        lines: list[str] = []
        lines.append("## The incident, as triage ranked it in code")
        lines.append("")
        lines.append(f"- severity: {inc.severity.upper()} (already decided; not yours to change)")
        lines.append(f"- category: {inc.category}")
        lines.append(f"- sensor: {inc.subject_id}, a {noun} sensor")
        lines.append(f"- location: {where}")
        lines.append(f"- reading: {inc.last_value or 'no reading'}" + (f", against a {inc.severity} line of {inc.threshold:g}{unit}" if inc.threshold is not None else ""))
        lines.append(f"- triage said: {inc.summary}")
        lines.append(f"- history of this incident: seen {inc.occurrences} time(s), first at {inc.first_seen_at.isoformat()}, still {inc.status}")
        lines.append("")

        lines.append(f"## This sensor's last {HISTORY_LIMIT} readings, newest first")
        lines.append("")
        if self.history:
            # The seam has to be stated, not smoothed over. `GET /sensors/:id/readings`
            # synthesizes its series fresh and unanchored to the single value `GET
            # /sensors/:id` returned, so the newest history point is NOT the reading triage
            # judged and is sometimes further from the line in either direction. Unlabeled,
            # a model reads the top of this list as "now" and quotes a number no human ever
            # saw. Labeled, the list is still worth its one HTTP call: what it establishes
            # is the operating range this sensor lives in, which is the part a single value
            # cannot show.
            lines.append("  (This series is generated independently of the reading above and does NOT contain it. Use it for the range and the trend; the authoritative current value is the triaged reading.)")
            for point in self.history:
                lines.append(f"  - {point.recorded_at}: {format_value(point.value, unit, boolean=bool(rule and rule.boolean))}")
        else:
            lines.append(f"  (none available: {self.history_note or 'not fetched'})")
        lines.append("")

        lines.append(f"## Other sensors at {inc.location}, read in this same sweep")
        lines.append("")
        if self.siblings:
            lines.extend(s.render() for s in self.siblings)
        else:
            lines.append("  (none: this sensor is the only one at this location)")
        lines.append("")

        if self.conditions or self.conditions_missing:
            lines.append("## Conditions now, read in this same sweep")
            lines.append("")
            lines.append("  (The standing orders below ask about the weather and the yard fuel. These are the nearest such sensors to this one on the ranch map, read in the same sweep as the incident. None of them is a forecast: each is one current reading, so say what it shows now and treat what is coming as unknown.)")
            lines.extend(c.render() for c in self.conditions)
            for sensor_type in self.conditions_missing:
                rule = RULES.get(sensor_type)
                lines.append(f"  - {rule.noun if rule else sensor_type}: no {sensor_type} sensor answered this sweep")
            lines.append("")

        lines.append("## What is standing behind it")
        lines.append("")
        lines.append(f"  {self.pasture.render()}" if self.pasture else f"  ({self.pasture_note or 'no pasture is mapped to this location'})")
        lines.append("")

        lines.extend(self._render_sop())
        return "\n".join(lines)

    def _render_sop(self) -> list[str]:
        return [f"## The standing orders that apply ({self.sop_name or 'none on file'})", "", self.sop_text.strip() if self.sop_text else "  (no SOP exists for this category yet. Say so rather than citing one.)"]

    def _render_animal(self) -> str:
        """The cow's page. The record, its pasture, its observations, its open care tasks, its
        herd-mates in that pasture, the SOP. **No sensor reading, ever**, see the module docstring."""
        inc = self.incident
        animal = self.animal or AnimalContext(record=None, record_note="the herd sweep carried nothing for this animal")
        lines: list[str] = []
        lines.append("## The incident, as triage ranked it in code")
        lines.append("")
        lines.append(f"- severity: {inc.severity.upper()} (already decided; not yours to change)")
        lines.append(f"- category: {inc.category}")
        lines.append(f"- animal: {inc.subject_id}")
        lines.append(f"- pasture: {inc.location or 'unrecorded'}")
        lines.append(f"- triage said: {inc.summary}")
        lines.append(f"- history of this incident: seen {inc.occurrences} time(s), first at {inc.first_seen_at.isoformat()}, still {inc.status}")
        lines.append("")
        lines.extend(animal.render(animal_id=inc.subject_id))

        lines.append("## The pasture it stands in")
        lines.append("")
        lines.append(f"  {self.pasture.render()}" if self.pasture else f"  ({self.pasture_note or 'no pasture is recorded for this animal'})")
        if animal.herd_mates:
            lines.append("  Other animals in this pasture whose state changed this sweep:")
            lines.extend(f"  - {m.species} {m.animal_id}: status {m.status}" for m in animal.herd_mates)
        elif self.pasture:
            lines.append("  No other animal in this pasture read as anything but active this sweep.")
        lines.append("  (No sensor reading is on this page by design. Water, feed, fence, and weather at this pasture are another agent's, and the supervisor joins the two.)")
        lines.append("")
        lines.extend(self._render_sop())
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# the SOP
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def load_sop(category: str) -> tuple[str, str]:
    """`(filename, text)` for a category, or `("", "")` when no SOP covers it yet.

    Loaded whole, not retrieved over. Each sensing world's rule set is a handful of rules,
    so the whole file beats the cleverness of chunking it, and `src/models/embeddings.py`
    exists as the seam for the day that stops being true. Cached because the files do not
    change inside a run and a tick opening 11 incidents would otherwise read the same file
    11 times.
    """
    filename = SOP_FOR_CATEGORY.get(category, "")
    if not filename:
        return "", ""
    path = KNOWLEDGE_BASE / filename
    if not path.exists():
        # Loud, because a packet that silently loses its SOP still reads complete, and the
        # brief tells the model to cite a rule it would then have to invent.
        log.error("sop_missing", category=category, expected=str(path), hint="the SOP files are derived from docs/sweetwater-ranch.md and nothing else")
        return "", ""
    return filename, path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the pasture roster lives in `herd.py` from M7A (the herd sweep reads it as its catalog) and is
# re-exported here because it is still this module's context for a sensor packet
# --------------------------------------------------------------------------- #
__all__ = ["PASTURE_LIMIT", "PastureContext", "PastureRoster", "fetch_pasture_roster", "parse_pastures", "slugify"]


# --------------------------------------------------------------------------- #
# history: the one term that scales with incident count
# --------------------------------------------------------------------------- #
def parse_history(payload: Any) -> tuple[HistoryPoint, ...]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return ()
    points: list[HistoryPoint] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        value = raw.get("value")
        # bool before number, same trap as `sensors.py`: `isinstance(True, int)` is True, so
        # checking for a number first turns every gate reading into the number 1.
        parsed: float | bool | None = value if isinstance(value, bool) else float(value) if isinstance(value, (int, float)) else None
        points.append(HistoryPoint(recorded_at=str(raw.get("recordedAt") or raw.get("recorded_at") or ""), value=parsed))
    return tuple(points)


async def fetch_history(client: httpx.AsyncClient, sensor_id: str, *, limit: int = HISTORY_LIMIT) -> tuple[tuple[HistoryPoint, ...], str]:
    """`(points, note)`. The note is empty on success and says why on failure.

    No retry here, same as every other read in this layer: attempts and deadline belong to
    the caller. A packet with a stated absence beats a packet that spun.
    """
    try:
        response = await client.get(f"/sensors/{sensor_id}/readings", params={"limit": limit})
    except httpx.HTTPError as exc:
        return (), f"{type(exc).__name__}"
    if response.status_code >= 400:
        return (), f"HTTP {response.status_code}"
    try:
        points = parse_history(response.json())
    except ValueError:
        return (), "upstream returned non-JSON"
    return points, "" if points else "upstream returned an empty history"


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def siblings_for(location: str, sensor_id: str, readings: tuple[SensorReading, ...] | list[SensorReading]) -> tuple[SiblingReading, ...]:
    """This tick's other readings at the same location. Zero HTTP calls, on purpose."""
    return tuple(
        SiblingReading(sensor_id=r.sensor_id, sensor_type=r.sensor_type, status=r.status, value=r.value)
        for r in sorted(readings, key=lambda r: (r.sensor_type, r.sensor_id))
        if r.location == location and r.sensor_id != sensor_id
    )


def _map_distance(a: dict[str, float] | None, b: dict[str, float] | None) -> float | None:
    """Straight-line distance on the ranch's stylized 0 to 100 grid, or `None` when either point is unmapped."""
    if not a or not b:
        return None
    try:
        return float(((float(a["x"]) - float(b["x"])) ** 2 + (float(a["y"]) - float(b["y"])) ** 2) ** 0.5)
    except (KeyError, TypeError, ValueError):
        return None


def conditions_for(incident: Incident, readings: tuple[SensorReading, ...] | list[SensorReading], ranch_map: RanchMap | None, *, sensor_types: tuple[str, ...]) -> tuple[tuple[ConditionReading, ...], tuple[str, ...]]:
    """M10 (#12). `(nearest reading per type, types nobody answered for)`. Zero HTTP, on purpose.

    A live value beats a dark sensor first, because a "no reading" here is not a fact the order can
    use and a dark tank in the room is not more useful than a diesel gauge across the yard. Then a
    sensor at the incident's own location wins, then the nearest by map coordinates when both ends
    are mapped (the ranch has two wind sensors and one snow gauge, and "the" reading is the one
    closest to this bin), then id, which makes the choice deterministic without a map. The page says
    "here" or "at <location>", never a distance.
    """
    here = ranch_map.get(incident.subject_id) if ranch_map else None
    origin = here.coordinates if here else None
    found: list[ConditionReading] = []
    missing: list[str] = []
    for sensor_type in sensor_types:
        candidates = [r for r in readings if r.sensor_type == sensor_type and r.sensor_id != incident.subject_id]
        if not candidates:
            missing.append(sensor_type)
            continue

        def rank(r: SensorReading) -> tuple[int, int, float, str]:
            ref = ranch_map.get(r.sensor_id) if ranch_map else None
            distance = _map_distance(origin, ref.coordinates if ref else None)
            return (0 if r.value is not None else 1, 0 if r.location == incident.location else 1, distance if distance is not None else float("inf"), r.sensor_id)

        best = min(candidates, key=rank)
        found.append(ConditionReading(sensor_type=sensor_type, sensor_id=best.sensor_id, location=best.location, status=best.status, value=best.value, same_location=best.location == incident.location))
    return tuple(found), tuple(missing)


def animal_context(incident: Incident, herd: HerdSweepResult | None) -> AnimalContext:
    """The cow's page from what the sweep already read. No HTTP here, and none allowed."""
    if herd is None:
        return AnimalContext(record=None, record_note="the herd sweep did not run this tick")
    record = herd.record(incident.subject_id)
    failed = next((e for e in herd.errors if e.subject == incident.subject_id), None)
    observations = herd.observations_for(incident.subject_id)
    note = ""
    if not observations:
        note = f"the Care API read for this animal failed ({failed.message})" if failed else ("the Care API was not read this tick" if herd.failure.startswith("care") else "")
    return AnimalContext(
        record=record,
        record_note=(failed.message if failed and record is None else ""),
        observations=observations,
        observations_note=note,
        care_tasks=herd.tasks_for(incident.subject_id),
        herd_mates=herd.herd_mates(record.pasture_id, excluding=incident.subject_id) if record else (),
    )


async def assemble(
    incidents: tuple[Incident, ...] | list[Incident],
    *,
    readings: tuple[SensorReading, ...] | list[SensorReading] = (),
    ranch_map: RanchMap | None = None,
    roster: PastureRoster | None = None,
    herd: HerdSweepResult | None = None,
    limit: int | None = None,
) -> tuple[EvidencePacket, ...]:
    """One packet per incident, in the order handed in.

    Called with **newly-opened incidents only**. An `ongoing` incident has already been
    worked, and re-assembling a page for it every five minutes spends HTTP calls to produce
    a work order nobody wanted twice.

    `roster` is passed in rather than fetched per call so a tick pays for the roster once.
    Fetched here when absent, which is the shape a test and a one-off script both want. From
    M7A the herd sweep already fetched it, so a tick hands it over through `herd`.

    An animal incident (`incident.is_animal`) costs **no HTTP at all** here: its page is built from
    `herd`, which the free pass already paid for, and it is never handed to the sensor client.
    """
    if not incidents:
        return ()

    settings = get_settings()
    ceiling = limit or settings.sweep_concurrency
    if roster is None:
        roster = herd.roster if herd is not None and herd.roster.pastures else await fetch_pasture_roster()

    sensor_incidents = [inc for inc in incidents if not inc.is_animal]
    history_by_key: dict[str, tuple[tuple[HistoryPoint, ...], str]] = {}
    if sensor_incidents:
        async with upstream_client(settings.sensor_api) as client:
            raw = await gather_bounded([fetch_history(client, inc.subject_id) for inc in sensor_incidents], limit=ceiling)
        for incident, outcome in zip(sensor_incidents, raw, strict=True):
            # `gather_bounded` returns exceptions as values. One sensor's history failing is
            # not a failed packet; it is a stated absence.
            history_by_key[incident.key] = outcome if isinstance(outcome, tuple) else ((), f"{type(outcome).__name__}")

    packets: list[EvidencePacket] = []
    for incident in incidents:
        pasture, pasture_note = roster.for_location(incident.location)
        sop_name, sop_text = load_sop(incident.category)
        if incident.is_animal:
            packets.append(EvidencePacket(incident=incident, pasture=pasture, pasture_note=pasture_note, sop_name=sop_name, sop_text=sop_text, animal=animal_context(incident, herd)))
            continue
        history, note = history_by_key[incident.key]
        ref = ranch_map.get(incident.subject_id) if ranch_map else None
        # M10 (#12): the facts the SOP asks about that live on other sensors, from this sweep. Only
        # the SOP files that ask get the block; `conditions_for` returns two empty tuples otherwise.
        conditions, conditions_missing = conditions_for(incident, readings, ranch_map, sensor_types=CONDITIONS_FOR_SOP.get(sop_name, ()))
        packets.append(
            EvidencePacket(
                incident=incident,
                history=history,
                history_note=note,
                siblings=siblings_for(incident.location, incident.subject_id, readings),
                pasture=pasture,
                pasture_note=pasture_note,
                sop_name=sop_name,
                sop_text=sop_text,
                coordinates=ref.coordinates if ref else None,
                conditions=conditions,
                conditions_missing=conditions_missing,
            )
        )

    missing_sop = sorted({p.incident.category for p in packets if not p.sop_name})
    if missing_sop:
        log.warning("packets_without_sop", categories=missing_sop, hint="add the category to SOP_FOR_CATEGORY and write the rule in data/knowledge_base/")
    log.info("evidence_assembled", packets=len(packets), animal_packets=sum(1 for p in packets if p.incident.is_animal), history_calls=len(sensor_incidents), pastures_known=len(roster.pastures), with_history=sum(1 for p in packets if p.history))
    return tuple(packets)
