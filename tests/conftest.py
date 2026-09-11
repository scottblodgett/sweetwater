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
from src.utils.config import Settings, get_settings

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


@pytest.fixture(autouse=True)
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """Chaos is disarmed for every test unless the test arms it itself.

    Found at M4: `.env` on this machine carries `CHAOS_ENABLED=1`, and with the real settings
    in force `sweep()` reads the active overlay through `resolve_store()`, whose default is
    Supabase. That is a test touching the hosted database, read-only and by accident, which
    is exactly the class of thing the first rule of this file forbids. And from M4 the tick
    itself injects when armed, which would have written the plan into `sw_ops_test` on every
    tick test. A test that wants chaos patches `src.tools.chaos.get_settings` itself, as the
    chaos suite in `test_tools.py` already does, and that later patch wins.

    Returned so a test can turn one knob on the copy (`settings.incident_confirm_sweeps = 1`)
    without building a whole `Settings`; the copy is per test, so nothing leaks.
    """
    disarmed = get_settings().model_copy(update={"chaos_enabled": False, "log_dir": str(tmp_path / "logs")})
    monkeypatch.setattr("src.tools.chaos.get_settings", lambda: disarmed)
    monkeypatch.setattr("src.agent.executor.get_settings", lambda: disarmed)
    monkeypatch.setattr("src.agent.memory.get_settings", lambda: disarmed)
    # And the log directory. Found at M6: a CLI rail calls `configure_logging()`, which installs
    # the file handlers for the whole process, and every audit line a later test wrote outside
    # `capture_logs` then landed in the real `logs/audit.jsonl` as a `proposed` nobody would ever
    # answer. Three of them, with the fixture's `run_id`. A receipt file must never carry a test.
    monkeypatch.setattr("src.utils.logger.get_settings", lambda: disarmed)
    return disarmed


@pytest.fixture
def first_sight(settings: Settings) -> Settings:
    """Open on the first bad read, the behaviour before migration 0003. For rails whose subject
    is something other than the debounce, so they do not each need a warm-up tick."""
    settings.incident_confirm_sweeps = 1
    return settings


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
def migrated_store(tmp_path_factory: pytest.TempPathFactory) -> str:
    """The URL of a freshly migrated `sw_ops_test`. Skips if no local Postgres is up.

    Skipped rather than failed so `pytest` passes on a plane, which is the rule in
    `tests/CLAUDE.md`. The skip message has to name what is not being proven, because a
    green run that quietly proved nothing about the store is worse than a red one.

    The log directory is patched here too, not only in `settings`: `alembic/env.py` calls
    `configure_logging()`, this fixture is session-scoped so it runs before any function-scoped
    patch, and `configure_logging` is idempotent, so whichever directory it saw first is where
    every file handler points for the rest of the process. Found at M6 as fixture lines in the
    real `logs/audit.jsonl`.
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
    quiet = get_settings().model_copy(update={"log_dir": str(tmp_path_factory.mktemp("logs"))})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.utils.logger.get_settings", lambda: quiet)
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
