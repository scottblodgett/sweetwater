"""sw_ops: the LangGraph checkpointer's tables, for the human gate

Revision ID: 0004
Revises: 0003
Created: 2026-09-11

M6. A proposed write pauses in a LangGraph `interrupt()`, and the pause has to outlive the
process: a gate that evaporates when the loop restarts is a delay, not a gate. The pause
lives in these four tables, owned by `langgraph-checkpoint-postgres` and read back by
`src/agent/gate.py` when a human answers from the CLI.

**The DDL here is the library's own, executed by alembic instead of by `saver.setup()`.**
Decision 1 in `docs/state.md`: every Supabase migration is one alembic revision that ran on
`sw_ops_test` first and was shown to Scott in full before it touched prod. `setup()` would
create the same tables at first use, silently, on whichever database the loop happened to be
pointed at, which is exactly the shape of change that rule exists to prevent. So the
statements are read from the installed library at migration time (not copied into this file,
so they cannot drift from the version actually installed), the version rows the library
expects are written, and `memory.checkpointer()` refuses to run against a database whose
`checkpoint_migrations` is behind the installed library rather than quietly upgrading it.

`CREATE INDEX CONCURRENTLY` cannot run inside a transaction, and alembic runs this file in
one. The tables are empty when this runs, so the plain form is the same index without the
lock-avoidance that an empty table does not need.

Every name is unqualified, same as 0001 through 0003. The engine pins `search_path`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from langgraph.checkpoint.postgres.base import BasePostgresSaver

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None

#: What the library will create. Exposed so the migration's own test can assert the number
#: of version rows written matches, and so the check in `memory.py` reads the same list.
CHECKPOINTER_TABLES = ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes")


def checkpointer_statements() -> list[str]:
    """The library's migration list, one statement each, safe inside a transaction."""
    return [stmt.replace("CONCURRENTLY ", "").strip() for stmt in BasePostgresSaver.MIGRATIONS]


def upgrade() -> None:
    statements = checkpointer_statements()
    for version, statement in enumerate(statements):
        op.execute(sa.text(statement))
        if version > 0:
            # `setup()` records every version it applied except the bootstrap that created
            # the version table itself; match it exactly so a later `setup()` is a no-op.
            op.execute(sa.text(f"INSERT INTO checkpoint_migrations (v) VALUES ({version}) ON CONFLICT DO NOTHING"))


def downgrade() -> None:
    for table in reversed(CHECKPOINTER_TABLES):
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
