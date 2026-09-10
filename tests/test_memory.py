"""The three reconciliation buckets, against a real local Postgres and the real migration.

What these prove: a persisting fault is `ongoing` and never re-alarmed, healing produces
`resolved`, and an upstream that failed to answer resolves nothing. The last one is the
subtle one and it is the reason `read_sensor_ids` exists.

Skipped, loudly, if no local Postgres is up. Never Supabase.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.memory import SCHEMA_TEST, counts_by_status, incidents, open_incidents, reconcile
from src.agent.state import Finding

T0 = datetime(2026, 9, 10, 14, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 10, 14, 5, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 10, 14, 10, 0, tzinfo=UTC)

ALL_SENSORS = ("alkali-flat-water", "east-allotment-fence", "home-place-diesel")


def finding(sensor_id: str = "alkali-flat-water", *, category: str = "water_low", severity: str = "critical", value: float = 1.4, summary: str = "") -> Finding:
    return Finding(
        sensor_id=sensor_id,
        sensor_type="water-level",
        location="Alkali Flat",
        category=category,
        severity=severity,  # type: ignore[arg-type]
        value=value,
        unit=" gal",
        threshold=2.0,
        observed_at="2026-09-10T14:00:00.000Z",
        summary=summary or f"Alkali Flat: stock-tank level reads {value:g} gal on {sensor_id}.",
    )


# --------------------------------------------------------------------------- #
# opened -> ongoing -> resolved
# --------------------------------------------------------------------------- #
async def test_a_new_finding_opens_exactly_once(store: AsyncSession) -> None:
    first = await reconcile(store, [finding()], tick=1, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T0)
    assert first.counts == {"opened": 1, "ongoing": 0, "resolved": 0}
    assert first.opened[0].key == "alkali-flat-water:water_low"
    assert first.opened[0].tick_opened == 1 and first.opened[0].occurrences == 1

    second = await reconcile(store, [finding()], tick=2, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T1)
    assert second.counts == {"opened": 0, "ongoing": 1, "resolved": 0}, "a persisting fault is ongoing, never re-alarmed"

    third = await reconcile(store, [finding()], tick=3, run_id="r1", read_sensor_ids=ALL_SENSORS, now=T2)
    assert third.counts == {"opened": 0, "ongoing": 1, "resolved": 0}
    assert third.ongoing[0].occurrences == 3
    assert third.ongoing[0].tick_opened == 1 and third.ongoing[0].tick_last_seen == 3
    assert third.ongoing[0].first_seen_at == T0 and third.ongoing[0].last_seen_at == T2

    # One row for the whole story, not three.
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 1


async def test_a_finding_that_stops_appearing_resolves(store: AsyncSession) -> None:
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.counts == {"opened": 0, "ongoing": 0, "resolved": 1}
    assert result.resolved[0].resolved_at == T1
    assert await open_incidents(store) == (), "a resolved incident is no longer open"
    assert await counts_by_status(store) == {"resolved": 1}


async def test_the_same_fault_returning_after_a_resolve_is_a_new_incident(store: AsyncSession) -> None:
    """History survives. The same tank drying out in March and again in July is two work
    orders, and collapsing them would overwrite the March story."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    await reconcile(store, [], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)
    again = await reconcile(store, [finding()], tick=3, read_sensor_ids=ALL_SENSORS, now=T2)

    assert again.counts == {"opened": 1, "ongoing": 0, "resolved": 0}
    assert (await store.execute(text("select count(*) from incidents"))).scalar_one() == 2
    assert again.opened[0].tick_opened == 3


# --------------------------------------------------------------------------- #
# the one that matters: no reading is not the same as no problem
# --------------------------------------------------------------------------- #
async def test_a_sensor_that_did_not_answer_resolves_nothing(store: AsyncSession) -> None:
    """The failure this prevents: the Sensor API has a bad five minutes, the sweep returns
    errors instead of readings, every incident on the ranch closes, and the feed reports an
    all-clear at the exact moment nobody can see anything."""
    await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)

    blind = await reconcile(store, [], tick=2, read_sensor_ids=(), now=T1)

    assert blind.counts == {"opened": 0, "ongoing": 0, "resolved": 0}
    assert blind.skipped_unread == ("alkali-flat-water:water_low",)
    still_open = await open_incidents(store)
    assert len(still_open) == 1 and still_open[0].status == "opened"
    assert still_open[0].last_seen_at == T0, "an unread tick must not restamp the incident as freshly seen"


