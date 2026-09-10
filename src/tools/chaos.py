"""The fifth agent's hands: inject, heal, expire.

Two injection paths, and the asymmetry is forced rather than chosen:

  * **Sensor faults go to an overlay in `sw_ops`.** The deployed Sensor API is stateless
    and synthesizes every reading in code, so there is nowhere to write a fault into it,
    and its own injector deliberately refuses to register on Lambda. That guard is correct
    and stays. `src/tools/sensors.py` applies overlay rows on top of the honest live read
    before triage sees it: the deployed API stays truthful and this repo owns the lie, in
    one place, under test.
  * **Animal events are written for real**, guarded by `CHAOS_ALLOW_WRITES` and confined to
    `CHAOS_ANIMAL_COHORT` so the rest of the herd stays pristine. A coyote kill is a status
    change plus an observation a human would actually read, and `herd_health` has to find it
    through its own tools with no overlay at all. There is no third way to do that: an
    overlay it cannot see is not a discovery.

**The write spans two APIs, and this is the trap.** `PATCH /animals/:animalId` is on the
**FARM** API and `POST /animals/:animalId/observations` is on the **CARE** API. `evidence.py`
already carries this warning and M5 walked into it anyway: one base URL for both 404s, and a
404 does not name itself. Every field name and every enum below was read off the deployed
services on 2026-09-10 by sending deliberately invalid bodies to a nonexistent animal id and
reading the 422s, which is the only way to learn a contract without either mutating a real
animal or reading the frozen upstream's source.

**Determinism is the product.** `plan()` is a pure function of `(seed, catalog, ticks)`:
no clock, no database, no settings that change what fires. It draws with an explicit
cumulative-weight roll and `randrange` rather than `choices`/`sample`/`shuffle`, because a
golden fixture that depends on a stdlib helper's internals is a fixture that breaks on a
Python upgrade for no reason. Same seed, same demo, twice, across a process restart.

**It heals.** Every event carries a TTL and expiry is what produces `resolved` incidents.
Without healing everything is broken an hour in and the feed goes quiet. The one exception
is deliberate and documented: a real write to a real animal is not undone by a row
expiring, so `restore` is a separate command a human runs on purpose.

The model is not in the load-bearing path. The seeded PRNG picks what breaks; an optional
model pass authors only the observation prose a human reads, and that is a Tier 1 job at
M7. Same ownership rule as severity.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from src.agent.memory import (
    StoreTarget,
    active_chaos_events,
    chaos_counts_by_status,
    expire_chaos_events,
    insert_chaos_events,
    resolve_store,
    store_session,
)
from src.tools.mcp_client import SensorRef
from src.utils.config import REPO_ROOT, Settings, get_settings
from src.utils.helpers import upstream_client
from src.utils.logger import configure_logging, get_logger, get_run_id, log_audit_decided, log_audit_proposed, new_audit_id

if TYPE_CHECKING:  # `sensors.py` imports this module, so the dependency runs one way only
    from src.tools.sensors import SensorReading

log = get_logger(__name__)

CATALOG_PATH = REPO_ROOT / "data" / "examples.json"

KIND_SENSOR = "sensor_overlay"
KIND_ANIMAL = "animal_event"
KINDS = frozenset({KIND_SENSOR, KIND_ANIMAL})

#: Every fault mode that exists. There is no default branch anywhere in this module: an
#: unrecognized mode warns and changes nothing, the same discipline `triage.py` applies to
#: an unrecognized sensor type. A fault mode that silently no-ops is a demo that quietly
#: does nothing, which is the failure this module is most likely to have.
MODE_OFFLINE = "offline"
MODE_DEGRADED = "degraded"
MODE_SENTINEL = "sentinel"
MODE_PIN = "pin"
MODE_DRIFT = "drift"
MODE_GATE_OPEN = "gate_open"
MODE_ANIMAL_STATUS = "animal_status"

SENSOR_MODES = frozenset({MODE_OFFLINE, MODE_DEGRADED, MODE_SENTINEL, MODE_PIN, MODE_DRIFT, MODE_GATE_OPEN})
ANIMAL_MODES = frozenset({MODE_ANIMAL_STATUS})
ALL_MODES = SENSOR_MODES | ANIMAL_MODES

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"

#: The three upstream vocabularies, verified over the wire on 2026-09-10 by reading the
#: services' own 422s. They are validated at catalog load rather than discovered at write
#: time: a scenario that 422s is a scenario that fires during a demo and does nothing, which
#: is the exact failure `parse_catalog` exists to prevent. These are the deployed contract,
#: not constants copied out of the frozen upstream's internals - which is why they were read
#: from the API instead of from its source, and why a retune over there shows up here as a
#: loud 422 rather than as a value this repo cannot verify.
ANIMAL_STATUSES = ("active", "inactive", "sold", "deceased")
OBSERVATION_TYPES = ("behavior", "appetite", "mobility", "appearance", "injury", "general")
OBSERVATION_SEVERITIES = ("low", "medium", "high")

#: What `restore` puts the cohort back to. Every animal on the live ranch reads `active`, and
#: `healthy` is not in the enum at all.
ANIMAL_STATUS_NORMAL = "active"


class ChaosCatalogError(ValueError):
    """`data/examples.json` is not a usable scenario catalog. Raised at load, never swallowed."""


# --------------------------------------------------------------------------- #
# warn_once, same shape as triage's
# --------------------------------------------------------------------------- #
@dataclass
class _WarnOnce:
    seen: set[str] = field(default_factory=set)

    def fire(self, key: str, event: str, **fields: object) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        log.warning(event, **fields)
        return True


_unknown_modes = _WarnOnce()
_missing_targets = _WarnOnce()
_unusable_faults = _WarnOnce()


def reset_warn_once() -> None:
    """Tests only. Warn-once state is per process."""
    for w in (_unknown_modes, _missing_targets, _unusable_faults):
        w.seen.clear()


# --------------------------------------------------------------------------- #
# the catalog
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FaultSpec:
    """One fault a scenario applies. `sensor_type` rather than a sensor id, always.

    A catalog that named specific sensors would be a topology this repo cannot verify and
    that fails silently the day the ranch adds a pasture. Types are the contract; the
    seeded PRNG turns them into targets against whatever catalog the ranch actually has.
    """

    mode: str
    sensor_type: str = ""
    count: int = 1
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    kind: str
    weight: float
    ttl_ticks: int
    faults: tuple[FaultSpec, ...]
    correlated: bool = False
    co_located: bool = False


_RESERVED_FAULT_KEYS = frozenset({"mode", "sensor_type", "count", "note"})


def parse_catalog(payload: Any) -> tuple[Scenario, ...]:
    """Parse and **validate**. A typo'd mode raises here rather than no-opping at inject time.

    Validation is loud on purpose. The natural failure of a data-driven injector is a
    scenario that parses, fires, and does nothing, which presents as a calm ranch during a
    demo and is indistinguishable from the monitor being broken.
    """
    if not isinstance(payload, dict):
        raise ChaosCatalogError("the scenario catalog must be a JSON object")
    raw = payload.get("scenarios")
    if not isinstance(raw, list) or not raw:
        raise ChaosCatalogError("the scenario catalog has no `scenarios` list; chaos with an empty catalog is a chaos agent configured to do nothing")

    scenarios: list[Scenario] = []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ChaosCatalogError(f"every scenario must be an object, got {type(entry).__name__}")
        sid = str(entry.get("id") or "")
        if not sid:
            raise ChaosCatalogError("a scenario is missing its `id`")
        if sid in seen_ids:
            raise ChaosCatalogError(f"duplicate scenario id {sid!r}; the id is what a ledger row is traced back to")
        seen_ids.add(sid)
        kind = str(entry.get("kind") or KIND_SENSOR)
        if kind not in KINDS:
            raise ChaosCatalogError(f"scenario {sid!r} has kind {kind!r}, which is not one of {sorted(KINDS)}")
        weight = float(entry.get("weight", 1))
        if weight <= 0:
            raise ChaosCatalogError(f"scenario {sid!r} has weight {weight}; a scenario that can never fire should be deleted, not weighted to zero")
        ttl = int(entry.get("ttl_ticks", 0))
        if ttl <= 0:
            raise ChaosCatalogError(f"scenario {sid!r} has no positive `ttl_ticks`; an event that never heals is a ranch that goes quiet an hour in")

        raw_faults = entry.get("faults")
        if not isinstance(raw_faults, list) or not raw_faults:
            raise ChaosCatalogError(f"scenario {sid!r} has no faults")
        faults: list[FaultSpec] = []
        for f in raw_faults:
            if not isinstance(f, dict):
                raise ChaosCatalogError(f"scenario {sid!r} has a non-object fault")
            mode = str(f.get("mode") or "")
            allowed = SENSOR_MODES if kind == KIND_SENSOR else ANIMAL_MODES
            if mode not in allowed:
                raise ChaosCatalogError(f"scenario {sid!r} uses mode {mode!r}, which is not valid for kind {kind!r} (valid: {sorted(allowed)})")
            sensor_type = str(f.get("sensor_type") or "")
            if kind == KIND_SENSOR and not sensor_type:
                raise ChaosCatalogError(f"scenario {sid!r} has a sensor fault with no `sensor_type`")
            count = int(f.get("count", 1))
            if count <= 0:
                raise ChaosCatalogError(f"scenario {sid!r} has a fault with count {count}")
            if mode in (MODE_PIN, MODE_SENTINEL) and not isinstance(f.get("value"), (int, float)):
                raise ChaosCatalogError(f"scenario {sid!r} uses {mode!r} without a numeric `value`")
            if mode == MODE_DRIFT and not (isinstance(f.get("from"), (int, float)) and isinstance(f.get("to"), (int, float))):
                raise ChaosCatalogError(f"scenario {sid!r} uses `drift` without numeric `from` and `to`; the ramp is stated rather than read off the live value so it can be replayed")
            if mode == MODE_ANIMAL_STATUS:
                # Checked here, against the enums the deployed services actually validate, so
                # a typo is a load-time error rather than a 422 nobody is watching for during
                # a demo. `_observation_body` then has nothing left to guess.
                if f.get("status") not in ANIMAL_STATUSES:
                    raise ChaosCatalogError(f"scenario {sid!r} sets status {f.get('status')!r}; the Farm API accepts only {list(ANIMAL_STATUSES)}")
                if f.get("observation_type") not in OBSERVATION_TYPES:
                    raise ChaosCatalogError(f"scenario {sid!r} sets observation_type {f.get('observation_type')!r}; the Care API accepts only {list(OBSERVATION_TYPES)}")
                if f.get("severity") not in OBSERVATION_SEVERITIES:
                    raise ChaosCatalogError(f"scenario {sid!r} sets severity {f.get('severity')!r}; the Care API accepts only {list(OBSERVATION_SEVERITIES)}")
                if not str(f.get("observation") or "").strip():
                    raise ChaosCatalogError(f"scenario {sid!r} has no `observation` text; the note is the whole point - it is what a human reads and what herd_health has to discover")
            params = {k: v for k, v in f.items() if k not in _RESERVED_FAULT_KEYS}
            faults.append(FaultSpec(mode=mode, sensor_type=sensor_type, count=count, params=params))

        scenarios.append(
            Scenario(
                id=sid,
                title=str(entry.get("title") or sid),
                kind=kind,
                weight=weight,
                ttl_ticks=ttl,
                faults=tuple(faults),
                correlated=bool(entry.get("correlated", False)),
                co_located=bool(entry.get("co_located", False)),
            )
        )
    # Sorted by id, not left in file order. Scenario weights decide what fires; file order
    # must not, or reordering the catalog for readability silently changes every seed.
    return tuple(sorted(scenarios, key=lambda s: s.id))


@lru_cache(maxsize=4)
def load_catalog(path: Path | None = None) -> tuple[Scenario, ...]:
    return parse_catalog(json.loads((path or CATALOG_PATH).read_text(encoding="utf-8")))


def load_fixtures(path: Path | None = None) -> dict[str, Any]:
    """The golden replay fixture: a synthetic catalog and the plan seed 1 makes from it."""
    payload = json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))
    fixtures = payload.get("fixtures")
    return fixtures if isinstance(fixtures, dict) else {}


# --------------------------------------------------------------------------- #
# the plan: pure, seeded, and the whole determinism promise
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PlannedFault:
    """One fault the seed decided on, before a clock or a database is involved.

    A plan for 12 ticks is a prefix-extension of the plan for 6, because every draw happens
    in tick order. That is what makes `event_id` stable across runs of different lengths and
    lets `ON CONFLICT DO NOTHING` make a replay idempotent instead of doubling the demo.
    """

    seq: int
    tick: int
    seed: int
    scenario: str
    group_id: str
    kind: str
    target_id: str
    target_type: str
    location: str
    fault: str
    payload: dict[str, Any]
    ttl_ticks: int

    @property
    def event_id(self) -> str:
        return f"chaos-{self.seed}-{self.seq}"


def _weighted_pick(rng: random.Random, scenarios: tuple[Scenario, ...]) -> Scenario:
    """One cumulative-weight roll off one `random()` call. Explicit rather than `choices`.

    `random.choices` is deterministic within a Python version and not across the internals
    of one; a golden fixture asserting on a plan should not be able to break because a
    stdlib helper changed how it bisects.
    """
    total = sum(s.weight for s in scenarios)
    roll = rng.random() * total
    upto = 0.0
    for s in scenarios:
        upto += s.weight
        if roll < upto:
            return s
    return scenarios[-1]


def _take(rng: random.Random, pool: list[SensorRef], *, at_location: str | None = None) -> SensorRef | None:
    """Pop one target. `at_location` is a preference, and a miss is reported, never faked."""
    candidates = [r for r in pool if r.location == at_location] if at_location is not None else pool
    if not candidates:
        return None
    chosen = candidates[rng.randrange(len(candidates))]
    pool.remove(chosen)
    return chosen


def plan(
    *,
    seed: int,
    catalog: Sequence[SensorRef] | tuple[SensorRef, ...],
    ticks: int,
    scenarios: tuple[Scenario, ...] | None = None,
    cohort: Sequence[str] = (),
    ticks_between_events: int = 1,
) -> tuple[PlannedFault, ...]:
    """What breaks, where, and when - as a pure function of the seed and the topology.

    No clock, no database, no `CHAOS_ALLOW_WRITES`. In particular an `animal_event` stays in
    the plan whether or not writes are permitted: a plan that changes shape when a flag
    flips is a plan whose seed no longer identifies a demo, and the guard belongs at the
    write, where it can leave a receipt.
    """
    if ticks <= 0:
        return ()
    every = max(1, ticks_between_events)
    scen = scenarios if scenarios is not None else load_catalog()
    if not scen:
        return ()

    rng = random.Random(seed)
    # Sorted by id, so the plan depends on the set of sensors and never on the order the
    # catalog happened to arrive in. The MCP resource and the REST fallback do not promise
    # the same order, and a demo must not change shape based on which one answered.
    by_type: dict[str, list[SensorRef]] = {}
    for entry in sorted(catalog, key=lambda r: r.sensor_id):
        by_type.setdefault(entry.sensor_type, []).append(entry)
    animals = sorted({a for a in cohort if a})

    out: list[PlannedFault] = []
    seq = 0
    for tick in range(1, ticks + 1):
        if tick % every != 0:
            continue
        scenario = _weighted_pick(rng, scen)
        group_id = f"chaos-{seed}-t{tick}-{scenario.id}"
        pools: dict[str, list[SensorRef]] = {t: list(refs) for t, refs in by_type.items()}
        animal_pool = list(animals)
        anchor_location: str | None = None

        for spec in scenario.faults:
            for _ in range(spec.count):
                payload = dict(spec.params)
                if scenario.kind == KIND_ANIMAL:
                    if not animal_pool:
                        _missing_targets.fire("cohort", "chaos_no_cohort_target", scenario=scenario.id, hint="CHAOS_ANIMAL_COHORT is empty, so animal scenarios plan nothing")
                        continue
                    animal_id = animal_pool.pop(rng.randrange(len(animal_pool)))
                    out.append(PlannedFault(seq=seq, tick=tick, seed=seed, scenario=scenario.id, group_id=group_id, kind=KIND_ANIMAL, target_id=animal_id, target_type="animal", location="", fault=spec.mode, payload=payload, ttl_ticks=scenario.ttl_ticks))
                    seq += 1
                    continue

                pool = pools.setdefault(spec.sensor_type, [])
                ref: SensorRef | None = _take(rng, pool, at_location=anchor_location) if (scenario.co_located and anchor_location is not None) else None
                if ref is None and scenario.co_located and anchor_location is not None:
                    # Best-effort, and the miss is recorded rather than papered over. A
                    # scenario that claims two co-located faults and got two unrelated ones
                    # is still a useful fault; it is just not the causal story it advertises.
                    payload["co_located"] = False
                if ref is None:
                    ref = _take(rng, pool)
                if ref is None:
                    _missing_targets.fire(spec.sensor_type, "chaos_no_target_for_type", scenario=scenario.id, sensor_type=spec.sensor_type, hint="the live catalog has no sensor of this type, or the scenario asked for more than it has")
                    continue
                if anchor_location is None:
                    anchor_location = ref.location
                out.append(PlannedFault(seq=seq, tick=tick, seed=seed, scenario=scenario.id, group_id=group_id, kind=KIND_SENSOR, target_id=ref.sensor_id, target_type=ref.sensor_type, location=ref.location, fault=spec.mode, payload=payload, ttl_ticks=scenario.ttl_ticks))
                seq += 1

    return tuple(out)


# --------------------------------------------------------------------------- #
# the event, as it lives in sw_ops
# --------------------------------------------------------------------------- #
def _as_utc(value: datetime) -> datetime:
    """Naive in, UTC out. Mixing the two is a `TypeError` at subtraction, mid-sweep."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ChaosEvent:
    """A planned fault that has been written down, with a lifetime attached."""

    event_id: str
    group_id: str
    scenario: str
    kind: str
    target_id: str
    target_type: str
    location: str
    fault: str
    payload: dict[str, Any]
    seed: int
    seq: int
    status: str
    injected_at: datetime
    expires_at: datetime
    expired_at: datetime | None = None
    tick_injected: int = 0
    run_id: str = ""

    @classmethod
    def materialize(cls, planned: PlannedFault, *, now: datetime, tick_seconds: float, run_id: str = "") -> ChaosEvent:
        stamp = _as_utc(now)
        return cls(
            event_id=planned.event_id,
            group_id=planned.group_id,
            scenario=planned.scenario,
            kind=planned.kind,
            target_id=planned.target_id,
            target_type=planned.target_type,
            location=planned.location,
            fault=planned.fault,
            payload=dict(planned.payload),
            seed=planned.seed,
            seq=planned.seq,
            status=STATUS_ACTIVE,
            injected_at=stamp,
            expires_at=stamp + timedelta(seconds=planned.ttl_ticks * tick_seconds),
            tick_injected=planned.tick,
            run_id=run_id,
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ChaosEvent:
        payload = row.get("payload")
        return cls(
            event_id=str(row["event_id"]),
            group_id=str(row.get("group_id") or ""),
            scenario=str(row["scenario"]),
            kind=str(row["kind"]),
            target_id=str(row["target_id"]),
            target_type=str(row.get("target_type") or ""),
            location=str(row.get("location") or ""),
            fault=str(row["fault"]),
            payload=dict(payload) if isinstance(payload, dict) else {},
            seed=int(row.get("seed") or 0),
            seq=int(row.get("seq") or 0),
            status=str(row["status"]),
            injected_at=_as_utc(row["injected_at"]),
            expires_at=_as_utc(row["expires_at"]),
            expired_at=_as_utc(row["expired_at"]) if row.get("expired_at") else None,
            tick_injected=int(row.get("tick_injected") or 0),
            run_id=str(row.get("run_id") or ""),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "group_id": self.group_id,
            "scenario": self.scenario,
            "kind": self.kind,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "location": self.location,
            "fault": self.fault,
            "payload": self.payload,
            "seed": self.seed,
            "seq": self.seq,
            "status": self.status,
            "injected_at": self.injected_at,
            "expires_at": self.expires_at,
            "expired_at": self.expired_at,
            "tick_injected": self.tick_injected,
            "run_id": self.run_id,
        }

    def progress(self, now: datetime) -> float:
        """0.0 at injection, 1.0 at expiry. The only clock a fault mode is allowed to read."""
        span = (self.expires_at - self.injected_at).total_seconds()
        if span <= 0:
            return 1.0
        elapsed = (_as_utc(now) - self.injected_at).total_seconds()
        return min(1.0, max(0.0, elapsed / span))


# --------------------------------------------------------------------------- #
# the overlay, applied over an honest reading
# --------------------------------------------------------------------------- #
def apply_fault(reading: SensorReading, event: ChaosEvent, *, now: datetime) -> SensorReading:
    """One fault over one honest reading. Returns the reading unchanged if it cannot apply.

    Every branch produces a reading that triage already understands: `offline` becomes the
    dark sensor, `sentinel` becomes the -500 probe the live ranch already has, `pin` and
    `drift` land in real threshold bands. Chaos invents no new category, which is what keeps
    severity entirely `triage.py`'s and keeps a faulted ranch and a genuinely broken one
    indistinguishable from the monitor's side. That is the point of the exercise.
    """
    mode = event.fault
    if mode == MODE_OFFLINE:
        return replace(reading, status="offline", value=None)
    if mode == MODE_DEGRADED:
        # Value untouched: a degraded sensor still answers, and the readings are merely no
        # longer trustworthy. That is the harder of the two failures for a human to notice.
        return replace(reading, status="degraded")
    if mode == MODE_GATE_OPEN:
        return replace(reading, value=True)
    if mode in (MODE_PIN, MODE_SENTINEL):
        value = event.payload.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _unusable_faults.fire(f"{event.event_id}:value", "chaos_fault_unusable", event_id=event.event_id, mode=mode, reason="no numeric `value` in the payload")
            return reading
        return replace(reading, value=float(value))
    if mode == MODE_DRIFT:
        start, end = event.payload.get("from"), event.payload.get("to")
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            _unusable_faults.fire(f"{event.event_id}:ramp", "chaos_fault_unusable", event_id=event.event_id, mode=mode, reason="`from` and `to` must both be numeric")
            return reading
        return replace(reading, value=float(start) + event.progress(now) * (float(end) - float(start)))

    # No default branch. An unrecognized mode changes nothing and says so, exactly as an
    # unrecognized sensor type never reads as nominal in triage.
    _unknown_modes.fire(mode, "chaos_unknown_fault_mode", mode=mode, event_id=event.event_id, hint="add it to SENSOR_MODES in src/tools/chaos.py; nothing was applied")
    return reading


def apply_overlay(readings: Sequence[SensorReading], events: Sequence[ChaosEvent], *, now: datetime | None = None) -> tuple[SensorReading, ...]:
    """Lay the active overlay over one sweep's honest readings.

    Applied in `seq` order so two faults on one sensor compose the same way every run.
    Every substitution is logged: a faked reading that leaves no trace is a demo nobody can
    audit afterward, and the log is the only place the honest value still exists.
    """
    overlay = [e for e in events if e.kind == KIND_SENSOR]
    if not overlay:
        return tuple(readings)
    stamp = _as_utc(now or datetime.now(UTC))
    by_target: dict[str, list[ChaosEvent]] = {}
    for event in sorted(overlay, key=lambda e: (e.seq, e.event_id)):
        by_target.setdefault(event.target_id, []).append(event)

    out: list[SensorReading] = []
    for reading in readings:
        faked = reading
        for event in by_target.get(reading.sensor_id, ()):
            before = faked
            faked = apply_fault(faked, event, now=stamp)
            if faked is not before:
                log.info("chaos_overlay_applied", sensor_id=reading.sensor_id, scenario=event.scenario, mode=event.fault, event_id=event.event_id, honest_value=before.value, faked_value=faked.value, faked_status=faked.status)
        out.append(faked)
    return tuple(out)


async def active_overlay(*, now: datetime | None = None, target: StoreTarget | None = None) -> tuple[ChaosEvent, ...]:
    """The active sensor overlay, or nothing at all. Called by `sweep()` every tick.

    Returns `()` immediately when chaos is off, before touching a database, so a switched-off
    overlay costs a boolean rather than a connection. And it swallows its own failures on
    purpose: chaos is a fixture, and a fixture that can fail the tick it decorates is worse
    than no fixture. The sweep continues on honest readings and says so in the log.
    """
    settings = get_settings()
    if not settings.chaos_enabled:
        return ()
    stamp = _as_utc(now or datetime.now(UTC))
    try:
        store = target or resolve_store()
        async with store_session(url=store.url, schema=store.schema) as session:
            rows = await active_chaos_events(session, now=stamp, kind=KIND_SENSOR)
        return tuple(ChaosEvent.from_row(r) for r in rows)
    except Exception as exc:
        log.warning("chaos_overlay_unavailable", error=f"{type(exc).__name__}: {exc}", hint="the sweep continues on honest readings; chaos never fails the tick it decorates")
        return ()


# --------------------------------------------------------------------------- #
# injection and healing
# --------------------------------------------------------------------------- #
async def inject_for_tick(
    session: Any,
    *,
    tick: int,
    planned: Sequence[PlannedFault],
    now: datetime | None = None,
    run_id: str = "",
    max_active: int | None = None,
    tick_seconds: float | None = None,
) -> tuple[ChaosEvent, ...]:
    """Materialize this tick's slice of the plan into `sw_ops.chaos_events`.

    The ceiling is enforced **by whole group**, never by truncating a list. A storm front
    trimmed to two of its four faults is no longer the scenario it claims to be, and the
    fused-work-order behaviour it exists to demonstrate would silently stop being tested.
    """
    settings = get_settings()
    due = [p for p in planned if p.tick == tick]
    if not due:
        return ()
    stamp = _as_utc(now or datetime.now(UTC))
    ceiling = settings.chaos_max_active if max_active is None else max_active
    seconds = float(settings.tick_interval_seconds if tick_seconds is None else tick_seconds)

    live = await active_chaos_events(session, now=stamp)
    headroom = ceiling - len(live)
    if headroom <= 0:
        log.info("chaos_at_ceiling", active=len(live), ceiling=ceiling, tick=tick, hint="nothing injected; healing has to catch up first, which is what keeps something left to break")
        return ()

    groups: dict[str, list[PlannedFault]] = {}
    for p in sorted(due, key=lambda p: p.seq):
        groups.setdefault(p.group_id, []).append(p)

    chosen: list[PlannedFault] = []
    for group_id, members in groups.items():
        if len(chosen) + len(members) > headroom:
            log.info("chaos_group_deferred", group_id=group_id, faults=len(members), headroom=headroom - len(chosen), hint="a correlated scenario is injected whole or not at all")
            continue
        chosen.extend(members)
    if not chosen:
        return ()

    events = [ChaosEvent.materialize(p, now=stamp, tick_seconds=seconds, run_id=run_id) for p in chosen]
    landed = await insert_chaos_events(session, [e.to_row() for e in events])
    out = tuple(ChaosEvent.from_row(r) for r in landed)
    for event in out:
        log.info("chaos_injected", event_id=event.event_id, group_id=event.group_id, scenario=event.scenario, kind=event.kind, target_id=event.target_id, mode=event.fault, expires_at=event.expires_at.isoformat(), tick=tick)
    return out


async def expire(session: Any, *, now: datetime | None = None, force_all: bool = False) -> tuple[ChaosEvent, ...]:
    """Heal what is due. This is the half of chaos that makes the feed feel alive."""
    stamp = _as_utc(now or datetime.now(UTC))
    rows = await expire_chaos_events(session, now=stamp, force_all=force_all)
    return tuple(ChaosEvent.from_row(r) for r in rows)


# --------------------------------------------------------------------------- #
# the real-write path, and every guard on it
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WriteOutcome:
    """What happened to one animal event. `performed=False` always carries a reason."""

    event_id: str
    animal_id: str
    performed: bool
    reason: str
    status_code: int | None = None


def blocked_reason(animal_id: str, *, settings: Settings | None = None) -> str | None:
    """The guard, in exactly one place. `None` means this write may proceed.

    Two conditions, both load-bearing, and neither is a nice-to-have. `CHAOS_ALLOW_WRITES`
    is off by default because this is the one path in the repo that mutates a real deployed
    API. The cohort confines every mutation to a named handful of animals, so the rest of
    the herd stays pristine for other demos and a bug here cannot scale past five cows.
    """
    s = settings or get_settings()
    if not s.chaos_allow_writes:
        return "writes_disabled"
    cohort = s.chaos_cohort
    if not cohort:
        return "cohort_empty"
    if animal_id not in cohort:
        return "outside_cohort"
    return None


def _observation_body(event: ChaosEvent) -> dict[str, Any]:
    """The Care API's real shape. Four required fields, and `note` is not spelled `notes`.

    `observedAt` is required and is **the event's own injection time**, not `now()`: the note
    a human reads has to be stamped with when the ranch broke, and it is milliseconds-precision
    UTC because the upstreams are ordered lexicographically. `observedBy` is not a field the
    service has - it accepts it and drops it - so it is not sent, because a body that looks
    like it recorded an author and did not is worse than one that never claimed to.
    """
    payload = event.payload
    return {
        "type": str(payload.get("observation_type") or "general"),
        "severity": str(payload.get("severity") or "high"),
        "note": str(payload.get("observation") or f"Chaos scenario {event.scenario}."),
        "observedAt": event.injected_at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{event.injected_at.microsecond // 1000:03d}Z",
    }


async def write_animal_event(farm: httpx.AsyncClient, care: httpx.AsyncClient, event: ChaosEvent, *, settings: Settings | None = None) -> WriteOutcome:
    """`PATCH /animals/:animalId` on the **FARM** API, then `POST .../observations` on **CARE**.

    Two clients, because it is two services. `/animals` lives on the Farm API and only the
    observation lives on Care; using one base URL for both 404s, and a 404 does not name
    itself. This function is the only place in the repo that mutates a deployed service.

    Both calls leave an audit pair, including the blocked case. That is deliberate: the
    receipt that **nothing** was mutated is the evidence the guard held, and an
    `audit_id` with no decision beside it is the one shape `tests/` refuses to allow.
    """
    audit_id = new_audit_id()
    log_audit_proposed(audit_id=audit_id, tool="PATCH /animals/:animalId", args={"animalId": event.target_id, "status": event.payload.get("status")}, proposed_by="chaos", incident_key=None, scenario=event.scenario, event_id=event.event_id)

    reason = blocked_reason(event.target_id, settings=settings)
    if reason is not None:
        log_audit_decided(audit_id=audit_id, decision="blocked", decided_by="chaos_guard", result=reason, latency_to_decision_ms=0, event_id=event.event_id, animal_id=event.target_id)
        log.info("chaos_write_blocked", event_id=event.event_id, animal_id=event.target_id, reason=reason)
        return WriteOutcome(event_id=event.event_id, animal_id=event.target_id, performed=False, reason=reason)

    status = str(event.payload.get("status") or "")
    try:
        patched = await farm.patch(f"/animals/{event.target_id}", json={"status": status})
        if patched.status_code >= 400:
            log_audit_decided(audit_id=audit_id, decision="auto_allowed", decided_by="chaos", result=f"patch_failed_{patched.status_code}", latency_to_decision_ms=0, event_id=event.event_id, animal_id=event.target_id)
            return WriteOutcome(event_id=event.event_id, animal_id=event.target_id, performed=False, reason=f"patch_failed_{patched.status_code}", status_code=patched.status_code)
        observed = await care.post(f"/animals/{event.target_id}/observations", json=_observation_body(event))
        if observed.status_code >= 400:
            # The status change landed and the note did not. Reported as a partial rather
            # than as a failure, because the animal really is changed upstream and a caller
            # that retries the whole thing writes the status twice.
            log_audit_decided(audit_id=audit_id, decision="auto_allowed", decided_by="chaos", result=f"observation_failed_{observed.status_code}", latency_to_decision_ms=0, event_id=event.event_id, animal_id=event.target_id)
            log.warning("chaos_write_partial", event_id=event.event_id, animal_id=event.target_id, status_code=observed.status_code, hint="status changed upstream, observation not written; do not retry the pair")
            return WriteOutcome(event_id=event.event_id, animal_id=event.target_id, performed=True, reason=f"observation_failed_{observed.status_code}", status_code=observed.status_code)
    except httpx.HTTPError as exc:
        log_audit_decided(audit_id=audit_id, decision="auto_allowed", decided_by="chaos", result=f"transport_{type(exc).__name__}", latency_to_decision_ms=0, event_id=event.event_id, animal_id=event.target_id)
        return WriteOutcome(event_id=event.event_id, animal_id=event.target_id, performed=False, reason=f"transport_{type(exc).__name__}")

    log_audit_decided(audit_id=audit_id, decision="auto_allowed", decided_by="chaos", result="written", latency_to_decision_ms=0, event_id=event.event_id, animal_id=event.target_id)
    log.info("chaos_animal_written", event_id=event.event_id, animal_id=event.target_id, status=status, scenario=event.scenario)
    return WriteOutcome(event_id=event.event_id, animal_id=event.target_id, performed=True, reason="written", status_code=200)


async def fire_animal_events(events: Sequence[ChaosEvent], *, settings: Settings | None = None) -> tuple[WriteOutcome, ...]:
    """Every animal event in a batch, guarded.

    **No HTTP client is constructed when nothing is permitted.** The guard runs first and
    the connection is opened only if something survives it, so with `CHAOS_ALLOW_WRITES=0`
    the write path is not merely refused at the last moment: it is never entered.
    """
    s = settings or get_settings()
    animal_events = [e for e in events if e.kind == KIND_ANIMAL]
    if not animal_events:
        return ()

    blocked = [(e, blocked_reason(e.target_id, settings=s)) for e in animal_events]
    permitted = [e for e, reason in blocked if reason is None]
    outcomes: list[WriteOutcome] = []
    if not permitted:
        for event, reason in blocked:
            log_and_reason = reason or "writes_disabled"
            audit_id = new_audit_id()
            log_audit_proposed(audit_id=audit_id, tool="PATCH /animals/:animalId", args={"animalId": event.target_id, "status": event.payload.get("status")}, proposed_by="chaos", incident_key=None, scenario=event.scenario, event_id=event.event_id)
            log_audit_decided(audit_id=audit_id, decision="blocked", decided_by="chaos_guard", result=log_and_reason, latency_to_decision_ms=0, event_id=event.event_id, animal_id=event.target_id)
            log.info("chaos_write_blocked", event_id=event.event_id, animal_id=event.target_id, reason=log_and_reason)
            outcomes.append(WriteOutcome(event_id=event.event_id, animal_id=event.target_id, performed=False, reason=log_and_reason))
        return tuple(outcomes)

    async with upstream_client(s.farm_api) as farm, upstream_client(s.care_api) as care:
        for event in animal_events:
            outcomes.append(await write_animal_event(farm, care, event, settings=s))
    return tuple(outcomes)


async def restore_cohort(*, status: str = ANIMAL_STATUS_NORMAL, settings: Settings | None = None) -> tuple[WriteOutcome, ...]:
    """Put the cohort back. Deliberate, human-invoked, and guarded exactly like the kill.

    This exists because of the one asymmetry a TTL cannot fix: a sensor overlay expires and
    the sensor is honest again, but a real `PATCH` against a real animal is not undone by a
    row changing status. So restoring is a separate command somebody runs on purpose rather
    than something that quietly happens, and it goes through the same guard and leaves the
    same receipt.

    It writes an observation as well as flipping the status, because the animal's history is
    what `herd_health` reads: a status that silently reverts leaves a record where a cow died
    and was then fine, with nothing in between explaining it.
    """
    s = settings or get_settings()
    if status not in ANIMAL_STATUSES:
        raise ValueError(f"status {status!r} is not one of {list(ANIMAL_STATUSES)}; the Farm API would 422 and the cohort would stay broken")
    now = datetime.now(UTC)
    events = [
        ChaosEvent(event_id=f"restore-{animal_id}-{now.strftime('%Y%m%dT%H%M%S')}", group_id="restore", scenario="restore_cohort", kind=KIND_ANIMAL, target_id=animal_id, target_type="animal", location="", fault=MODE_ANIMAL_STATUS, payload={"status": status, "observation_type": "general", "severity": "low", "observation": f"Chaos cohort restored to {status} after a demo. This animal is part of the chaos cohort and nothing recorded against it reflects a real event."}, seed=s.chaos_seed, seq=0, status=STATUS_EXPIRED, injected_at=now, expires_at=now)
        for animal_id in s.chaos_cohort
    ]
    return await fire_animal_events(events, settings=s)


# --------------------------------------------------------------------------- #
# the CLI: `python -m src.tools.chaos ...`
# --------------------------------------------------------------------------- #
# Chaos drives itself from here at M5 rather than from a stage inside `run_tick`. The tick
# order in `src/agent/CLAUDE.md` puts "chaos maybe-fires" first and that wiring is a handful
# of lines in `executor.py`, which M3 owns while this is being built. Deferred on purpose,
# to the M3 boundary; the overlay itself needs no change there, because `sweep()` reads it.
def _fixture_catalog() -> tuple[SensorRef, ...]:
    entries = load_fixtures().get("catalog") or []
    return tuple(SensorRef(sensor_id=str(e["sensor_id"]), sensor_type=str(e["sensor_type"]), location=str(e.get("location") or ""), status=str(e.get("status") or "online")) for e in entries)


async def _catalog_for_cli(use_fixture: bool) -> tuple[SensorRef, ...]:
    if use_fixture:
        return _fixture_catalog()
    from src.tools.sensors import fetch_catalog  # local: `sensors` imports this module

    ranch_map, source = await fetch_catalog()
    log.info("chaos_cli_catalog", sensors=len(ranch_map.sensors), source=source)
    return ranch_map.sensors


async def _cmd_plan(args: argparse.Namespace) -> int:
    settings = get_settings()
    catalog = await _catalog_for_cli(args.fixture)
    if not catalog:
        print("no catalog, so no plan. Chaos picks targets from the live topology.")
        return 3
    planned = plan(seed=args.seed if args.seed is not None else settings.chaos_seed, catalog=catalog, ticks=args.ticks, cohort=settings.chaos_cohort, ticks_between_events=settings.chaos_ticks_between_events)
    for p in planned:
        print(f"tick {p.tick:>3}  {p.event_id:<16} {p.scenario:<18} {p.fault:<12} {p.target_id:<28} {p.location or '-':<22} ttl={p.ttl_ticks} {json.dumps(p.payload) if p.payload else ''}")
    print(f"\n{len(planned)} events over {args.ticks} ticks, seed {args.seed if args.seed is not None else settings.chaos_seed}. Same seed, same list, every time.")
    return 0


async def _cmd_inject(args: argparse.Namespace) -> int:
    settings = get_settings()
    store = resolve_store()
    catalog = await _catalog_for_cli(args.fixture)
    if not catalog:
        print("no catalog, so nothing to break.")
        return 3
    seed = args.seed if args.seed is not None else settings.chaos_seed
    planned = plan(seed=seed, catalog=catalog, ticks=max(args.tick, 1), cohort=settings.chaos_cohort, ticks_between_events=settings.chaos_ticks_between_events)
    async with store_session(url=store.url, schema=store.schema) as session:
        healed = await expire(session)
        events = await inject_for_tick(session, tick=args.tick, planned=planned, run_id=get_run_id())
        written = await fire_animal_events(events)
    print(f"store={store.name} tick={args.tick} seed={seed}: healed {len(healed)}, injected {len(events)}")
    for e in events:
        print(f"  {e.event_id:<16} {e.scenario:<18} {e.fault:<12} {e.target_id:<28} expires {e.expires_at.isoformat()}")
    for w in written:
        print(f"  animal {w.animal_id}: {'written' if w.performed else 'NOT written'} ({w.reason})")
    return 0


async def _cmd_expire(args: argparse.Namespace) -> int:
    store = resolve_store()
    async with store_session(url=store.url, schema=store.schema) as session:
        healed = await expire(session, force_all=args.all)
    print(f"store={store.name}: expired {len(healed)}{' (forced)' if args.all else ''}")
    for e in healed:
        print(f"  {e.event_id:<16} {e.scenario:<18} {e.fault:<12} {e.target_id}")
    return 0


async def _cmd_status(_args: argparse.Namespace) -> int:
    settings = get_settings()
    store = resolve_store()
    now = datetime.now(UTC)
    async with store_session(url=store.url, schema=store.schema) as session:
        counts = await chaos_counts_by_status(session)
        live = await active_chaos_events(session, now=now)
    print(f"store={store.name}  enabled={settings.chaos_enabled}  seed={settings.chaos_seed}  writes={settings.chaos_allow_writes}  ceiling={settings.chaos_max_active}")
    print(f"rows by status: {counts or 'none'}")
    for row in live:
        event = ChaosEvent.from_row(row)
        print(f"  {event.event_id:<16} {event.scenario:<18} {event.fault:<12} {event.target_id:<28} {event.progress(now):.0%} of its TTL, expires {event.expires_at.isoformat()}")
    return 0


async def _cmd_restore(args: argparse.Namespace) -> int:
    outcomes = await restore_cohort(status=args.status)
    for w in outcomes:
        print(f"  {w.animal_id}: {'restored' if w.performed else 'NOT restored'} ({w.reason})")
    return 0 if all(w.performed for w in outcomes) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.tools.chaos", description="Inject, heal, and inspect the chaos overlay. Seeded, so a demo replays.")
    subs = parser.add_subparsers(dest="command", required=True)

    p_plan = subs.add_parser("plan", help="print what a seed will do, without touching the database")
    p_plan.add_argument("--ticks", type=int, default=6)
    p_plan.add_argument("--seed", type=int, default=None)
    p_plan.add_argument("--fixture", action="store_true", help="use the synthetic catalog in data/examples.json instead of the live ranch")
    p_plan.set_defaults(func=_cmd_plan)

    p_inject = subs.add_parser("inject", help="materialize one tick of the plan into sw_ops.chaos_events")
    p_inject.add_argument("--tick", type=int, default=1)
    p_inject.add_argument("--seed", type=int, default=None)
    p_inject.add_argument("--fixture", action="store_true")
    p_inject.set_defaults(func=_cmd_inject)

    p_expire = subs.add_parser("expire", help="heal what is due, or everything with --all")
    p_expire.add_argument("--all", action="store_true", help="force-expire every active event; heals the whole ranch on demand")
    p_expire.set_defaults(func=_cmd_expire)

    subs.add_parser("status", help="what is currently lying to us").set_defaults(func=_cmd_status)

    p_restore = subs.add_parser("restore", help="put the animal cohort back after a demo. Needs CHAOS_ALLOW_WRITES=1")
    p_restore.add_argument("--status", default=ANIMAL_STATUS_NORMAL, choices=ANIMAL_STATUSES)
    p_restore.set_defaults(func=_cmd_restore)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    return int(asyncio.run(args.func(args)))


if __name__ == "__main__":  # pragma: no cover - the CLI entry point
    raise SystemExit(main())
