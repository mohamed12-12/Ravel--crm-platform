"""add_trip_program_fields

Revision ID: b6e2d9f4a8c1
Revises: a8d1f6c2b934
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b6e2d9f4a8c1"
down_revision = "a8d1f6c2b934"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    for name in ("itinerary", "inclusions", "exclusions"):
        _add_column_if_missing("trips", sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("trips") as batch_op:
        for name in ("exclusions", "inclusions", "itinerary"):
            batch_op.drop_column(name)
