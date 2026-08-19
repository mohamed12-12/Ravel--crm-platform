"""add_private_trip_requests

Revision ID: f9c2d1e8a6b4
Revises: f8b2d4c6a9e1
Create Date: 2026-08-19 04:00:00.000001

"""
from alembic import op
import sqlalchemy as sa


revision = "f9c2d1e8a6b4"
down_revision = "f8b2d4c6a9e1"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(column["name"] == column_name for column in sa.inspect(op.get_bind()).get_columns(table_name))


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return any(index["name"] == index_name for index in sa.inspect(op.get_bind()).get_indexes(table_name))


def upgrade():
    if _table_exists("trips") and not _column_exists("trips", "is_private"):
        op.add_column("trips", sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _index_exists("trips", "ix_trips_is_private"):
            op.create_index("ix_trips_is_private", "trips", ["is_private"])

    if _table_exists("booking_transactions") and not _column_exists("booking_transactions", "is_non_refundable"):
        op.add_column(
            "booking_transactions",
            sa.Column("is_non_refundable", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if not _table_exists("private_trip_requests"):
        op.create_table(
            "private_trip_requests",
            sa.Column("request_id", sa.String(length=20), nullable=False),
            sa.Column("traveler_id", sa.String(length=20), nullable=True),
            sa.Column("lead_id", sa.String(length=50), nullable=True),
            sa.Column("service_type", sa.String(length=40), nullable=False),
            sa.Column("trip_scope", sa.String(length=20), nullable=False),
            sa.Column("destination", sa.String(length=200), nullable=True),
            sa.Column("start_date_pref", sa.Date(), nullable=True),
            sa.Column("end_date_pref", sa.Date(), nullable=True),
            sa.Column("dates_flexible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("party_size", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("boys_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("girls_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("budget_amount", sa.Float(), nullable=True),
            sa.Column("budget_currency", sa.String(length=3), nullable=True),
            sa.Column("stage", sa.String(length=40), nullable=False, server_default="registered"),
            sa.Column("stage_changed_at", sa.DateTime(), nullable=False),
            sa.Column("assigned_to_user_id", sa.Integer(), nullable=True),
            sa.Column("consultation_due_at", sa.DateTime(), nullable=True),
            sa.Column("consultation_done_at", sa.DateTime(), nullable=True),
            sa.Column("design_due_at", sa.DateTime(), nullable=True),
            sa.Column("design_delivered_at", sa.DateTime(), nullable=True),
            sa.Column("deposit_amount", sa.Float(), nullable=True),
            sa.Column("deposit_currency", sa.String(length=3), nullable=True),
            sa.Column("deposit_paid_at", sa.DateTime(), nullable=True),
            sa.Column("deposit_is_refundable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("converted_booking_id", sa.String(length=50), nullable=True),
            sa.Column("lost_reason", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=200), nullable=True),
            sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["converted_booking_id"], ["trip_bookings.booking_id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.ForeignKeyConstraint(["lead_id"], ["leads.lead_id"]),
            sa.ForeignKeyConstraint(["traveler_id"], ["travelers.traveler_id"]),
            sa.PrimaryKeyConstraint("request_id"),
            sa.UniqueConstraint("idempotency_key"),
        )
    for index_name, columns in (
        ("ix_private_trip_requests_traveler_id", ["traveler_id"]),
        ("ix_private_trip_requests_lead_id", ["lead_id"]),
        ("ix_private_trip_requests_stage", ["stage"]),
        ("ix_private_trip_requests_assigned_to_user_id", ["assigned_to_user_id"]),
        ("ix_private_trip_requests_converted_booking_id", ["converted_booking_id"]),
        ("ix_private_trip_requests_created_at", ["created_at"]),
    ):
        if not _index_exists("private_trip_requests", index_name):
            op.create_index(index_name, "private_trip_requests", columns)


def downgrade():
    if _table_exists("private_trip_requests"):
        op.drop_table("private_trip_requests")
    if _table_exists("booking_transactions") and _column_exists("booking_transactions", "is_non_refundable"):
        op.drop_column("booking_transactions", "is_non_refundable")
    if _table_exists("trips") and _column_exists("trips", "is_private"):
        if _index_exists("trips", "ix_trips_is_private"):
            op.drop_index("ix_trips_is_private", table_name="trips")
        op.drop_column("trips", "is_private")
