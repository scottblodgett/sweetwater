"""sw_ops: the held set survives a restart

Revision ID: 0005
Revises: 0004
Created: 2026-09-11

M6, and `docs/issues.md` #3. Since M4 an incident whose agent raised, or whose model call
died in transport, is *held*: carried into the next tick and re-routed until somebody
actually answers for it. The set lived in the loop's memory, so a restart turned every held
incident back into a plain `ongoing` row that would never be re-routed and never get a work
order. One nullable column fixes that: `run_tick` writes the reason when it holds a key and
clears it when the key is answered or resolves, and `run_loop` reads the live held keys
before its first tick.

A reason rather than a boolean, because "held" alone does not say whether the model was
down or the agent crashed, and that is the first thing a person reading the ledger asks.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("held_reason", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "held_reason")
