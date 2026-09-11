"""The herd sweep: the second free pass, off the Farm and Care APIs, under the sensor sweep's rules.

M7A. Until this module existed nothing in the tick read the Care API: the free pass was a sensor
sweep, triage a sensor truth table, and `herd_health` cannot read a sensor, so a dead cow written
by chaos was invisible to the monitor. This is the discovery path. It goes direct over httpx like
`sensors.py`, for the same reason: there is no judgment in "is this animal's status `deceased`",
so there is no reason to pay a tool call for it.

**The shape was decided by two wire facts, both read on 2026-09-11 and both in `docs/STATE.md`.**

  * **The herd is 1,195 head and `GET /animals` runs at roughly 90 ms a row.** `limit=100` takes
    9s, `limit=200` 17s, and `limit=500` hits the API Gateway 30-second wall and comes back 503
    every time. So the whole catalog cannot be read in one call, ever. Paged at 100 a page it is
    12 requests, every row carrying its real status, and **the Farm API fails under concurrency**:
    at 12 or 20 pages in flight 2 of 12 came back HTTP 500 on every attempt, at 6 in flight all 12
    were 200 every time (measured five waves, 2026-09-11). So the pages go out in waves of
    `HERD_PAGE_CONCURRENCY`, stopping at the first short page, about 18s for this herd.
  * **The `status` filter cannot be used to find a non-active animal.** It is validated (a bad
    value 422s naming the enum) and `status=active` does exclude a deceased cow, but
    `status=deceased` returned **nothing** with cow-0905 recorded deceased and sitting in the
    unfiltered list. The first design read the three non-active statuses in half a second each and
    the first live kill was invisible to it. So the list is read whole and the status is read off
    each row, which is the only thing the wire actually vouches for. There is no pastureless
    filter either (`pastureId=null` and friends return empty).
  * **`GET /pastures?limit=50` returns every pasture with its `animalIds` inline in one call**, and
    that is still read, for the packet's head count and as the second source: a herd list that
    disagrees with the roster by more than the pastureless animals is logged.
  * **Observations list per animal only.** `GET /observations` is a 404, the MCP tool requires
    `animalId`, and every since-style parameter is silently ignored on the per-animal route.
    There is no ranch-wide "what changed since T". Reading all 1,195 is ~21s a tick and 344k
    Care requests a day, so observations are fetched only for the animals whose state changed:
    the non-active set, plus the animals named on a pending care task. About ten reads today.

**An animal with a live incident is vouched for by the list, never by the roster.** Found on the
first live kill: the Farm API **nulls `pastureId` when it patches an animal to `deceased`**, so the
dead cow leaves every pasture roster, and when `chaos restore` puts her back to `active` she is on
no roster at all. The list still carries her, and the list is what `answered` is built from.
`sweep_herd` takes `watch`, the animal subjects of every live incident (read off the ledger by the
executor), so an animal that has left the list entirely is recorded as a subject that did not
answer rather than one that is fine, and so her record is always in the changed set for her packet.

**What this cannot see, on purpose, and it is written down in `docs/issues.md`:** a `high`
observation on an `active` animal with no care task. The coyote kill is caught through the
status, which chaos patches on the Farm API, and the observation is then read for that animal
and quoted in its packet.

**The three rules inherited from `sensors.py`, each with a rail:**

  * **Errors are returned as data, never raised, and nothing here retries.** Retry policy belongs
    to the caller.
  * **An empty herd catalog fails the stage rather than reading as an empty herd.** A roster with
    no animals in it downstream is indistinguishable from a ranch where every cow is fine, which
    is the worst available way to be wrong, so it is `failure`, not an empty tuple.
  * **The stage returns the subjects that actually answered.** `reconcile` resolves only those. A
    Care API outage therefore resolves no animal: the deceased finding still opens (the status is
    on the Farm API and answered), but nothing closes on the strength of a record we could not read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from src.utils.config import get_settings
from src.utils.helpers import gather_bounded, upstream_client
from src.utils.logger import get_logger

log = get_logger(__name__)

#: One page of the herd list. 100 rows is ~9s on the deployed Farm API against a 15s upstream
#: timeout and the API Gateway's 30s wall; 200 is 17s and too close to both. Smaller pages mean more
#: requests for no time saved, because the pages run concurrently.
HERD_PAGE = 100
#: The ceiling on pages. 20 x 100 is 2,000 head against a ranch of roughly 1,000 cows plus a band of
#: sheep (1,195 on 2026-09-11). If every page comes back full the herd exceeds the ceiling and the
#: stage fails rather than silently reading the first 2,000 as the whole herd.
HERD_MAX_PAGES = 20
#: Pages in flight at once, and it is a wave size, not `SWEEP_CONCURRENCY`: the deployed Farm API
#: returned HTTP 500 on 2 of 12 pages at 12 or 20 in flight, every time, and 12 of 12 at 6. Measured
#: 2026-09-11, five waves. Paging stops at the first short page, so this herd is two waves.
HERD_PAGE_CONCURRENCY = 6
PASTURE_LIMIT = 50  # 18 exist; the ceiling is here so a new pasture does not silently page
CARE_TASK_LIMIT = 500
#: Per animal, newest first. Enough to carry the ranch's whole history for one cow (cow-0001 has
#: four) and small enough to render whole in a packet.
OBSERVATION_LIMIT = 50

ANIMAL_STATUS_ACTIVE = "active"
#: The Farm API's enum minus `active`, read off its 422 on 2026-09-11. Any of these on a list row
#: puts the animal in the changed set. `sold` included, so triage can rule it out **explicitly**: a
#: sold animal is a ranch running normally, not a finding.
NON_ACTIVE_STATUSES: tuple[str, ...] = ("deceased", "inactive", "sold")
CARE_TASK_PENDING = "pending"

_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def parse_timestamp(raw: object) -> datetime | None:
    """Upstream `observedAt` / `dueAt` / `updatedAt`, UTC ISO 8601 with milliseconds. `None` for
    anything else: a missing timestamp is a stated absence downstream, never `now()`."""
    if not isinstance(raw, str) or not _TS.match(raw):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AnimalRecord:
    """One row of `GET /animals`. `status` is the Farm API's enum; chaos PATCHes it for real."""

    animal_id: str
    name: str
    species: str
    sex: str
    status: str
    pasture_id: str
    shelter_id: str
    updated_at: str


