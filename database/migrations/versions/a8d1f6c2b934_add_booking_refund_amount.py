"""add_booking_refund_amount

Revision ID: a8d1f6c2b934
Revises: f4c8b21e9a3d
Create Date: 2026-08-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a8d1f6c2b934"
down_revision = "f4c8b21e9a3d"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    _add_column_if_missing("trip_bookings", sa.Column("refund_amount", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("trip_bookings") as batch_op:
        batch_op.drop_column("refund_amount")