async def test_other_sensors_still_resolve_while_one_is_dark(store: AsyncSession) -> None:
    """Per sensor, not per tick. One dark sensor must not freeze the whole ledger."""
    await reconcile(store, [finding(), finding("east-allotment-fence", category="fence_down")], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [], tick=2, read_sensor_ids=("east-allotment-fence",), now=T1)

    assert [i.sensor_id for i in result.resolved] == ["east-allotment-fence"]
    assert result.skipped_unread == ("alkali-flat-water:water_low",)


# --------------------------------------------------------------------------- #
# severity, and who owns it
# --------------------------------------------------------------------------- #
async def test_severity_follows_the_current_reading_on_an_ongoing_incident(store: AsyncSession) -> None:
    """Code owns severity at every tick, not just the first one. A tank that fills back to
    a warning is still one incident, described accurately."""
    await reconcile(store, [finding(severity="critical", value=1.4)], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    result = await reconcile(store, [finding(severity="warning", value=5.0)], tick=2, read_sensor_ids=ALL_SENSORS, now=T1)

    assert result.ongoing[0].severity == "warning"
    assert result.ongoing[0].last_value == "5"
    row = (await store.execute(text("select severity, last_value, status from incidents"))).one()
    assert tuple(row) == ("warning", "5", "ongoing")


async def test_two_categories_on_one_sensor_are_two_incidents(store: AsyncSession) -> None:
    """A degraded probe reporting a dry tank is two work orders for two different people.
    The key is `sensor:category`, so they never collapse."""
    result = await reconcile(store, [finding(), finding(category="sensor_degraded", severity="warning")], tick=1, read_sensor_ids=ALL_SENSORS, now=T0)
    assert {i.key for i in result.opened} == {"alkali-flat-water:water_low", "alkali-flat-water:sensor_degraded"}


async def test_a_gate_value_is_stored_as_open_not_as_true(store: AsyncSession) -> None:
    gate = Finding(sensor_id="coyote-draw-gate", sensor_type="gate", location="Coyote Draw", category="gate_open", severity="warning", value=True, summary="Coyote Draw: gate coyote-draw-gate reads open.")
    result = await reconcile(store, [gate], tick=1, read_sensor_ids=("coyote-draw-gate",), now=T0)
    assert result.opened[0].last_value == "open"


async def test_the_owner_is_recorded_when_routing_supplies_one(store: AsyncSession) -> None:
    result = await reconcile(store, [finding()], tick=1, read_sensor_ids=ALL_SENSORS, now=T0, owners={"alkali-flat-water:water_low": "water_feed"})
    assert result.opened[0].owner == "water_feed"


# --------------------------------------------------------------------------- #
# the database enforces it, not this module
# --------------------------------------------------------------------------- #
async def test_the_partial_unique_index_forbids_two_live_incidents_on_one_key(store: AsyncSession) -> None:
    """Enforced in Postgres rather than by application code remembering to check first.
    If this fails, migration 0001 lost its partial index and duplicate alarms are one
    concurrent tick away."""
    row = {
        "incident_key": "alkali-flat-water:water_low",
        "sensor_id": "alkali-flat-water",
        "sensor_type": "water-level",
        "location": "Alkali Flat",
        "category": "water_low",
        "severity": "critical",
        "status": "opened",
        "summary": "s",
        "first_seen_at": T0,
        "last_seen_at": T0,
    }
    await store.execute(incidents.insert(), row)
    with pytest.raises(IntegrityError):
        await store.execute(incidents.insert(), {**row, "status": "ongoing"})
    await store.rollback()


async def test_the_connection_can_only_see_the_agent_schema(store: AsyncSession) -> None:
    """The live half of the schema guard. This database also holds the upstream project's
    `farm`, `feed`, `animal_care`, and `sensor` schemas, and this connection cannot reach
    any of them without qualifying, which nothing in this repo does."""
    assert (await store.execute(text("select current_schemas(false)"))).scalar_one() == [SCHEMA_TEST]
    assert (await store.execute(text("select current_setting('search_path')"))).scalar_one() == SCHEMA_TEST


async def test_the_migration_put_alembic_version_in_our_schema(store: AsyncSession) -> None:
    """Alembic's default drops `alembic_version` into the first schema on the search path.
    On this database that has to be `sw_ops_test`, not a table belonging to the upstream
    project's own migrations."""
    found = (await store.execute(text("select count(*) from information_schema.tables where table_schema = :s and table_name = 'alembic_version'"), {"s": SCHEMA_TEST})).scalar_one()
    assert found == 1
