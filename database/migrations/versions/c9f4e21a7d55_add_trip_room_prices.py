"""add_trip_room_prices

Revision ID: c9f4e21a7d55
Revises: b6e2d9f4a8c1
Create Date: 2026-08-11 00:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c9f4e21a7d55"
down_revision = "b6e2d9f4a8c1"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    _add_column_if_missing("trips", sa.Column("room_prices_json", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("trips") as batch_op:
        batch_op.drop_column("room_prices_json")
