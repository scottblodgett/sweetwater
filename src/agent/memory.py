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
  * **A subject that failed to read resolves nothing.** "No finding" and "no reading" are
    different facts, and conflating them means a single upstream outage closes every
    incident on the ranch and reports an all-clear at the worst possible moment. From M7A a
    subject is a sensor or an animal (migration `0006`), and the rule is the same for both: a
    Care API outage resolves no cow.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from psycopg import Connection, errors
from psycopg.rows import dict_row
from sqlalchemy import BigInteger, Column, DateTime, Float, Index, Integer, MetaData, Table, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.agent.state import LIVE_STATUSES, SUBJECT_ANIMAL, Finding, Incident, IncidentStatus, Severity
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
    # `subject:category`, not a foreign key to anything. Cross-service IDs are plain
    # strings here; the sensor or animal lives in another service and orphans are allowed.
    Column("incident_key", Text, nullable=False),
    # Migration 0006 renamed these from `sensor_id` / `sensor_type`. `subject_type` is one of the
    # 13 sensor types for a sensor row or the literal `animal` for an animal row (`state.SUBJECT_ANIMAL`).
    Column("subject_id", Text, nullable=False),
    Column("subject_type", Text, nullable=False),
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
    # Migration 0005. Non-null means the loop is carrying this incident unanswered and will
    # re-route it; the value says why (`agent_raised`, `transport_error`, ...). Written and
    # cleared by `run_tick`, read by `run_loop` before its first tick, so a restart does not
    # turn a held incident back into a plain `ongoing` one nobody re-asks about.
    Column("held_reason", Text, nullable=True),
    # At most one live incident per `sensor:category`, enforced by the database rather
    # than by this module remembering to check. Partial rather than plain unique so the
    # history survives: a tank that dries out in March and again in July is two rows and
    # two work orders, not one row with its March story overwritten.
    Index("uq_incidents_open_key", "incident_key", unique=True, postgresql_where=text("status IN ('pending', 'opened', 'ongoing')")),
    Index("ix_incidents_status_last_seen", "status", "last_seen_at"),
    Index("ix_incidents_subject", "subject_id"),
)

