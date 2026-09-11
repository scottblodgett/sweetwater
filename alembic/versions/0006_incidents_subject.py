"""sw_ops: an incident is about a subject, and a subject is not always a sensor

Revision ID: 0006
Revises: 0005
Created: 2026-09-11

M7A, the herd sweep. Until now every incident was about a sensor, so the two columns that name
what an incident is about were `sensor_id` and `sensor_type`. The herd sweep opens incidents
about animals (`cow-0903:deceased`), and writing `cow-0903` into a column called `sensor_id`
works today and lies to everyone who reads the table later. So the columns are renamed to what
they hold:

  * `subject_id`    the sensor id, or the animal id. Still a plain string, never a foreign key.
  * `subject_type`  one of the 13 sensor types for a sensor row, or the literal `animal` for an
                    animal row. That is how the two kinds of row are told apart: a type that is
                    `animal` is an animal, anything else is a sensor, including a sensor type
                    triage has never heard of.

A pure rename with no data rewrite: every existing row is a sensor row and its type is already
the right value. The index over the id column is renamed with it. The incident key is unchanged,
`subject:category`, so nothing that reads `incident_key` notices.

Every name here is unqualified, same as 0001 through 0005. The engine pins `search_path`.
"""

from __future__ import annotations

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("incidents", "sensor_id", new_column_name="subject_id")
    op.alter_column("incidents", "sensor_type", new_column_name="subject_type")
    op.execute("ALTER INDEX ix_incidents_sensor RENAME TO ix_incidents_subject")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_incidents_subject RENAME TO ix_incidents_sensor")
    op.alter_column("incidents", "subject_type", new_column_name="sensor_type")
    op.alter_column("incidents", "subject_id", new_column_name="sensor_id")
