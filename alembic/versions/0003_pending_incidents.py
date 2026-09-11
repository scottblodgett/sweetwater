"""sw_ops: a pending state, so one bad read is not an incident

Revision ID: 0003
Revises: 0002
Created: 2026-09-10

The deployed Sensor API synthesizes a fresh, unanchored reading on every call, so a healthy
sensor draws an extreme value a fraction of the time and reads fine on the next sweep. Before
this migration every such draw opened an incident, paid a model to write a work order about it,
and resolved itself five minutes later: M4 measured 10 to 24 of those per tick, every tick,
roughly $28 an hour, almost all of it about tanks that were never empty.

Two new values for `status`, and one index change:

  * `pending`   seen on fewer than `INCIDENT_CONFIRM_SWEEPS` consecutive sweeps. Live, so the
                partial unique index covers it, but not paged, not routed, not billed.
  * `dismissed` a pending incident whose sensor read clean before it was confirmed. Never
                alarmed, so never `resolved`; kept as a row because the churn rate is a fact
                about the ranch worth being able to count.

The partial unique index used to say `status <> 'resolved'`; it now names the live set
explicitly, because a second terminal state means "not resolved" no longer means "live".

Every name here is unqualified, same as 0001 and 0002. The engine pins `search_path`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

LIVE = "status IN ('pending', 'opened', 'ongoing')"


def upgrade() -> None:
    op.drop_index("uq_incidents_open_key", table_name="incidents")
    op.create_index("uq_incidents_open_key", "incidents", ["incident_key"], unique=True, postgresql_where=sa.text(LIVE))


def downgrade() -> None:
    # A `pending` or `dismissed` row cannot exist under 0002's vocabulary. Pending rows are
    # promoted to `opened` (the pre-0003 behaviour was to open on first sight) and dismissed
    # rows to `resolved`, so the old index can be rebuilt without a collision.
    op.execute("update incidents set status = 'opened' where status = 'pending'")
    op.execute("update incidents set status = 'resolved', resolved_at = coalesce(resolved_at, last_seen_at) where status = 'dismissed'")
    op.drop_index("uq_incidents_open_key", table_name="incidents")
    op.create_index("uq_incidents_open_key", "incidents", ["incident_key"], unique=True, postgresql_where=sa.text("status <> 'resolved'"))