#: The chaos overlay. Mirrors migration 0002, and it is the only place in this system
#: where a lie about the ranch is stored: the deployed Sensor API is stateless, so a fault
#: cannot be written into it, and its own injector refuses to arm on Lambda on purpose.
#: `src/tools/chaos.py` owns the meaning of every column here; this module owns the rows.
chaos_events = Table(
    "chaos_events",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("event_id", Text, nullable=False),
    Column("group_id", Text, nullable=False, server_default=""),
    Column("scenario", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("target_id", Text, nullable=False),
    Column("target_type", Text, nullable=False, server_default=""),
    Column("location", Text, nullable=False, server_default=""),
    Column("fault", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("seed", Integer, nullable=False, server_default="0"),
    Column("seq", Integer, nullable=False, server_default="0"),
    Column("status", Text, nullable=False),
    Column("injected_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("expired_at", DateTime(timezone=True), nullable=True),
    Column("tick_injected", Integer, nullable=False, server_default="0"),
    Column("run_id", Text, nullable=False, server_default=""),
    Index("uq_chaos_events_event_id", "event_id", unique=True),
    Index("uq_chaos_active_target_fault", "target_id", "fault", unique=True, postgresql_where=text("status = 'active'")),
    Index("ix_chaos_events_status_expires", "status", "expires_at"),
    Index("ix_chaos_events_group", "group_id"),
)

#: M8. The tick line as a row, so a process that is not the loop can see ticks land. Mirrors
#: migration 0007. `fields` is the whole line the logger wrote; the typed columns are what the API
#: sorts and filters on. `id` is the SSE cursor.
ticks = Table(
    "ticks",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", Text, nullable=False),
    Column("tick", Integer, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("store", Text, nullable=False, server_default=""),
    Column("duration_ms", Integer, nullable=False, server_default="0"),
    Column("cost_usd", Float, nullable=False, server_default="0"),
    Column("error", Text, nullable=True),
    Column("failed_stage", Text, nullable=True),
    Column("fields", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Index("uq_ticks_run_tick", "run_id", "tick", unique=True),
    Index("ix_ticks_at", "at"),
)

#: M8. The supervisor's page as a row. `incident_keys` is what the page was handed, kept beside
#: `linked` so the fusion claim stays checkable after the fact.
shift_reports = Table(
    "shift_reports",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("run_id", Text, nullable=False),
    Column("tick", Integer, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("source", Text, nullable=False),
    Column("headline", Text, nullable=False, server_default=""),
    Column("situation", Text, nullable=False, server_default=""),
    Column("priorities", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("linked", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("escalations", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("worlds", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("incident_keys", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("work_orders", Integer, nullable=False, server_default="0"),
    Column("violations", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("provider", Text, nullable=False, server_default=""),
    Column("model", Text, nullable=False, server_default=""),
    Column("finish_reason", Text, nullable=False, server_default=""),
    Column("latency_ms", Integer, nullable=False, server_default="0"),
    Column("input_tokens", Integer, nullable=False, server_default="0"),
    Column("output_tokens", Integer, nullable=False, server_default="0"),
    Index("uq_shift_reports_run_tick", "run_id", "tick", unique=True),
    Index("ix_shift_reports_at", "at"),
)

#: M8, `docs/issues.md` #10. The gate's receipt as a row, keyed on `(audit_id, phase)`, so "every
#: audit_id appears exactly twice" is a constraint and `logs/audit.jsonl` is a projection. Written
#: by `src/agent/gate.py` through the checkpointer's own connection (`ThreadedPostgresSaver.record_receipt`),
#: because the process holding a pause is the process that has that connection open.
audit_receipts = Table(
    "audit_receipts",
    metadata,
    Column("audit_id", Text, primary_key=True),
    Column("phase", Text, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("run_id", Text, nullable=False, server_default=""),
    Column("tick", Integer, nullable=False, server_default="0"),
    Column("tool", Text, nullable=False),
    Column("incident_key", Text, nullable=False, server_default=""),
    Column("proposed_by", Text, nullable=True),
    Column("args", JSONB, nullable=True),
    Column("decision", Text, nullable=True),
    Column("decided_by", Text, nullable=True),
    Column("result", Text, nullable=True),
    Column("reason", Text, nullable=True),
    Column("upstream", Text, nullable=True),
    Column("latency_to_decision_ms", Integer, nullable=True),
    Index("ix_audit_receipts_at", "at"),
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
    #: The debounce buckets. `pending` was flagged but not yet on enough consecutive sweeps to
    #: be an incident; `dismissed` was pending and read clean. Neither is paged or billed.
    pending: tuple[Incident, ...] = ()
    dismissed: tuple[Incident, ...] = ()
    skipped_unread: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {"opened": len(self.opened), "ongoing": len(self.ongoing), "resolved": len(self.resolved), "pending": len(self.pending), "dismissed": len(self.dismissed)}


def _display_value(finding: Finding) -> str | None:
    if finding.value is None:
        return None
    if isinstance(finding.value, bool):
        return "open" if finding.value else "closed"
    return f"{finding.value:g}"


def _to_incident(row: dict[str, Any]) -> Incident:
    return Incident(
        key=row["incident_key"],
        subject_id=row["subject_id"],
        subject_type=row["subject_type"],
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
    """Every live row: `pending`, `opened`, `ongoing`. Named by the live set rather than as
    "not resolved", because `dismissed` is also not resolved and is not live."""
    rows = (await session.execute(select(incidents).where(incidents.c.status.in_(LIVE_STATUSES)).order_by(incidents.c.first_seen_at))).mappings().all()
    return tuple(_to_incident(dict(r)) for r in rows)


async def live_animal_subjects(session: AsyncSession) -> frozenset[str]:
    """The animal ids with a live incident. M7A: the herd sweep reads each of these directly, because
    an animal can fall off every roster and every status read (a restored kill) and still be owed a
    resolution. Read before the sweep, on the same connection budget as everything else here."""
    rows = (await session.execute(select(incidents.c.subject_id).where(incidents.c.subject_type == SUBJECT_ANIMAL, incidents.c.status.in_(LIVE_STATUSES)))).scalars().all()
    return frozenset(str(k) for k in rows)


async def reconcile(
    session: AsyncSession,
    findings: Sequence[Finding],
    *,
    tick: int,
    run_id: str = "",
    read_subject_ids: Iterable[str] | None = None,
    now: datetime | None = None,
    owners: dict[str, str] | None = None,
    confirm_sweeps: int | None = None,
) -> ReconcileResult:
    """Fold this tick's findings into the ledger and return the buckets.

    `read_subject_ids` is the set of subjects (sensors, and from M7A animals) this tick actually
    got a reading for. An incident is only resolved (or dismissed) if its subject answered and
    had nothing wrong to say. Leaving it `None` means "everything was read", which is convenient
    in a test and wrong in production, so `executor.py` always passes it.

    `confirm_sweeps` is the debounce: a finding has to be present on this many consecutive
    sweeps before it opens. Until then it is `pending`: live in the ledger so the unique
    index and the unread guard both cover it, but not in `opened`, so it is never routed,
    never paged, and never billed. A pending row whose sensor reads clean is `dismissed`,
    not `resolved`, because nothing was ever alarmed. Defaults to `INCIDENT_CONFIRM_SWEEPS`;
    1 is open-on-first-sight, the behaviour before 0003. The reason is in `config.py`: the
    deployed Sensor API invents a fresh reading per call, and one bad draw is not a dry tank.

    `now` is injectable so tests assert on fixed timestamps rather than on the clock.
    """
    stamp = now or datetime.now(UTC)
    confirm = max(1, get_settings().incident_confirm_sweeps if confirm_sweeps is None else confirm_sweeps)
    readable = set(read_subject_ids) if read_subject_ids is not None else None
    by_key = {f.key: f for f in findings}
    existing = {inc.key: inc for inc in await open_incidents(session)}

    opened: list[Incident] = []
    ongoing: list[Incident] = []
    resolved: list[Incident] = []
    pending: list[Incident] = []
    dismissed: list[Incident] = []

    for key, finding in by_key.items():
        owner = (owners or {}).get(key)
        prior = existing.get(key)
        if prior is None:
            first_status: IncidentStatus = "opened" if confirm <= 1 else "pending"
            values = {
                "incident_key": key,
                "subject_id": finding.subject_id,
                "subject_type": finding.subject_type,
                "location": finding.location,
                "category": finding.category,
                "severity": finding.severity,
                "status": first_status,
                "summary": finding.summary,
                "last_value": _display_value(finding),
                "unit": finding.unit,
                "threshold": finding.threshold,
                "occurrences": 1,
                "first_seen_at": stamp,
                "last_seen_at": stamp,
                "resolved_at": None,
                "tick_opened": tick if first_status == "opened" else 0,
                "tick_last_seen": tick,
                "run_id": run_id,
                "owner": owner,
            }
            row_id = (await session.execute(incidents.insert().returning(incidents.c.id), values)).scalar_one()
            (opened if first_status == "opened" else pending).append(_to_incident({**values, "id": row_id}))
            continue

        # Seen again. Severity is refreshed because the reading moved and code owns severity
        # at every tier. A pending row that has now been seen enough times in a row opens, and
        # `tick_opened` is the tick it opened on, not the tick it was first glimpsed;
        # `first_seen_at` keeps the glimpse. An opened row becomes `ongoing`, never `opened`
        # a second time.
        seen = prior.occurrences + 1
        if prior.status == "pending":
            next_status: IncidentStatus = "opened" if seen >= confirm else "pending"
        else:
            next_status = "ongoing"
        updated = {
            "severity": finding.severity,
            "status": next_status,
            "summary": finding.summary,
            "last_value": _display_value(finding),
            "threshold": finding.threshold,
            "occurrences": seen,
            "last_seen_at": stamp,
            "tick_last_seen": tick,
            "owner": owner or prior.owner,
        }
        if next_status == "opened":
            updated["tick_opened"] = tick
        await session.execute(incidents.update().where(incidents.c.id == prior.row_id).values(**updated))
        after = prior.model_copy(update={**updated, "severity": finding.severity})
        {"opened": opened, "pending": pending, "ongoing": ongoing}[next_status].append(after)

    for key, prior in existing.items():
        if key in by_key:
            continue
        if readable is not None and prior.subject_id not in readable:
            # The subject did not answer this tick. Absence of a finding is not evidence of
            # health, and an upstream outage must not close every incident on the ranch. This
            # holds for a pending row too: unread is not "read clean", and for an animal too: a
            # Care API outage resolves no cow.
            continue
        if prior.status == "pending":
            # One bad draw, then a clean read. Never alarmed, so never resolved.
            await session.execute(incidents.update().where(incidents.c.id == prior.row_id).values(status="dismissed", resolved_at=stamp, tick_last_seen=tick))
            dismissed.append(prior.model_copy(update={"status": "dismissed", "resolved_at": stamp, "tick_last_seen": tick}))
            continue
        await session.execute(incidents.update().where(incidents.c.id == prior.row_id).values(status="resolved", resolved_at=stamp, tick_last_seen=tick))
        resolved.append(prior.model_copy(update={"status": "resolved", "resolved_at": stamp, "tick_last_seen": tick}))

    skipped = tuple(sorted(k for k, inc in existing.items() if k not in by_key and readable is not None and inc.subject_id not in readable))
    await session.commit()

    log.info("reconciled", tick=tick, opened=len(opened), ongoing=len(ongoing), resolved=len(resolved), pending=len(pending), dismissed=len(dismissed), held_unread=len(skipped), confirm_sweeps=confirm)
    return ReconcileResult(opened=tuple(opened), ongoing=tuple(ongoing), resolved=tuple(resolved), pending=tuple(pending), dismissed=tuple(dismissed), skipped_unread=skipped)


def worst_severity(items: Iterable[Incident]) -> Severity | None:
    order: dict[Severity, int] = {"critical": 0, "warning": 1, "nominal": 2}
    ranked = sorted((i.severity for i in items), key=lambda s: order.get(s, 3))
    return ranked[0] if ranked else None


async def counts_by_status(session: AsyncSession) -> dict[IncidentStatus, int]:
    """Used by the live verification and, at M8, by the read API's summary endpoint."""
    rows = (await session.execute(select(incidents.c.status, text("count(*)")).group_by(incidents.c.status))).all()
    return {str(status): int(n) for status, n in rows}  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# the held set, durable from M6 (migration 0005)
# --------------------------------------------------------------------------- #
async def held_incident_keys(session: AsyncSession) -> frozenset[str]:
    """Every live incident the loop was carrying unanswered when it last wrote. Read once, by
    `run_loop` before its first tick, so a restart re-routes what the previous run could not
    get answered instead of leaving it `ongoing` forever."""
    rows = (await session.execute(select(incidents.c.incident_key).where(incidents.c.held_reason.is_not(None), incidents.c.status.in_(LIVE_STATUSES)))).scalars().all()
    return frozenset(str(k) for k in rows)


async def record_held(session: AsyncSession, *, reasons: dict[str, str], released: Iterable[str]) -> None:
    """Write this tick's held set: a reason on every key still held, null on every key that
    was held and is not any more. Only live rows are touched; a resolved incident keeps a
    stale reason as history, which is harmless because `held_incident_keys` reads live rows only."""
    freed = sorted(set(released) - set(reasons))
    if freed:
        await session.execute(incidents.update().where(incidents.c.incident_key.in_(freed), incidents.c.status.in_(LIVE_STATUSES)).values(held_reason=None))
    for key, reason in sorted(reasons.items()):
        await session.execute(incidents.update().where(incidents.c.incident_key == key, incidents.c.status.in_(LIVE_STATUSES)).values(held_reason=reason))
    await session.commit()


# --------------------------------------------------------------------------- #
# the checkpointer, M6: where a paused write waits for a human
# --------------------------------------------------------------------------- #
#: The receipt row, in insert order. One tuple so the writer and the API's reader cannot disagree.
RECEIPT_COLUMNS = ("audit_id", "phase", "at", "run_id", "tick", "tool", "incident_key", "proposed_by", "args", "decision", "decided_by", "result", "reason", "upstream", "latency_to_decision_ms")


class CheckpointerNotMigratedError(RuntimeError):
    """The database is behind the installed LangGraph checkpointer, or has no tables for it."""


class ThreadedPostgresSaver(PostgresSaver):
    """The library's **sync** Postgres saver, driven from an async graph through one worker thread.

    Why not `AsyncPostgresSaver`: psycopg's async connection refuses Windows' default
    `ProactorEventLoop`, which is the loop everything else in this process (asyncpg, httpx, the
    MCP client) already runs on. Switching the process to the selector loop to please one
    driver is a policy change every other component and every test would inherit. The sync
    saver has no loop affinity at all, so the async half is written here: each async method
    runs its sync twin in a thread, and one lock serializes them because a psycopg connection
    is not safe for concurrent use. `langgraph-checkpoint`'s `PostgresSaver` raises
    `NotImplementedError` from its async methods rather than doing this itself, which is how
    the first spike found out.
    """

    def __init__(self, conn: Connection[Any]) -> None:
        super().__init__(conn)
        self._lock = asyncio.Lock()
        #: The same connection, typed as the single connection it is (the library's field admits a pool).
        self._receipt_conn: Connection[Any] = conn

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        async with self._lock:
            return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        async with self._lock:
            items = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in items:
            yield item

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        async with self._lock:
            return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        async with self._lock:
            await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def record_receipt(self, row: dict[str, Any]) -> None:
        """M8. One gate receipt, `proposed` or `decided`, on the connection that holds the pause.

        A plain INSERT with no conflict clause on purpose: the primary key is the rail, and a second
        `decided` for one `audit_id` is exactly the thing that must fail loudly rather than merge.
        `args` is passed as JSON text because psycopg does not adapt a dict on its own.
        """
        cols = RECEIPT_COLUMNS
        values = [json.dumps(row.get("args")) if c == "args" and row.get("args") is not None else row.get(c) for c in cols]
        placeholders = ", ".join("%s::jsonb" if c == "args" else "%s" for c in cols)
        async with self._lock:
            await asyncio.to_thread(self._receipt_conn.execute, f"INSERT INTO audit_receipts ({', '.join(cols)}) VALUES ({placeholders})", values)


def psycopg_url(url: str) -> str:
    """`config.py` upgrades both URLs to `postgresql+asyncpg://` for SQLAlchemy; psycopg wants
    the bare scheme back. One place, so nobody strips it by hand twice."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def assert_checkpointer_migrated(conn: Connection[Any]) -> None:
    """Refuse a database whose checkpointer tables are missing or behind the installed library.

    The alternative is `saver.setup()`, which would create or upgrade the tables silently on
    whichever database the process is pointed at. Decision 1 in `docs/STATE.md` says a Supabase
    migration is an alembic revision Scott has seen. So migration 0004 runs the library's own
    DDL, and this check is what turns a newer library into an error naming the next revision
    rather than an unreviewed schema change on prod.
    """
    expected = len(BasePostgresSaver.MIGRATIONS) - 1
    try:
        row = conn.execute("SELECT max(v) AS v FROM checkpoint_migrations").fetchone()
    except errors.UndefinedTable as exc:
        raise CheckpointerNotMigratedError("no checkpoint_migrations table: run `alembic upgrade head` (migration 0004 creates the checkpointer's tables) before starting a loop or the gate CLI") from exc
    applied = int((row or {}).get("v") or -1)
    if applied < expected:
        raise CheckpointerNotMigratedError(f"checkpoint_migrations is at v{applied} and the installed langgraph-checkpoint-postgres expects v{expected}. The library was upgraded; write the next alembic revision from its MIGRATIONS list rather than calling setup() against this database")


@asynccontextmanager
async def checkpointer(target: StoreTarget) -> AsyncIterator[ThreadedPostgresSaver]:
    """One checkpointer for one gate action, on the same schema the ledger uses.

    `search_path` is set on the session the same way the asyncpg engine pins it, so the
    checkpointer's unqualified table names can only land in the schema this repo owns.
    `prepare_threshold=0` for the same reason `statement_cache_size=0` is on the engine: the
    Supabase pooler and prepared statements do not mix. Opened per use rather than held,
    because a tick proposes a write on a minority of ticks and a connection held open between
    them is one Supabase counts against us for nothing.
    """
    schema = assert_agent_schema(target.schema)
    conn = await asyncio.to_thread(lambda: Connection.connect(psycopg_url(target.url), autocommit=True, prepare_threshold=0, row_factory=dict_row))
    try:
        await asyncio.to_thread(conn.execute, f'SET search_path TO "{schema}"')
        await asyncio.to_thread(assert_checkpointer_migrated, conn)
        yield ThreadedPostgresSaver(conn)
    finally:
        await asyncio.to_thread(conn.close)


# --------------------------------------------------------------------------- #
# the chaos overlay
# --------------------------------------------------------------------------- #
# These four take and return plain mappings rather than a typed model, on purpose:
# `src/tools/chaos.py` imports this module, so this module cannot import its `ChaosEvent`
# without a cycle. The table definition above is the row shape, and chaos.py owns both
# directions of the conversion. Every column is written explicitly by the caller, because
# a server default that only some inserts rely on is a default nobody knows is there.
async def insert_chaos_events(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Insert, skipping any row the database already has. Returns only what actually landed.

    `ON CONFLICT DO NOTHING` with **no conflict target**, so it covers both unique indexes:
    a replayed `event_id` (the same seed injected twice, which must not double the demo)
    and a second active fault of the same mode on the same target. Naming only `event_id`
    here would turn the second case into an `IntegrityError` that aborts the transaction
    and takes the rest of the batch with it.
    """
    if not rows:
        return ()
    stmt = pg_insert(chaos_events).values(list(rows)).on_conflict_do_nothing().returning(chaos_events)
    landed = (await session.execute(stmt)).mappings().all()
    await session.commit()
    if len(landed) != len(rows):
        log.info("chaos_insert_deduplicated", offered=len(rows), inserted=len(landed), hint="a replayed seed or an already-active fault on the same target; both are skips, not errors")
    return tuple(dict(r) for r in landed)


async def active_chaos_events(session: AsyncSession, *, now: datetime, kind: str | None = None) -> tuple[dict[str, Any], ...]:
    """Events that are still lying to us at `now`.

    The `expires_at` filter is here as well as in `expire_chaos_events`, deliberately
    belt-and-braces: a sensor has to heal on time even on a tick where nothing called the
    expiry sweep, because the healing is what produces `resolved` incidents and a fault
    that outlives its TTL by an hour is the demo going quiet.
    """
    q = select(chaos_events).where(chaos_events.c.status == "active", chaos_events.c.expires_at > now)
    if kind is not None:
        q = q.where(chaos_events.c.kind == kind)
    rows = (await session.execute(q.order_by(chaos_events.c.seq, chaos_events.c.id))).mappings().all()
    return tuple(dict(r) for r in rows)


async def expire_chaos_events(session: AsyncSession, *, now: datetime, force_all: bool = False) -> tuple[dict[str, Any], ...]:
    """Flip due events to `expired` and return them. `force_all` heals the ranch on demand.

    Expiry is not bookkeeping, it is the product: the next sweep reads the honest value,
    triage finds nothing, and `reconcile` moves the incident to `resolved`. Without it
    everything is broken an hour in and the feed only ever grows.
    """
    q = chaos_events.update().where(chaos_events.c.status == "active")
    if not force_all:
        q = q.where(chaos_events.c.expires_at <= now)
    rows = (await session.execute(q.values(status="expired", expired_at=now).returning(chaos_events))).mappings().all()
    await session.commit()
    if rows:
        log.info("chaos_expired", count=len(rows), forced=force_all, targets=sorted({str(r["target_id"]) for r in rows}))
    return tuple(dict(r) for r in rows)


async def chaos_counts_by_status(session: AsyncSession) -> dict[str, int]:
    rows = (await session.execute(select(chaos_events.c.status, text("count(*)")).group_by(chaos_events.c.status))).all()
    return {str(status): int(n) for status, n in rows}


# --------------------------------------------------------------------------- #
# M8: the tick, the shift report, and what the read API selects
# --------------------------------------------------------------------------- #
# The loop writes the first two beside the log line; the API reads all of it and writes nothing
# here (its one write goes through the gate). Every reader orders newest first and by `id` second,
# because two rows can share a timestamp and the window must never see them swap places.
async def insert_tick(session: AsyncSession, *, run_id: str, tick: int, at: datetime, fields: dict[str, Any]) -> int:
    """One tick line as a row. Returns the row id, which is the SSE cursor.

    `fields` is the exact mapping `log_tick` was called with, so the row and the line cannot
    disagree; the typed columns are lifted out of it rather than passed twice.
    """
    stmt = pg_insert(ticks).values(run_id=run_id, tick=tick, at=at, store=str(fields.get("store") or ""), duration_ms=int(fields.get("duration_ms") or 0), cost_usd=float(fields.get("cost_usd") or 0.0), error=fields.get("error"), failed_stage=fields.get("failed_stage"), fields=fields).returning(ticks.c.id)
    row_id = int((await session.execute(stmt)).scalar_one())
    await session.commit()
    return row_id


async def insert_shift_report(session: AsyncSession, *, run_id: str, tick: int, at: datetime, report: dict[str, Any], incident_keys: Sequence[str]) -> int:
    """The supervisor's page as a row. `report` is `ShiftReport.model_dump()`; a mapping here rather
    than the model, because `agent.py` imports this module."""
    stmt = pg_insert(shift_reports).values(
        run_id=run_id, tick=tick, at=at, source=str(report.get("source") or "code"), headline=str(report.get("headline") or ""), situation=str(report.get("situation") or ""),
        priorities=list(report.get("priorities") or ()), linked=list(report.get("linked") or ()), escalations=list(report.get("escalations") or ()), worlds=list(report.get("worlds") or ()),
        incident_keys=sorted(incident_keys), work_orders=int(report.get("work_orders") or 0), violations=list(report.get("violations") or ()),
        provider=str(report.get("provider") or ""), model=str(report.get("model") or ""), finish_reason=str(report.get("finish_reason") or ""), latency_ms=int(report.get("latency_ms") or 0),
        input_tokens=int(report.get("input_tokens") or 0), output_tokens=int(report.get("output_tokens") or 0),
    ).returning(shift_reports.c.id)
    row_id = int((await session.execute(stmt)).scalar_one())
    await session.commit()
    return row_id


async def latest_shift_report(session: AsyncSession) -> dict[str, Any] | None:
    row = (await session.execute(select(shift_reports).order_by(shift_reports.c.at.desc(), shift_reports.c.id.desc()).limit(1))).mappings().first()
    return dict(row) if row else None


async def latest_tick(session: AsyncSession) -> dict[str, Any] | None:
    row = (await session.execute(select(ticks).order_by(ticks.c.id.desc()).limit(1))).mappings().first()
    return dict(row) if row else None


async def ticks_after(session: AsyncSession, *, after_id: int, limit: int = 100) -> tuple[dict[str, Any], ...]:
    """Every tick row with an id past the cursor, oldest first, so the stream replays in order."""
    rows = (await session.execute(select(ticks).where(ticks.c.id > after_id).order_by(ticks.c.id).limit(limit))).mappings().all()
    return tuple(dict(r) for r in rows)


async def list_incidents(session: AsyncSession, *, status: str | None = None, owner: str | None = None, subject_type: str | None = None, limit: int = 100, offset: int = 0) -> tuple[Incident, ...]:
    """Newest first by `last_seen_at`. The three filters are the ones the window asked for at M7A,
    when animals and sensors started sharing this table. Validation of the values is the API's."""
    q = select(incidents)
    if status is not None:
        q = q.where(incidents.c.status == status)
    if owner is not None:
        q = q.where(incidents.c.owner == owner)
    if subject_type is not None:
        q = q.where(incidents.c.subject_type == subject_type)
    rows = (await session.execute(q.order_by(incidents.c.last_seen_at.desc(), incidents.c.id.desc()).limit(limit).offset(offset))).mappings().all()
    return tuple(_to_incident(dict(r)) for r in rows)
