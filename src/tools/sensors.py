"""The sweep: one catalog read, then ~160 bounded live reads per tick.

Two shapes here are deliberate and both cost something to rediscover:

  * **The catalog is read once, the values are read every tick.** `ranch://sensors/map`
    is topology and is stable; `GET /sensors/:id` synthesizes a fresh value on every
    call. Caching the second one produces a ranch that looks alive and is not.
  * **The sweep goes direct to `SENSOR_API` over httpx, not through the MCP tool
    layer.** 160 reads per tick through a tool wrapper is pure overhead: there is no
    judgment in a threshold comparison, so there is no reason to pay a tool call for
    it. The MCP tools exist for the sub-agents, which do exercise judgment.

Every failure in this module is **returned as a value**, never raised, and nothing here
retries. Classification belongs to whoever saw the status line; retry policy belongs to
the caller, because attempts, budget, and deadline are the caller's to spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from src.tools.mcp_client import McpUnavailableError, RanchMap, SensorRef, parse_ranch_map, ranch_session, read_ranch_map
from src.utils.config import get_settings
from src.utils.helpers import gather_bounded, upstream_client
from src.utils.logger import get_logger

log = get_logger(__name__)

CATALOG_LIMIT = 500  # the whole catalog in one page; `GET /sensors` takes pagination only


@dataclass(frozen=True)
class SensorReading:
    """One live value for one sensor, at one instant. Never cached, never reused.

    `value` is `None` for a dark sensor, `bool` for a gate, and a number for everything
    else. Keeping the three distinguishable is triage's whole input: `None` is "we do
    not know", `0` is "we know, and it is zero", and those two want different work
    orders.
    """

    sensor_id: str
    sensor_type: str
    location: str
    status: str
    value: float | bool | None
    recorded_at: str | None = None


@dataclass(frozen=True)
class SweepError:
    """An upstream failure as data. `category` and `retriable` mirror the upstream envelope."""

    sensor_id: str
    category: str
    message: str
    retriable: bool = True
    status_code: int | None = None


@dataclass(frozen=True)
class SweepResult:
    readings: tuple[SensorReading, ...] = ()
    errors: tuple[SweepError, ...] = ()

    @property
    def attempted(self) -> int:
        return len(self.readings) + len(self.errors)


# --------------------------------------------------------------------------- #
# the catalog
# --------------------------------------------------------------------------- #
async def fetch_catalog() -> tuple[RanchMap, str]:
    """The sensor topology, from the MCP resource, falling back to the REST catalog.

    Two sources on purpose. The known undetectable failure is an upstream that answers
    **200 with a structurally perfect empty envelope**: no retry layer can see it,
    because nothing is wrong at the protocol level. An empty map downstream is
    indistinguishable from a calm ranch, which is the worst possible way to be wrong,
    so a zero-sensor map is treated as a failed read and the second source is asked.
    """
    try:
        async with ranch_session() as session:
            ranch_map = await read_ranch_map(session)
        if ranch_map.sensors:
            return ranch_map, "mcp_resource"
        log.warning("catalog_empty_from_resource", hint="an empty map reads downstream as a calm ranch; falling back to the REST catalog")
    except McpUnavailableError as exc:
        log.warning("catalog_resource_unavailable", error=str(exc), hint="falling back to GET /sensors")

    settings = get_settings()
    try:
        async with upstream_client(settings.sensor_api) as client:
            response = await client.get("/sensors", params={"limit": CATALOG_LIMIT})
            response.raise_for_status()
            ranch_map = parse_ranch_map(response.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.error("catalog_unavailable", error=f"{type(exc).__name__}: {exc}")
        return RanchMap(), "unavailable"

    if not ranch_map.sensors:
        log.error("catalog_empty_from_rest", hint="both sources returned an empty catalog; the tick has nothing to sweep")
        return ranch_map, "unavailable"
    return ranch_map, "rest_fallback"


# --------------------------------------------------------------------------- #
# one read
# --------------------------------------------------------------------------- #
def parse_sensor_payload(ref: SensorRef, payload: Any) -> SensorReading | SweepError:
    """`{ "data": { ...catalog entry..., "latestReading": { value, recordedAt } } }`.

    A dark sensor answers `status: "offline"` with `latestReading: null`, which is a
    successful read of a sensor that has nothing to say - a `SensorReading` with a
    `None` value, not an error. Triage owns what that means; this layer does not.
    """
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(data, dict) or not data:
        return SweepError(sensor_id=ref.sensor_id, category="bad_response", message="200 with no sensor object in the envelope", retriable=True)

    latest = data.get("latestReading")
    value: float | bool | None = None
    recorded_at: str | None = None
    if isinstance(latest, dict):
        raw = latest.get("value")
        # bool before int: `isinstance(True, int)` is True in Python, so testing for a
        # number first turns every gate into the number 1 and loses the only type whose
        # triage rule is not a threshold comparison.
        if isinstance(raw, bool):
            value = raw
        elif isinstance(raw, (int, float)):
            value = float(raw)
        recorded = latest.get("recordedAt")
        recorded_at = str(recorded) if recorded is not None else None

    return SensorReading(
        sensor_id=str(data.get("id") or ref.sensor_id),
        sensor_type=str(data.get("type") or ref.sensor_type),
        location=str(data.get("locationName") or data.get("location") or ref.location),
        status=str(data.get("status") or ref.status),
        value=value,
        recorded_at=recorded_at,
    )


async def read_sensor(client: httpx.AsyncClient, ref: SensorRef) -> SensorReading | SweepError:
    """One sensor, one read, no retry. Every failure comes back as a `SweepError`."""
    try:
        response = await client.get(f"/sensors/{ref.sensor_id}")
    except httpx.TimeoutException as exc:
        return SweepError(sensor_id=ref.sensor_id, category="timeout", message=f"{type(exc).__name__}: {exc}", retriable=True)
    except httpx.HTTPError as exc:
        return SweepError(sensor_id=ref.sensor_id, category="transport", message=f"{type(exc).__name__}: {exc}", retriable=True)

    if response.status_code >= 400:
        # 4xx is the caller's fault and retrying it is how a helper spins forever on a
        # 422. The distinction is recorded here and acted on nowhere in this layer.
        retriable = response.status_code >= 500 or response.status_code == 429
        return SweepError(sensor_id=ref.sensor_id, category="http_error", message=f"HTTP {response.status_code}", retriable=retriable, status_code=response.status_code)

    try:
        payload = response.json()
    except ValueError:
        return SweepError(sensor_id=ref.sensor_id, category="bad_response", message="upstream returned non-JSON", retriable=True, status_code=response.status_code)
    return parse_sensor_payload(ref, payload)


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
async def sweep(refs: tuple[SensorRef, ...] | list[SensorRef], *, limit: int | None = None) -> SweepResult:
    """Read every sensor in `refs`, bounded by `SWEEP_CONCURRENCY`.

    Bounded, always. 160 unbounded requests against API Gateway is a wall of Lambda
    cold starts and reads to the other side as a load test rather than a monitoring
    sweep. The ceiling is a rail in `tests/`, not a suggestion.

    Chaos does not appear here yet. At M5 the overlay in `sw_ops.chaos_events` is
    applied to these honest readings before triage sees them, because the deployed
    Sensor API stays truthful and this repo owns the lie in exactly one place.
    """
    settings = get_settings()
    ceiling = limit or settings.sweep_concurrency
    if not refs:
        return SweepResult()

    async with upstream_client(settings.sensor_api) as client:
        raw = await gather_bounded([read_sensor(client, ref) for ref in refs], limit=ceiling)

    readings: list[SensorReading] = []
    errors: list[SweepError] = []
    for ref, outcome in zip(refs, raw, strict=True):
        if isinstance(outcome, SensorReading):
            readings.append(outcome)
        elif isinstance(outcome, SweepError):
            errors.append(outcome)
        else:
            # `gather_bounded` returns exceptions as values. Reaching here means a read
            # raised something `read_sensor` does not catch; it still has to come out as
            # a per-sensor error, because one bad sensor is not a failed sweep.
            errors.append(SweepError(sensor_id=ref.sensor_id, category="unexpected", message=f"{type(outcome).__name__}: {outcome}", retriable=False))

    if errors:
        log.warning("sweep_partial", attempted=len(refs), failed=len(errors), categories=sorted({e.category for e in errors}))
    return SweepResult(readings=tuple(readings), errors=tuple(errors))
