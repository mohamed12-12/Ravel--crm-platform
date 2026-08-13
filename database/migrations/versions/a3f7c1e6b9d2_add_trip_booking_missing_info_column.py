"""add_trip_booking_missing_info_column

Revision ID: a3f7c1e6b9d2
Revises: e7b2c4d9a1f0
Create Date: 2026-08-13 00:00:00.000001

"""
from alembic import op
import sqlalchemy as sa


revision = "a3f7c1e6b9d2"
down_revision = "e7b2c4d9a1f0"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in sa.inspect(op.get_bind()).get_columns(table_name))


def upgrade():
    if not _column_exists("trip_bookings", "missing_info"):
        op.add_column(
            "trip_bookings",
            sa.Column("missing_info", sa.Boolean(), nullable=True, server_default=sa.false()),
        )


def downgrade():
    if _column_exists("trip_bookings", "missing_info"):
        op.drop_column("trip_bookings", "missing_info")