@dataclass(frozen=True)
class Observation:
    """One row of `GET /animals/:id/observations`. Append-only upstream; `note` is what a human reads."""

    observation_id: str
    animal_id: str
    type: str
    severity: str
    note: str
    observed_at: str

    @property
    def observed(self) -> datetime | None:
        return parse_timestamp(self.observed_at)


@dataclass(frozen=True)
class CareTask:
    """One row of `GET /care-tasks`. `due_at` against the clock is the whole of `care_overdue`."""

    task_id: str
    animal_id: str
    title: str
    due_at: str
    status: str
    notes: str

    @property
    def due(self) -> datetime | None:
        return parse_timestamp(self.due_at)


@dataclass(frozen=True)
class HerdError:
    """An upstream failure as data. `subject` is the animal id, or the read that failed
    (`roster`, `status:deceased`, `care_tasks`) when the failure was not about one animal."""

    subject: str
    category: str
    message: str
    retriable: bool = True
    status_code: int | None = None


# --------------------------------------------------------------------------- #
# the pasture roster: one call per tick, flat. Shared with `evidence.py`, which re-exports it
# --------------------------------------------------------------------------- #
_SLUG = re.compile(r"[^a-z0-9]+")
#: The two upstream surfaces spell a location differently. `GET /sensors/:id` returns
#: `locationName: "Alkali Flat"`, while the `ranch://sensors/map` resource labels the same
#: group `"Alkali Flat (alkali-flat)"`. Incidents carry the REST form because that is what
#: the sweep parses, but a location string from the map must not slug to
#: `alkali-flat-alkali-flat` and silently match no pasture. When the id is spelled out in
#: parentheses, take it rather than re-deriving it. An animal incident carries the pasture id
#: itself as its location, which slugs to itself.
_PARENTHESIZED_ID = re.compile(r"\(([a-z0-9][a-z0-9-]*)\)\s*$")


