"""sw_ops: the incident ledger

Revision ID: 0001
Revises: none
Created: 2026-09-10

The schema itself is created by `env.py`, before alembic's own version table can be
written into it. This migration owns the tables inside it.

Every name here is unqualified. The engine pins `search_path` to one schema, so this file
runs unchanged against `sw_ops` in Supabase and `sw_ops_test` on the local Postgres, and
there is no schema literal in it that could send it to the wrong one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        # `sensor:category`. A plain string, never a foreign key: the sensor lives in
        # another service and an orphan is allowed on purpose.
        sa.Column("incident_key", sa.Text, nullable=False),
        sa.Column("sensor_id", sa.Text, nullable=False),
        sa.Column("sensor_type", sa.Text, nullable=False),
        sa.Column("location", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("last_value", sa.Text, nullable=True),
        sa.Column("unit", sa.Text, nullable=False, server_default=""),
        sa.Column("threshold", sa.Float, nullable=True),
        sa.Column("occurrences", sa.Integer, nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tick_opened", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tick_last_seen", sa.Integer, nullable=False, server_default="0"),
        sa.Column("run_id", sa.Text, nullable=False, server_default=""),
        sa.Column("owner", sa.Text, nullable=True),
    )
    # At most one LIVE incident per sensor and category, enforced by the database rather
    # than by application code remembering to check first. Partial rather than plain
    # unique so history survives: the same tank drying out in March and again in July is
    # two rows, not one row with March overwritten.
    op.create_index("uq_incidents_open_key", "incidents", ["incident_key"], unique=True, postgresql_where=sa.text("status <> 'resolved'"))
    op.create_index("ix_incidents_status_last_seen", "incidents", ["status", "last_seen_at"])
    op.create_index("ix_incidents_sensor", "incidents", ["sensor_id"])


def downgrade() -> None:
    op.drop_index("ix_incidents_sensor", table_name="incidents")
    op.drop_index("ix_incidents_status_last_seen", table_name="incidents")
    op.drop_index("uq_incidents_open_key", table_name="incidents")
    op.drop_table("incidents")
    # The schema is deliberately NOT dropped here. `env.py` created it, only the test
    # schema is ever droppable, and a downgrade that removes a schema takes alembic's own
    # version table with it.
