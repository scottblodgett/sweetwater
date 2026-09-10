"""Alembic against `sw_ops`, or `sw_ops_test`, and never anything else.

The connection string comes from `src/utils/config.py`, so there is one place a database
URL lives in this repo. The schema comes from a two-value allowlist and is checked before
a connection is opened, because the credentials that reach `sw_ops` also reach the ranch's
own schemas in the same database.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from src.agent.memory import SchemaGuardError, StoreTarget, build_engine, metadata, resolve_store
from src.utils.logger import configure_logging, get_logger


def resolve_target() -> StoreTarget:
    """The same resolver `main.py` uses, with `-x target=test` as an extra way to ask.

    One resolver, deliberately: a migration applied to one database and a tick written to
    another is a failure that presents as an empty ledger rather than as an error.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    try:
        return resolve_store(x_args.get("target"))
    except SchemaGuardError as exc:
        raise SystemExit(str(exc)) from exc


def do_run_migrations(connection: Connection, schema: str) -> None:
    # The schema has to exist before alembic writes its version table into it. Quoted
    # interpolation rather than a bind parameter because DDL cannot be parameterized;
    # the value came through `assert_agent_schema`, so it is one of two literals.
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    connection.execute(text(f'SET search_path TO "{schema}"'))
    # `version_table_schema` matters more than it looks: alembic's default puts
    # `alembic_version` in the search path's first schema, and on the local test database
    # that would drop a table belonging to this repo into a database shared with the
    # upstream project's own schemas.
    context.configure(connection=connection, target_metadata=metadata, version_table_schema=schema, include_schemas=False, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    store = resolve_target()
    # This repo's own logging, so `alembic upgrade head` says what it did instead of
    # running silently. The URL is never logged: it carries a password.
    configure_logging()
    get_logger("sweetwater.alembic").info("migrating", target=store.name, schema=store.schema)
    engine = build_engine(store.url, schema=store.schema)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations, store.schema)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported: the schema must be created before the version table, which takes a live connection.")

asyncio.run(run_migrations_online())
