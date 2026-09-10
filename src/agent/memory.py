"""`sw_ops`, and nothing else. The incident ledger and the reconciliation that fills it.

Three things here are load-bearing, and each one exists because of a specific way this
goes wrong:

  * **The schema is pinned on the connection, not written into the SQL.** Every
    statement in this module names `incidents` unqualified and the engine sets
    `search_path` to the one schema this repo owns. The connection that reaches `sw_ops`
    also has write access to the ranch's `farm`, `feed`, `animal_care`, and `sensor`
    schemas and must never use it. A schema this repo does not own is not something to
    remember not to type; it is something the connection cannot reach.
  * **`opened` happens once.** A persisting fault is `ongoing`, forever if need be.
    Duplicate alerts train the client to ignore the service.
  * **A sensor that failed to read resolves nothing.** "No finding" and "no reading" are
    different facts, and conflating them means a single upstream outage closes every
    incident on the ranch and reports an all-clear at the worst possible moment.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Float, Index, Integer, MetaData, Table, Text, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.agent.state import Finding, Incident, IncidentStatus, Severity
from src.utils.config import get_settings
from src.utils.logger import get_logger

log = get_logger(__name__)

SCHEMA = "sw_ops"
SCHEMA_TEST = "sw_ops_test"

#: The only two schemas anything in this repo may create, write, or drop.
ALLOWED_SCHEMAS = frozenset({SCHEMA, SCHEMA_TEST})
#: The upstream project's schemas, in the same databases, reachable by the same
#: credentials. Named here so the guard's error message can say what went wrong.
RANCH_SCHEMAS = frozenset({"farm", "feed", "animal_care", "sensor"})


class SchemaGuardError(RuntimeError):
    """A statement was aimed at a schema this repo does not own."""


def assert_agent_schema(name: str) -> str:
    """Gate every engine and every migration. Refuses anything but `sw_ops*`.

    The accident this prevents cost three answer keys in a previous life of this project:
    a test suite pointed at the same database the demo reads from, dropping and
    recreating a schema that was not its own.
    """
    if name not in ALLOWED_SCHEMAS:
        detail = " That is a ranch schema, owned by the frozen upstream and reachable only over HTTP from here." if name in RANCH_SCHEMAS else ""
        raise SchemaGuardError(f"refusing to operate on schema {name!r}. This repo owns {sorted(ALLOWED_SCHEMAS)} and nothing else.{detail}")
    return name


def assert_local_test_url(url: str) -> str:
    """The test suite gets a local database or it gets nothing.

    A suite that drops and recreates a schema against the same database the demo reads
    from is the exact accident that invalidated three answer keys here before. The check
    is on the host rather than on intent, because intent is not what fails.
    """
    lowered = url.lower()
    if not lowered:
        raise SchemaGuardError("DATABASE_URL_TEST is empty. The suite needs a local Postgres; it must never fall back to DATABASE_URL.")
    for banned in ("supabase.co", "supabase.com", "pooler.supabase"):
        if banned in lowered:
            raise SchemaGuardError(f"DATABASE_URL_TEST points at Supabase ({banned}). Tests use a local Postgres, always. See tests/CLAUDE.md.")
    return url


def assert_droppable_schema(name: str) -> str:
    """Stricter still: only the test schema may ever be dropped.

    `sw_ops` holds the incident history the demo reads. A `DROP SCHEMA` that a test can
    reach is a `DROP SCHEMA` that will eventually run against the wrong database.
    """
    if name != SCHEMA_TEST:
        raise SchemaGuardError(f"refusing to drop schema {name!r}. Only {SCHEMA_TEST!r} is ever droppable, and only by the test suite.")
    return name


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
# No `schema=` on this MetaData, on purpose. The same Table definition has to work
# against `sw_ops` in Supabase and `sw_ops_test` locally, and the connection decides
# which. Hardcoding either one here is how the test suite ends up writing to prod.
metadata = MetaData()

incidents = Table(
    "incidents",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    # `sensor:category`, not a foreign key to anything. Cross-service IDs are plain
    # strings here; the sensor lives in another service and orphans are allowed.
    Column("incident_key", Text, nullable=False),
    Column("sensor_id", Text, nullable=False),
    Column("sensor_type", Text, nullable=False),
    Column("location", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("last_value", Text, nullable=True),
    Column("unit", Text, nullable=False, server_default=""),
    Column("threshold", Float, nullable=True),
    Column("occurrences", Integer, nullable=False, server_default="1"),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    Column("tick_opened", Integer, nullable=False, server_default="0"),
    Column("tick_last_seen", Integer, nullable=False, server_default="0"),
    Column("run_id", Text, nullable=False, server_default=""),
    Column("owner", Text, nullable=True),
    # At most one live incident per `sensor:category`, enforced by the database rather
    # than by this module remembering to check. Partial rather than plain unique so the
    # history survives: a tank that dries out in March and again in July is two rows and
    # two work orders, not one row with its March story overwritten.
    Index("uq_incidents_open_key", "incident_key", unique=True, postgresql_where=text("status <> 'resolved'")),
    Index("ix_incidents_status_last_seen", "status", "last_seen_at"),
    Index("ix_incidents_sensor", "sensor_id"),
)


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StoreTarget:
    """Which database a command talks to, resolved in one place.

    `alembic` and `main.py` read the same variable, because a migration applied to one
    database and a tick written to another is a failure that looks like an empty ledger.
    """

    name: str
    url: str
    schema: str


def resolve_store(target: str | None = None) -> StoreTarget:
    """`SW_OPS_TARGET=test` selects the local database. Anything else has to be explicit.

    Prod is the default rather than test on purpose: a default that quietly writes
    somewhere harmless is a default that ships, and a demo whose ledger is empty because
    every tick went to a test schema is a bad afternoon.
    """
    settings = get_settings()
    chosen = (target or os.environ.get("SW_OPS_TARGET") or "prod").lower()
    if chosen in {"test", "local"}:
        return StoreTarget(name="test", url=assert_local_test_url(settings.database_url_test), schema=assert_agent_schema(SCHEMA_TEST))
    if chosen != "prod":
        raise SchemaGuardError(f"unknown store target {chosen!r}. Use `prod` or `test`.")
    return StoreTarget(name="prod", url=settings.database_url, schema=assert_agent_schema(SCHEMA))


def connect_args_for(schema: str) -> dict[str, Any]:
    """The asyncpg connect arguments that make the schema rule structural.

    `search_path` is pinned to one schema, so an unqualified statement cannot land in
    `farm` even if someone writes one. Not remembering to qualify a table name; being
    unable to reach anything else.

    `statement_cache_size=0` because Supabase's session-mode pooler is pgBouncer, and
    asyncpg's prepared-statement cache breaks behind it in a way that surfaces as an
    intermittent `prepared statement "__asyncpg_stmt_x__" does not exist` under
    concurrency. Set here rather than in the URL so both databases get it and nobody has
    to know to add it to `.env`.
    """
    assert_agent_schema(schema)
    return {"statement_cache_size": 0, "server_settings": {"search_path": schema, "application_name": "sweetwater-agent"}}


def build_engine(url: str, *, schema: str = SCHEMA, echo: bool = False) -> AsyncEngine:
    """An engine that can only see one schema."""
    if not url:
        raise SchemaGuardError("no database URL. DATABASE_URL (or DATABASE_URL_TEST for the suite) is empty.")
    return create_async_engine(url, echo=echo, pool_pre_ping=True, connect_args=connect_args_for(schema))


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def store_session(*, url: str | None = None, schema: str = SCHEMA) -> AsyncIterator[AsyncSession]:
    """One session for one tick. The engine is disposed with it.

    An engine per tick is deliberate at this size: a tick runs every five minutes and
    writes about twenty rows, so a pool held open between ticks is a connection Supabase
    counts against us for nothing.
    """
    engine = build_engine(url or get_settings().database_url, schema=schema)
    try:
        async with session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# reconciliation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReconcileResult:
    """The three buckets, which are the whole point of the store.

    `opened` is what anyone gets paged about. `ongoing` is what is already known.
    `resolved` is what healed, and it is the bucket that makes the feed feel alive
    instead of a list that only grows.
    """

    opened: tuple[Incident, ...] = ()
    ongoing: tuple[Incident, ...] = ()
    resolved: tuple[Incident, ...] = ()
    skipped_unread: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {"opened": len(self.opened), "ongoing": len(self.ongoing), "resolved": len(self.resolved)}


def _display_value(finding: Finding) -> str | None:
    if finding.value is None:
        return None
    if isinstance(finding.value, bool):
        return "open" if finding.value else "closed"
    return f"{finding.value:g}"


def _to_incident(row: dict[str, Any]) -> Incident:
    return Incident(
        key=row["incident_key"],
        sensor_id=row["sensor_id"],
        sensor_type=row["sensor_type"],
        location=row["location"],
        category=row["category"],
        severity=str(row["severity"]),
        status=str(row["status"]),
        summary=row["summary"],
        last_value=row["last_value"],
        unit=row["unit"],
        threshold=row["threshold"],
        occurrences=row["occurrences"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        resolved_at=row["resolved_at"],
        tick_opened=row["tick_opened"],
        tick_last_seen=row["tick_last_seen"],
        run_id=row["run_id"],
        owner=row["owner"],
        row_id=row["id"],
    )


async def open_incidents(session: AsyncSession) -> tuple[Incident, ...]:
    rows = (await session.execute(select(incidents).where(incidents.c.status != "resolved").order_by(incidents.c.first_seen_at))).mappings().all()
    return tuple(_to_incident(dict(r)) for r in rows)


async def reconcile(
    session: AsyncSession,
    findings: Sequence[Finding],
    *,
    tick: int,
    run_id: str = "",
    read_sensor_ids: Iterable[str] | None = None,
    now: datetime | None = None,
    owners: dict[str, str] | None = None,
) -> ReconcileResult:
    """Fold this tick's findings into the ledger and return the three buckets.

    `read_sensor_ids` is the set of sensors this tick actually got a reading for. An
    incident is only resolved if its sensor answered and had nothing wrong to say.
    Leaving it `None` means "everything was read", which is convenient in a test and
    wrong in production, so `main.py` always passes it.

    `now` is injectable so tests assert on fixed timestamps rather than on the clock.
    """
    stamp = now or datetime.now(UTC)
    readable = set(read_sensor_ids) if read_sensor_ids is not None else None
    by_key = {f.key: f for f in findings}
    existing = {inc.key: inc for inc in await open_incidents(session)}

    opened: list[Incident] = []
    ongoing: list[Incident] = []
    resolved: list[Incident] = []

    for key, finding in by_key.items():
        owner = (owners or {}).get(key)
        prior = existing.get(key)
        if prior is None:
            values = {
                "incident_key": key,
                "sensor_id": finding.sensor_id,
                "sensor_type": finding.sensor_type,
                "location": finding.location,
                "category": finding.category,
                "severity": finding.severity,
                "status": "opened",
                "summary": finding.summary,
                "last_value": _display_value(finding),
                "unit": finding.unit,
                "threshold": finding.threshold,
                "occurrences": 1,
                "first_seen_at": stamp,
                "last_seen_at": stamp,
                "resolved_at": None,
                "tick_opened": tick,
                "tick_last_seen": tick,
                "run_id": run_id,
                "owner": owner,
            }
            row_id = (await session.execute(incidents.insert().returning(incidents.c.id), values)).scalar_one()
            opened.append(_to_incident({**values, "id": row_id}))
            continue

        # Seen again. `ongoing`, never `opened` a second time, and severity is refreshed
        # because the reading moved and code owns severity at every tier.
        updated = {
            "severity": finding.severity,
            "status": "ongoing",
            "summary": finding.summary,
            "last_value": _display_value(finding),
            "threshold": finding.threshold,
            "occurrences": prior.occurrences + 1,
            "last_seen_at": stamp,
            "tick_last_seen": tick,
            "owner": owner or prior.owner,
        }
        await session.execute(incidents.update().where(incidents.c.id == prior.row_id).values(**updated))
        ongoing.append(prior.model_copy(update={**updated, "severity": finding.severity}))

    for key, prior in existing.items():
        if key in by_key:
            continue
        if readable is not None and prior.sensor_id not in readable:
            # The sensor did not answer this tick. Absence of a finding is not evidence of
            # health, and an upstream outage must not close every incident on the ranch.
            continue
        await session.execute(incidents.update().where(incidents.c.id == prior.row_id).values(status="resolved", resolved_at=stamp, tick_last_seen=tick))
        resolved.append(prior.model_copy(update={"status": "resolved", "resolved_at": stamp, "tick_last_seen": tick}))

    skipped = tuple(sorted(k for k, inc in existing.items() if k not in by_key and readable is not None and inc.sensor_id not in readable))
    await session.commit()

    log.info("reconciled", tick=tick, opened=len(opened), ongoing=len(ongoing), resolved=len(resolved), held_unread=len(skipped))
    return ReconcileResult(opened=tuple(opened), ongoing=tuple(ongoing), resolved=tuple(resolved), skipped_unread=skipped)


def worst_severity(items: Iterable[Incident]) -> Severity | None:
    order: dict[Severity, int] = {"critical": 0, "warning": 1, "nominal": 2}
    ranked = sorted((i.severity for i in items), key=lambda s: order.get(s, 3))
    return ranked[0] if ranked else None


async def counts_by_status(session: AsyncSession) -> dict[IncidentStatus, int]:
    """Used by the live verification and, at M8, by the read API's summary endpoint."""
    rows = (await session.execute(select(incidents.c.status, text("count(*)")).group_by(incidents.c.status))).all()
    return {str(status): int(n) for status, n in rows}  # type: ignore[misc]
