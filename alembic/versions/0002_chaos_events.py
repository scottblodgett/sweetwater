"""sw_ops: the chaos overlay

Revision ID: 0002
Revises: 0001
Created: 2026-09-10

One table, and it is the only place in this system where a lie about the ranch is stored.

The deployed Sensor API is stateless and synthesizes every reading in code, so there is
nowhere to write a fault into it, and its own fault injector deliberately refuses to arm
when `AWS_LAMBDA_FUNCTION_NAME` is set so deployed prod can never be faulted. That guard
is correct and stays. So the fault lives here instead, and `src/tools/sensors.py` lays it
over the honest live read before triage sees it: **the deployed API stays truthful and
this repo owns the lie, in one place, under test.**

Every name here is unqualified, same as 0001. The engine pins `search_path` to one schema,
so this file runs unchanged against `sw_ops` in Supabase and `sw_ops_test` locally.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "chaos_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # `chaos-{seed}-{seq}`, derived from the seeded plan rather than from a uuid. A
        # replay of the same seed produces the same ids, so re-injecting a demo is
        # idempotent instead of doubling it. That is the whole determinism promise made
        # structural: the database refuses the second copy.
        sa.Column("event_id", sa.Text, nullable=False),
        # Every fault a single scenario firing produced shares one group id. A storm front
        # is four faults and one weather event, and telling those apart is what lets a
        # supervisor fuse them into one work order instead of four unrelated ones.
        sa.Column("group_id", sa.Text, nullable=False, server_default=""),
        sa.Column("scenario", sa.Text, nullable=False),
        # `sensor_overlay` or `animal_event`. Only the first kind is ever applied over a
        # reading; the second is a receipt for a write that really happened upstream.
        sa.Column("kind", sa.Text, nullable=False),
        # A sensor id or an animal id, and a plain string either way. Cross-service ids are
        # never foreign keys here and an orphan is allowed on purpose.
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False, server_default=""),
        sa.Column("location", sa.Text, nullable=False, server_default=""),
        sa.Column("fault", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # The seed and the position in its plan, stored rather than recomputed, so a row
        # in the ledger can be traced back to the run that authored it.
        sa.Column("seed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("seq", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("injected_at", sa.DateTime(timezone=True), nullable=False),
        # Not nullable: an event without an expiry is an event that never heals, and a
        # ranch that never heals goes quiet an hour into the demo.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tick_injected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("run_id", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("uq_chaos_events_event_id", "chaos_events", ["event_id"], unique=True)
    # At most one ACTIVE fault of a given mode per target, enforced by the database rather
    # than by the injector remembering to look first. Partial rather than plain unique so
    # the history survives: the same tank faulted this morning and again tonight is two
    # rows, which is exactly what a `resolved` incident followed by a new one looks like.
    op.create_index("uq_chaos_active_target_fault", "chaos_events", ["target_id", "fault"], unique=True, postgresql_where=sa.text("status = 'active'"))
    op.create_index("ix_chaos_events_status_expires", "chaos_events", ["status", "expires_at"])
    op.create_index("ix_chaos_events_group", "chaos_events", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_chaos_events_group", table_name="chaos_events")
    op.drop_index("ix_chaos_events_status_expires", table_name="chaos_events")
    op.drop_index("uq_chaos_active_target_fault", table_name="chaos_events")
    op.drop_index("uq_chaos_events_event_id", table_name="chaos_events")
    op.drop_table("chaos_events")
