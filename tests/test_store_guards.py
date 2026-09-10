"""The guards that keep this repo out of the ranch's schemas. No database required.

These run on a plane, and they have to: the accident they prevent is not a bug that
shows up as a failing test, it is a `DROP SCHEMA` that already happened.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.agent.memory import (
    ALLOWED_SCHEMAS,
    RANCH_SCHEMAS,
    SCHEMA,
    SCHEMA_TEST,
    SchemaGuardError,
    assert_agent_schema,
    assert_droppable_schema,
    assert_local_test_url,
    build_engine,
    connect_args_for,
    metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# the runtime guard
# --------------------------------------------------------------------------- #
def test_only_the_two_agent_schemas_are_allowed() -> None:
    assert assert_agent_schema(SCHEMA) == SCHEMA
    assert assert_agent_schema(SCHEMA_TEST) == SCHEMA_TEST
    assert ALLOWED_SCHEMAS == {SCHEMA, SCHEMA_TEST}


@pytest.mark.parametrize("schema", sorted(RANCH_SCHEMAS))
def test_a_ranch_schema_is_refused_by_name(schema: str) -> None:
    """The credentials that reach `sw_ops` also have write access to `farm`, `feed`,
    `animal_care`, and `sensor` in the same database. The only way to reach ranch data
    from this repo is over HTTP through the deployed APIs."""
    with pytest.raises(SchemaGuardError, match="ranch schema"):
        assert_agent_schema(schema)


def test_public_and_typos_are_refused_too() -> None:
    for name in ("public", "sw_op", "sw_ops_prod", "", "SW_OPS"):
        with pytest.raises(SchemaGuardError):
            assert_agent_schema(name)


def test_only_the_test_schema_is_ever_droppable() -> None:
    """`sw_ops` holds the incident history the demo reads from. A DROP a test can reach is
    a DROP that eventually runs against the wrong database."""
    assert assert_droppable_schema(SCHEMA_TEST) == SCHEMA_TEST
    with pytest.raises(SchemaGuardError, match="Only 'sw_ops_test'"):
        assert_droppable_schema(SCHEMA)


def test_the_engine_factory_refuses_a_foreign_schema() -> None:
    with pytest.raises(SchemaGuardError):
        build_engine("postgresql+asyncpg://u@localhost/db", schema="farm")


def test_the_connection_pins_search_path_to_one_schema() -> None:
    """Not remembering to qualify a table name; being unable to reach anything else. The
    live proof that this reaches Postgres is in `test_memory.py`; this is the wiring."""
    args = connect_args_for(SCHEMA)
    assert args["server_settings"]["search_path"] == SCHEMA
    assert args["statement_cache_size"] == 0, "pgBouncer breaks asyncpg's prepared statement cache"
    with pytest.raises(SchemaGuardError):
        connect_args_for("animal_care")


def test_a_supabase_url_is_a_hard_failure_for_tests() -> None:
    """Failed, not skipped. A skip would let a prod-pointing test config sit undetected
    until the run that drops the schema the demo reads from."""
    for url in (
        "postgresql+asyncpg://postgres:pw@aws-0-us-west-2.pooler.supabase.com:5432/postgres",
        "postgresql+asyncpg://postgres:pw@db.abcdefgh.supabase.co:5432/postgres",
    ):
        with pytest.raises(SchemaGuardError, match="Supabase"):
            assert_local_test_url(url)
    with pytest.raises(SchemaGuardError, match="empty"):
        assert_local_test_url("")
    assert assert_local_test_url("postgresql+asyncpg://postgres@localhost:5432/farm_systems_test").endswith("farm_systems_test")


# --------------------------------------------------------------------------- #
# the grep: no SQL in this repo names a ranch schema
# --------------------------------------------------------------------------- #
NAMES = "|".join(sorted(RANCH_SCHEMAS))
PATTERNS = (
    re.compile(rf'(?i)\b(?:from|join|into|update|table|truncate)\s+"?(?:{NAMES})"?\.'),
    re.compile(rf'(?i)\b(?:create|drop|alter)\s+schema\s+(?:if\s+(?:not\s+)?exists\s+)?"?(?:{NAMES})"?'),
    re.compile(rf'(?i)\bsearch_path\s*(?:to|=)\s*"?(?:{NAMES})"?'),
)

SCANNED = ("src/**/*.py", "alembic/**/*.py", "alembic/**/*.mako", "tests/**/*.py", "main.py", "alembic.ini", "**/*.sql")


def test_the_detector_actually_detects() -> None:
    """A positive control first. A grep rail that cannot fail is a rail that proves the
    repo is clean of nothing at all."""
    offenders = [
        "select * from farm.animals",
        'UPDATE "feed".rations set x = 1',
        "insert into animal_care.observations values (1)",
        "drop schema sensor cascade",
        "SET search_path TO farm",
    ]
    for line in offenders:
        assert any(p.search(line) for p in PATTERNS), f"the detector missed {line!r}"


def test_no_sql_in_this_repo_names_a_ranch_schema() -> None:
    """`sw_ops` lives in the same database as the ranch's schemas, and the connection can
    write to all of them. This repo adds no endpoints and reads no ranch tables: the only
    way to ranch data is over HTTP through the deployed APIs."""
    here = Path(__file__).resolve()
    hits: list[str] = []
    for pattern in SCANNED:
        for path in REPO_ROOT.glob(pattern):
            if path.resolve() == here or ".venv" in path.parts:
                continue  # this file holds the positive control above
            body = path.read_text(encoding="utf-8")
            for regex in PATTERNS:
                for match in regex.finditer(body):
                    line_no = body[: match.start()].count("\n") + 1
                    hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_no}: {match.group(0)!r}")
    assert hits == [], "SQL naming a ranch schema:\n  " + "\n  ".join(hits)


def test_the_table_definition_carries_no_schema_of_its_own() -> None:
    """One definition, two schemas, decided by the connection. A `schema=` here is how the
    test suite ends up writing to prod."""
    assert metadata.schema is None
    assert metadata.tables["incidents"].schema is None
