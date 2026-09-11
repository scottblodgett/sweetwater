"""sw_ops: the tick, the shift report, and the gate receipt become rows

Revision ID: 0007
Revises: 0006
Created: 2026-09-11

M8, the read API. `python main.py --api` is its own process, on its own box if need be, and it
reads `sw_ops` and nothing else: never the ranch, never a model, never a log file. Until now two
of the five things it has to show existed only as lines in `logs/`, which a second process cannot
see. So the loop writes them here, beside the log line, and the API is a pure projection of the
ledger.

  * `ticks`          one row per tick, including a tick that failed. A handful of typed columns for
                     what the API sorts and filters on, and the whole tick line as `fields` jsonb:
                     the line grew five fields at M7 and three at M7A, and a column per field is a
                     migration every phase for a document the window reads whole anyway. `id` is
                     the SSE cursor.
  * `shift_reports`  the supervisor's page, one per tick that produced one. `incident_keys` is
                     what the page was handed, so `linked` (the fusion claim) can be checked later
                     against it, same as `check_shift_report` did at the time.
  * `audit_receipts` the two halves of a gate receipt, `proposed` and `decided`, keyed on
                     `(audit_id, phase)`. That primary key is `docs/issues.md` #10 closing: the
                     "every audit_id appears exactly twice" rail becomes a constraint, a third
                     receipt for one id cannot be inserted, and `logs/audit.jsonl` becomes a
                     projection of this table rather than the record two processes append to.

The log lines keep being written first-class: the tick line is the heartbeat and must never depend
on the ledger being up. A row that could not be written is a warning on the console, never a failed
tick.

Every name is unqualified, same as 0001 through 0006. The engine pins `search_path`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ticks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("tick", sa.Integer, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("store", sa.Text, nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("failed_stage", sa.Text, nullable=True),
        sa.Column("fields", JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("uq_ticks_run_tick", "ticks", ["run_id", "tick"], unique=True)
    op.create_index("ix_ticks_at", "ticks", ["at"])

    op.create_table(
        "shift_reports",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Text, nullable=False),
        sa.Column("tick", sa.Integer, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("headline", sa.Text, nullable=False, server_default=""),
        sa.Column("situation", sa.Text, nullable=False, server_default=""),
        sa.Column("priorities", JSONB, nullable=False, server_default="[]"),
        sa.Column("linked", JSONB, nullable=False, server_default="[]"),
        sa.Column("escalations", JSONB, nullable=False, server_default="[]"),
        sa.Column("worlds", JSONB, nullable=False, server_default="[]"),
        sa.Column("incident_keys", JSONB, nullable=False, server_default="[]"),
        sa.Column("work_orders", sa.Integer, nullable=False, server_default="0"),
        sa.Column("violations", JSONB, nullable=False, server_default="[]"),
        sa.Column("provider", sa.Text, nullable=False, server_default=""),
        sa.Column("model", sa.Text, nullable=False, server_default=""),
        sa.Column("finish_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("uq_shift_reports_run_tick", "shift_reports", ["run_id", "tick"], unique=True)
    op.create_index("ix_shift_reports_at", "shift_reports", ["at"])

    op.create_table(
        "audit_receipts",
        sa.Column("audit_id", sa.Text, nullable=False),
        sa.Column("phase", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Text, nullable=False, server_default=""),
        sa.Column("tick", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("incident_key", sa.Text, nullable=False, server_default=""),
        sa.Column("proposed_by", sa.Text, nullable=True),
        sa.Column("args", JSONB, nullable=True),
        sa.Column("decision", sa.Text, nullable=True),
        sa.Column("decided_by", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("upstream", sa.Text, nullable=True),
        sa.Column("latency_to_decision_ms", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint("audit_id", "phase", name="pk_audit_receipts"),
        sa.CheckConstraint("phase in ('proposed', 'decided')", name="ck_audit_receipts_phase"),
    )
    op.create_index("ix_audit_receipts_at", "audit_receipts", ["at"])


def downgrade() -> None:
    op.drop_index("ix_audit_receipts_at", table_name="audit_receipts")
    op.drop_table("audit_receipts")
    op.drop_index("ix_shift_reports_at", table_name="shift_reports")
    op.drop_index("uq_shift_reports_run_tick", table_name="shift_reports")
    op.drop_table("shift_reports")
    op.drop_index("ix_ticks_at", table_name="ticks")
    op.drop_index("uq_ticks_run_tick", table_name="ticks")
    op.drop_table("ticks")