def slugify(location: str) -> str:
    text = location.strip()
    embedded = _PARENTHESIZED_ID.search(text.lower())
    if embedded:
        return embedded.group(1)
    return _SLUG.sub("-", text.lower()).strip("-")


@dataclass(frozen=True)
class PastureContext:
    """What is standing behind the problem. The head count is why one tank goes first, and from
    M7A `animal_ids` is the herd roster the sweep's plausibility check reads."""

    pasture_id: str
    name: str
    acreage: int | None
    fence_type: str
    status: str
    head_count: int
    animal_ids: tuple[str, ...] = ()

    def render(self) -> str:
        acres = f"{self.acreage} acres, " if self.acreage else ""
        return f"{self.name} ({self.pasture_id}): {acres}{self.head_count} head on it, {self.fence_type or 'fence type unrecorded'}, pasture status {self.status or 'unrecorded'}"


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
        if not location:
            return None, "no pasture is recorded for this animal"
        # Not every sensor location is a pasture. A spring, a well, a barn, and the weather
        # station are all real locations with no cattle standing on them, and saying so is
        # better than implying the herd count is zero.
        return None, f"no pasture is mapped to {location}; it is a site rather than grazing ground"

    @property
    def animal_ids(self) -> frozenset[str]:
        """Every animal any pasture names. 1,195 on 2026-09-11. Zero is a failed read, never a calm ranch."""
        return frozenset(a for p in self.pastures for a in p.animal_ids)


def parse_pastures(payload: Any) -> tuple[PastureContext, ...]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return ()
    out: list[PastureContext] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        animal_ids = raw.get("animalIds")
        ids = tuple(str(a) for a in animal_ids if a) if isinstance(animal_ids, list) else ()
        acreage = raw.get("acreage")
        out.append(
            PastureContext(
                pasture_id=str(raw.get("id") or ""),
                name=str(raw.get("name") or raw.get("id") or "unnamed"),
                acreage=int(acreage) if isinstance(acreage, (int, float)) else None,
                fence_type=str(raw.get("fenceType") or ""),
                status=str(raw.get("status") or ""),
                head_count=len(ids),
                animal_ids=ids,
            )
        )
    return tuple(p for p in out if p.pasture_id)


