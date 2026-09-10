"""Store fixtures: a LOCAL Postgres, schema `sw_ops_test`, migrated by alembic itself.

Two decisions worth knowing:

  * **Never Supabase.** `assert_local_test_url` fails the run rather than skipping it if
    `DATABASE_URL_TEST` points at the hosted database. A suite that drops and recreates a
    schema against the database the demo reads from is how three answer keys were lost
    here before, and a skip would let that config sit undetected.
  * **The suite runs the real migration, not `metadata.create_all`.** `create_all` would
    test a table definition that nothing in production ever executes, and the two drift
    silently. Running `alembic upgrade head` means migration 0001 is verified locally
    before it is ever pointed at Supabase.
"""

from __future__ import annotations

import asyncio
from argparse import Namespace
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.memory import SCHEMA_TEST, assert_droppable_schema, assert_local_test_url, build_engine, session_factory
from src.utils.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that reaches a real model fails here instead of billing for it.

    The same rule as `assert_local_test_url`, applied to the other expensive mistake. From
    M2 the tick spends money at its last two stages, so `run_tick` takes `spend=False` and
    every rail passes it. This fixture is what happens when somebody forgets: a forgotten
    flag becomes a loud failure rather than a slow suite and a bill, and `pytest` keeps
    passing on a plane. Rails that exercise the response path build a `ModelResponse`
    directly, which is the honest way to test a parser anyway.
    """
    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a test tried to construct a real model client. Pass spend=False, or build a ModelResponse directly (tests/CLAUDE.md).")

    monkeypatch.setattr("src.models.llm_client.build_client", _refuse)


async def _probe(url: str) -> None:
    engine = build_engine(url, schema=SCHEMA_TEST)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("select 1"))
    finally:
        await engine.dispose()


async def _drop_test_schema(url: str) -> None:
    # `assert_droppable_schema` in the statement itself rather than above it: the only
    # schema name that can reach a DROP is the one the guard returns.
    engine = build_engine(url, schema=SCHEMA_TEST)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{assert_droppable_schema(SCHEMA_TEST)}" CASCADE'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_store() -> str:
    """The URL of a freshly migrated `sw_ops_test`. Skips if no local Postgres is up.

    Skipped rather than failed so `pytest` passes on a plane, which is the rule in
    `tests/CLAUDE.md`. The skip message has to name what is not being proven, because a
    green run that quietly proved nothing about the store is worse than a red one.
    """
    url = assert_local_test_url(get_settings().database_url_test)
    try:
        asyncio.run(_probe(url))
    except Exception as exc:
        pytest.skip(f"local Postgres not reachable, so the reconciliation and migration rails are NOT being proven: {type(exc).__name__}: {exc}")

    asyncio.run(_drop_test_schema(url))
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    # `-x target=test` through the API, so `SW_OPS_TARGET` is never mutated for the rest
    # of the process and no later test can inherit a prod-pointing environment.
    cfg.cmd_opts = Namespace(x=["target=test"])
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
async def store(migrated_store: str) -> AsyncIterator[AsyncSession]:
    """An empty `incidents` table and a session on it.

    A fresh engine per test: pytest-asyncio gives each test its own event loop, and an
    AsyncEngine that has already connected is bound to the loop it connected on.
    """
    engine = build_engine(migrated_store, schema=SCHEMA_TEST)
    async with engine.begin() as conn:
        await conn.execute(text("truncate table incidents restart identity"))
    try:
        async with session_factory(engine)() as session:
            yield session
    finally:
        await engine.dispose()
