"""add_mixed_gender_party_columns

Revision ID: f8b2d4c6a9e1
Revises: f7a9c2d4e6b1
Create Date: 2026-08-19 00:00:00.000002

"""
from alembic import op
import sqlalchemy as sa


revision = "f8b2d4c6a9e1"
down_revision = "f7a9c2d4e6b1"
branch_labels = None
depends_on = None


PARTY_COLUMNS = {
    "boys_count": sa.Integer(),
    "girls_count": sa.Integer(),
    "family_units": sa.Integer(),
}


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in sa.inspect(op.get_bind()).get_columns(table_name))


def upgrade():
    if not _table_exists("trip_bookings"):
        return
    for column_name, column_type in PARTY_COLUMNS.items():
        if not _column_exists("trip_bookings", column_name):
            op.add_column("trip_bookings", sa.Column(column_name, column_type, nullable=True, server_default="0"))


def downgrade():
    if not _table_exists("trip_bookings"):
        return
    for column_name in reversed(PARTY_COLUMNS):
        if _column_exists("trip_bookings", column_name):
            op.drop_column("trip_bookings", column_name)