async def fetch_pasture_roster() -> PastureRoster:
    """One call. Errors come back as data, on the same rule as `sensors.py`.

    A missing roster must not fail the packet: a work order that names the tank and admits
    it does not know the head count is useful, and one that never got written because the
    farm API hiccuped is not. (The herd sweep holds the roster to a stricter standard, because
    for it the roster is the catalog: see `sweep_herd`.)
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
# parsing the Farm and Care envelopes
# --------------------------------------------------------------------------- #
def _rows(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def parse_animal(raw: Any) -> AnimalRecord | None:
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    return AnimalRecord(
        animal_id=str(raw["id"]),
        name=str(raw.get("name") or ""),
        species=str(raw.get("species") or "animal"),
        sex=str(raw.get("sex") or ""),
        status=str(raw.get("status") or ""),
        pasture_id=str(raw.get("pastureId") or ""),
        shelter_id=str(raw.get("shelterId") or ""),
        updated_at=str(raw.get("updatedAt") or ""),
    )


def parse_animals(payload: Any) -> tuple[AnimalRecord, ...]:
    return tuple(a for a in (parse_animal(r) for r in _rows(payload)) if a is not None)


def parse_observations(payload: Any, *, animal_id: str) -> tuple[Observation, ...]:
    """Newest first, which is the order the upstream returns and the order a human wants."""
    out = [
        Observation(observation_id=str(r.get("id") or ""), animal_id=str(r.get("animalId") or animal_id), type=str(r.get("type") or "general"), severity=str(r.get("severity") or ""), note=str(r.get("note") or ""), observed_at=str(r.get("observedAt") or ""))
        for r in _rows(payload)
    ]
    return tuple(sorted(out, key=lambda o: o.observed_at, reverse=True))


def parse_care_tasks(payload: Any) -> tuple[CareTask, ...]:
    return tuple(
        CareTask(task_id=str(r.get("id") or ""), animal_id=str(r.get("animalId") or ""), title=str(r.get("title") or ""), due_at=str(r.get("dueAt") or ""), status=str(r.get("status") or ""), notes=str(r.get("notes") or ""))
        for r in _rows(payload)
        if r.get("id") and r.get("animalId")
    )


# --------------------------------------------------------------------------- #
# one read, as data
# --------------------------------------------------------------------------- #
async def _get(client: httpx.AsyncClient, path: str, *, subject: str, params: dict[str, Any] | None = None) -> tuple[Any, HerdError | None]:
    """`(payload, None)` or `(None, error)`. Classified exactly like `sensors.read_sensor`."""
    try:
        response = await client.get(path, params=params)
    except httpx.TimeoutException as exc:
        return None, HerdError(subject=subject, category="timeout", message=f"{type(exc).__name__}: {exc}", retriable=True)
    except httpx.HTTPError as exc:
        return None, HerdError(subject=subject, category="transport", message=f"{type(exc).__name__}: {exc}", retriable=True)
    if response.status_code >= 400:
        retriable = response.status_code >= 500 or response.status_code == 429
        return None, HerdError(subject=subject, category="http_error", message=f"HTTP {response.status_code}", retriable=retriable, status_code=response.status_code)
    try:
        return response.json(), None
    except ValueError:
        return None, HerdError(subject=subject, category="bad_response", message="upstream returned non-JSON", retriable=True, status_code=response.status_code)


# --------------------------------------------------------------------------- #
# the result
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HerdSweepResult:
    """What the herd stage learned this tick, and how much of it can be believed.

    `animals` is the **changed set** with a record each: every non-active animal, every animal named
    on a pending care task, and every watched animal, as the herd list carried them. `observations`
    and `care_tasks` are for that set only. `answered` is the subjects `reconcile` may resolve: every
    animal on the list, minus any whose own observation read failed or who is watched and gone, and
    **empty whenever `failure` is set**, because a stage that could not read the herd has no business
    closing a cow's incident.
    """

    roster: PastureRoster = field(default_factory=PastureRoster)
    animals: tuple[AnimalRecord, ...] = ()
    observations: dict[str, tuple[Observation, ...]] = field(default_factory=dict)
    care_tasks: tuple[CareTask, ...] = ()
    errors: tuple[HerdError, ...] = ()
    answered: frozenset[str] = frozenset()
    #: Empty when the stage can be believed. Otherwise why not, in one phrase for the tick line.
    failure: str = ""
    requests: int = 0

    @property
    def ok(self) -> bool:
        return not self.failure

    @property
    def animal_ids(self) -> frozenset[str]:
        """The changed set: every animal this sweep read a record for."""
        return frozenset(a.animal_id for a in self.animals)

    def record(self, animal_id: str) -> AnimalRecord | None:
        return next((a for a in self.animals if a.animal_id == animal_id), None)

    def observations_for(self, animal_id: str) -> tuple[Observation, ...]:
        return self.observations.get(animal_id, ())

    def tasks_for(self, animal_id: str) -> tuple[CareTask, ...]:
        return tuple(t for t in self.care_tasks if t.animal_id == animal_id)

    def herd_mates(self, pasture_id: str, *, excluding: str) -> tuple[AnimalRecord, ...]:
        """The other animals **in the changed set** standing in the same pasture. Five cows read
        deceased in one pasture is a different afternoon from one, and this is where a packet sees it."""
        return tuple(a for a in self.animals if a.pasture_id == pasture_id and a.animal_id != excluding and pasture_id)


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
async def sweep_herd(*, watch: Iterable[str] = (), limit: int | None = None) -> HerdSweepResult:
    """Read the herd off the Farm and Care APIs, bounded, errors as data.

    Request bill per tick: the roster and `HERD_MAX_PAGES` list pages fired together (21, of which 8
    are empty tails today), one care-task read, then one observation read per animal in the changed
    set. About 25 on 2026-09-11, roughly 12s of wall clock, against the sensor sweep's 160 and 3s.

    The order matters for what `answered` means. Every list page has to come back for any animal to
    have answered, because "not in the list as deceased" only means "not deceased" if the whole list
    came back; and the care-task read has to succeed too, because an `ongoing` care_overdue incident
    can only be resolved by a task list that answered. After that, failures are per animal and only
    that animal drops out of `answered`.

    `watch` is the animal subjects of the ledger's live incidents. Each is put in the changed set so
    its packet has a record, and one that is not on the list at all is a subject that did not answer.
    """
    settings = get_settings()
    ceiling = limit or settings.sweep_concurrency
    errors: list[HerdError] = []
    watched = frozenset(watch)
    requests = 0

    # --- Farm: the roster, then the whole list in waves ---------------------------------------
    farm_ok = True
    herd: list[AnimalRecord] = []
    async with upstream_client(settings.farm_api) as farm:
        roster_payload, roster_error = await _get(farm, "/pastures", subject="roster", params={"limit": PASTURE_LIMIT})
        requests += 1
        if roster_error is not None:
            # The roster is context, not the catalog: a packet says "pasture roster unavailable"
            # and the sweep carries on. The list is what decides who answered.
            errors.append(roster_error)
            roster = PastureRoster(error=roster_error.message)
        else:
            roster = PastureRoster(pastures=parse_pastures(roster_payload))

        offsets = [i * HERD_PAGE for i in range(HERD_MAX_PAGES)]
        wave = min(HERD_PAGE_CONCURRENCY, ceiling)
        read_whole = False
        for start in range(0, len(offsets), wave):
            batch = offsets[start : start + wave]
            raw = await gather_bounded([_get(farm, "/animals", subject=f"page:{offset}", params={"limit": HERD_PAGE, "offset": offset}) for offset in batch], limit=wave)
            requests += len(batch)
            short = False
            for offset, outcome in zip(batch, raw, strict=True):
                payload, error = outcome if isinstance(outcome, tuple) else (None, HerdError(subject=f"page:{offset}", category="unexpected", message=f"{type(outcome).__name__}: {outcome}", retriable=False))
                if error is not None:
                    errors.append(error)
                    farm_ok = False
                    continue
                rows = parse_animals(payload)
                herd.extend(rows)
                if len(rows) < HERD_PAGE:
                    short = True
            if short or not farm_ok:
                # A short page is the end of the herd (the pages are contiguous, so nothing lies past
                # it); a failed page is a failed stage and the rest of the waves buy nothing.
                read_whole = short and farm_ok
                break
        else:
            # Every page full, including the last. The herd exceeds the ceiling and was not read whole.
            farm_ok = False
            errors.append(HerdError(subject=f"page:{offsets[-1]}", category="ceiling", message=f"all {HERD_MAX_PAGES} pages were full; the herd exceeds {HERD_PAGE * HERD_MAX_PAGES} and was not read whole", retriable=False))
        if farm_ok and read_whole and not herd:
            # The plausibility check. 200 with a perfect empty envelope is the one failure no retry
            # layer can see, and an empty herd downstream reads as every cow being fine.
            farm_ok = False
            errors.append(HerdError(subject="page:0", category="empty_catalog", message="200 with no animals on any page", retriable=True))
    listed = {a.animal_id: a for a in herd}
    if roster.pastures and farm_ok:
        off_roster = sorted(set(listed) - roster.animal_ids)
        if len(off_roster) > sum(1 for a in herd if not a.pasture_id):
            log.warning("herd_roster_disagrees", listed=len(listed), on_roster=len(roster.animal_ids), off_roster=len(off_roster), hint="animals in the list that no pasture names, beyond the pastureless ones; the list is what the sweep vouches for")

    non_active = [a for a in herd if a.status != ANIMAL_STATUS_ACTIVE]

    # --- Care: the pending tasks, then observations for the changed set --------------------------
    async with upstream_client(settings.care_api) as care:
        tasks_payload, tasks_error = await _get(care, "/care-tasks", subject="care_tasks", params={"status": CARE_TASK_PENDING, "limit": CARE_TASK_LIMIT})
        requests += 1
        care_ok = tasks_error is None
        if tasks_error is not None:
            errors.append(tasks_error)
        care_tasks = parse_care_tasks(tasks_payload) if care_ok else ()

        changed = sorted({a.animal_id for a in non_active} | {t.animal_id for t in care_tasks} | watched)
        failed_animals: set[str] = set()
        records: list[AnimalRecord] = []
        for animal_id in changed:
            record = listed.get(animal_id)
            if record is not None:
                records.append(record)
            elif farm_ok and animal_id in watched:
                # A watched animal that has left the list is not fine, it is gone. Not answered.
                errors.append(HerdError(subject=animal_id, category="not_in_herd", message="a watched animal is not on the herd list", retriable=False))
                failed_animals.add(animal_id)

        observations: dict[str, tuple[Observation, ...]] = {}
        if care_ok and changed:
            raw_obs = await gather_bounded([_get(care, f"/animals/{animal_id}/observations", subject=animal_id, params={"limit": OBSERVATION_LIMIT}) for animal_id in changed], limit=ceiling)
            requests += len(changed)
            for animal_id, outcome in zip(changed, raw_obs, strict=True):
                payload, error = outcome if isinstance(outcome, tuple) else (None, HerdError(subject=animal_id, category="unexpected", message=f"{type(outcome).__name__}: {outcome}", retriable=False))
                if error is not None:
                    errors.append(error)
                    failed_animals.add(animal_id)
                    continue
                observations[animal_id] = parse_observations(payload, animal_id=animal_id)

    failure = ""
    if not farm_ok:
        failure = "farm: " + "; ".join(f"{e.subject} {e.category} {e.message}" for e in errors if e.subject.startswith("page:"))
    elif not care_ok:
        failure = "care: " + "; ".join(f"{e.subject} {e.category} {e.message}" for e in errors if e.subject == "care_tasks")

    answered = frozenset() if failure else frozenset(listed) - failed_animals
    result = HerdSweepResult(roster=roster, animals=tuple(records), observations=observations, care_tasks=care_tasks, errors=tuple(errors), answered=answered, failure=failure, requests=requests)
    if failure:
        log.error("herd_sweep_failed", failure=failure, requests=requests, errors=[f"{e.subject}: {e.category} {e.message}" for e in errors], hint="no animal answered this tick, so no animal incident resolves; findings from what did come back still open")
    elif errors:
        log.warning("herd_sweep_partial", requests=requests, failed=len(errors), errors=[f"{e.subject}: {e.category} {e.message}" for e in errors])
    log.info("herd_swept", requests=requests, listed=len(listed), roster=len(roster.animal_ids), changed=len(records), non_active=len(non_active), watched=len(watched), care_tasks=len(care_tasks), observations=sum(len(v) for v in observations.values()), answered=len(answered), errors=len(errors))
    return result
