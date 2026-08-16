"""add_trip_name_ar_column

Revision ID: d4e8f2a91c37
Revises: a3f7c1e6b9d2
Create Date: 2026-08-16 00:00:00.000001

"""
from alembic import op
import sqlalchemy as sa


revision = "d4e8f2a91c37"
down_revision = "a3f7c1e6b9d2"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in sa.inspect(op.get_bind()).get_columns(table_name))


def upgrade():
    if not _column_exists("trips", "trip_name_ar"):
        op.add_column(
            "trips",
            sa.Column("trip_name_ar", sa.String(length=200), nullable=True),
        )


def downgrade():
    if _column_exists("trips", "trip_name_ar"):
        op.drop_column("trips", "trip_name_ar")
