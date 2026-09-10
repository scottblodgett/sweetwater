"""The sweep: errors come back as data, gates stay boolean, and fan-out stays bounded.

Nothing here touches the live ranch. `pytest` has to pass on a plane, so every upstream
is a respx route against a fake host.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from src.tools.mcp_client import SensorRef
from src.tools.sensors import SensorReading, SweepError, parse_sensor_payload, read_sensor, sweep
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


def payload(value: object, *, status: str = "online") -> dict[str, object]:
    return {"data": {"id": REF.sensor_id, "type": REF.sensor_type, "locationName": "Alkali Flat", "status": status, "latestReading": {"value": value, "recordedAt": FROZEN_TS}}}


# --------------------------------------------------------------------------- #
# parsing one payload
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# errors are data, and nothing here retries
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# the sweep
# --------------------------------------------------------------------------- #
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
