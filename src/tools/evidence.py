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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from src.agent.state import Incident
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

PASTURE_LIMIT = 50  # 18 exist; the ceiling is here so a new pasture does not silently page

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
class PastureContext:
    """What is standing behind the problem. The head count is why one tank goes first."""

    pasture_id: str
    name: str
    acreage: int | None
    fence_type: str
    status: str
    head_count: int

    def render(self) -> str:
        acres = f"{self.acreage} acres, " if self.acreage else ""
        return f"{self.name} ({self.pasture_id}): {acres}{self.head_count} head on it, {self.fence_type or 'fence type unrecorded'}, pasture status {self.status or 'unrecorded'}"


@dataclass(frozen=True)
class EvidencePacket:
    """One page. Everything a human on shift would need, and nothing they would have to go get.

    Absences are stated, never omitted. `history_note` carrying "unavailable" is a fact the
    model has to work around; an empty history list with no explanation is one it will fill
    in for itself, which is exactly the invented premise the brief forbids.
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

    def render(self) -> str:
        """The page, as the model receives it. Also what a human reads to check the model.

        Deliberately one function: a packet whose printed form differs from the prompted
        form makes every debugging session a guess. If it reads badly here it reads badly
        to the model.
        """
        inc = self.incident
        rule = RULES.get(inc.sensor_type)
        unit = rule.unit if rule else inc.unit
        noun = rule.noun if rule else inc.sensor_type
        where = f"{inc.location}" + (f" at map point {self.coordinates.get('x')},{self.coordinates.get('y')}" if self.coordinates else "")

        lines: list[str] = []
        lines.append("## The incident, as triage ranked it in code")
        lines.append("")
        lines.append(f"- severity: {inc.severity.upper()} (already decided; not yours to change)")
        lines.append(f"- category: {inc.category}")
        lines.append(f"- sensor: {inc.sensor_id}, a {noun} sensor")
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

        lines.append("## What is standing behind it")
        lines.append("")
        lines.append(f"  {self.pasture.render()}" if self.pasture else f"  ({self.pasture_note or 'no pasture is mapped to this location'})")
        lines.append("")

        lines.append(f"## The standing orders that apply ({self.sop_name or 'none on file'})")
        lines.append("")
        lines.append(self.sop_text.strip() if self.sop_text else "  (no SOP exists for this category yet. Say so rather than citing one.)")
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
# the pasture roster: one call per tick, flat
# --------------------------------------------------------------------------- #
_SLUG = re.compile(r"[^a-z0-9]+")
#: The two upstream surfaces spell a location differently. `GET /sensors/:id` returns
#: `locationName: "Alkali Flat"`, while the `ranch://sensors/map` resource labels the same
#: group `"Alkali Flat (alkali-flat)"`. Incidents carry the REST form because that is what
#: the sweep parses, but a location string from the map must not slug to
#: `alkali-flat-alkali-flat` and silently match no pasture. When the id is spelled out in
#: parentheses, take it rather than re-deriving it.
_PARENTHESIZED_ID = re.compile(r"\(([a-z0-9][a-z0-9-]*)\)\s*$")


def slugify(location: str) -> str:
    text = location.strip()
    embedded = _PARENTHESIZED_ID.search(text.lower())
    if embedded:
        return embedded.group(1)
    return _SLUG.sub("-", text.lower()).strip("-")


@dataclass(frozen=True)
class PastureRoster:
    """Every pasture and its head count, from one `GET /pastures`.

    Matched to a sensor location by **slug against the pasture id**, not by display name.
    The two disagree on purpose upstream: the sensor location `East Allotment` is the
    pasture named `East BLM Allotment`, and both are the id `east-allotment`. A name match
    would drop three pastures and report "no cattle here" for ground with 111 head on it,
    which is the most dangerous way for this lookup to be wrong.
    """

    pastures: tuple[PastureContext, ...] = ()
    error: str = ""

    def for_location(self, location: str) -> tuple[PastureContext | None, str]:
        slug = slugify(location)
        for pasture in self.pastures:
            if pasture.pasture_id == slug:
                return pasture, ""
        if self.error:
            return None, f"pasture roster unavailable: {self.error}"
        # Not every sensor location is a pasture. A spring, a well, a barn, and the weather
        # station are all real locations with no cattle standing on them, and saying so is
        # better than implying the herd count is zero.
        return None, f"no pasture is mapped to {location}; it is a site rather than grazing ground"


def parse_pastures(payload: Any) -> tuple[PastureContext, ...]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return ()
    out: list[PastureContext] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        animal_ids = raw.get("animalIds")
        acreage = raw.get("acreage")
        out.append(
            PastureContext(
                pasture_id=str(raw.get("id") or ""),
                name=str(raw.get("name") or raw.get("id") or "unnamed"),
                acreage=int(acreage) if isinstance(acreage, (int, float)) else None,
                fence_type=str(raw.get("fenceType") or ""),
                status=str(raw.get("status") or ""),
                head_count=len(animal_ids) if isinstance(animal_ids, list) else 0,
            )
        )
    return tuple(p for p in out if p.pasture_id)


async def fetch_pasture_roster() -> PastureRoster:
    """One call. Errors come back as data, on the same rule as `sensors.py`.

    A missing roster must not fail the packet: a work order that names the tank and admits
    it does not know the head count is useful, and one that never got written because the
    farm API hiccuped is not.
    """
    settings = get_settings()
    try:
        async with upstream_client(settings.farm_api) as client:
            response = await client.get("/pastures", params={"limit": PASTURE_LIMIT})
            response.raise_for_status()
            pastures = parse_pastures(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("pasture_roster_unavailable", error=f"{type(exc).__name__}: {exc}")
        return PastureRoster(error=f"{type(exc).__name__}")
    if not pastures:
        log.warning("pasture_roster_empty", hint="200 with no pastures; head counts will be absent from every packet this tick")
        return PastureRoster(error="upstream returned no pastures")
    return PastureRoster(pastures=pastures)


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


async def assemble(
    incidents: tuple[Incident, ...] | list[Incident],
    *,
    readings: tuple[SensorReading, ...] | list[SensorReading] = (),
    ranch_map: RanchMap | None = None,
    roster: PastureRoster | None = None,
    limit: int | None = None,
) -> tuple[EvidencePacket, ...]:
    """One packet per incident, in the order handed in.

    Called with **newly-opened incidents only**. An `ongoing` incident has already been
    worked, and re-assembling a page for it every five minutes spends HTTP calls to produce
    a work order nobody wanted twice.

    `roster` is passed in rather than fetched per call so a tick pays for the roster once.
    Fetched here when absent, which is the shape a test and a one-off script both want.
    """
    if not incidents:
        return ()

    settings = get_settings()
    ceiling = limit or settings.sweep_concurrency
    roster = roster if roster is not None else await fetch_pasture_roster()

    async with upstream_client(settings.sensor_api) as client:
        raw = await gather_bounded([fetch_history(client, inc.sensor_id) for inc in incidents], limit=ceiling)

    packets: list[EvidencePacket] = []
    for incident, outcome in zip(incidents, raw, strict=True):
        if isinstance(outcome, tuple):
            history, note = outcome
        else:
            # `gather_bounded` returns exceptions as values. One sensor's history failing is
            # not a failed packet; it is a stated absence.
            history, note = (), f"{type(outcome).__name__}"
        pasture, pasture_note = roster.for_location(incident.location)
        sop_name, sop_text = load_sop(incident.category)
        ref = ranch_map.get(incident.sensor_id) if ranch_map else None
        packets.append(
            EvidencePacket(
                incident=incident,
                history=history,
                history_note=note,
                siblings=siblings_for(incident.location, incident.sensor_id, readings),
                pasture=pasture,
                pasture_note=pasture_note,
                sop_name=sop_name,
                sop_text=sop_text,
                coordinates=ref.coordinates if ref else None,
            )
        )

    missing_sop = sorted({p.incident.category for p in packets if not p.sop_name})
    if missing_sop:
        log.warning("packets_without_sop", categories=missing_sop, hint="add the category to SOP_FOR_CATEGORY and write the rule in data/knowledge_base/")
    log.info("evidence_assembled", packets=len(packets), history_calls=len(packets), pastures_known=len(roster.pastures), with_history=sum(1 for p in packets if p.history))
    return tuple(packets)
