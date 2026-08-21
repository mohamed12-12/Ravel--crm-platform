"""Add the private trip payment ledger, additional fees, and the private trip counter.

A private trip request used to become revenue only by being converted into a
synthetic Trip + TripBooking. It now carries its own agreed price, its own
additional fees and its own append-only payment ledger, so its money is
recognized when it is actually received rather than when a booking is faked
for it -- and the conversion step is retired, which is what stops the same
money being counted twice.

Also adds additional_fees, shared by trip bookings and private requests, and
travelers.private_trips_count.

Revision ID: a1c7e4b9d206
Revises: d4e8a1c5f930
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa


revision = "a1c7e4b9d206"
down_revision = "d4e8a1c5f930"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("travelers", sa.Column("private_trips_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("private_trip_requests", sa.Column("agreed_price_amount", sa.Float(), nullable=True))
    op.add_column("private_trip_requests", sa.Column("agreed_price_currency", sa.String(length=3), nullable=True))

    op.create_table(
        "private_trip_transactions",
        sa.Column("transaction_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_ref", sa.String(length=24), nullable=True),
        sa.Column("request_id", sa.String(length=20), nullable=False),
        sa.Column("traveler_id", sa.String(length=20), nullable=True),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("date_precision", sa.String(length=16), nullable=False, server_default="exact"),
        sa.Column("method", sa.String(length=40), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="crm_ui"),
        sa.Column("is_inferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_non_refundable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reverses_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_private_trip_transactions_amount_positive"),
        sa.CheckConstraint(
            "entry_type IN ('payment', 'refund')", name="ck_private_trip_transactions_entry_type"
        ),
        sa.ForeignKeyConstraint(["request_id"], ["private_trip_requests.request_id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["travelers.traveler_id"]),
        sa.ForeignKeyConstraint(["reverses_id"], ["private_trip_transactions.transaction_id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index(
        "ix_private_trip_transactions_public_ref", "private_trip_transactions", ["public_ref"], unique=True
    )
    op.create_index(
        "ix_private_trip_transactions_idempotency_key",
        "private_trip_transactions",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_private_trip_transactions_request_id", "private_trip_transactions", ["request_id"])
    op.create_index("ix_private_trip_transactions_traveler_id", "private_trip_transactions", ["traveler_id"])
    op.create_index("ix_private_trip_transactions_created_at", "private_trip_transactions", ["created_at"])
    op.create_index("ix_private_trip_transactions_reverses_id", "private_trip_transactions", ["reverses_id"])
    op.create_index(
        "ix_private_trip_transactions_request_entry",
        "private_trip_transactions",
        ["request_id", "entry_type"],
    )

    op.create_table(
        "additional_fees",
        sa.Column("fee_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("booking_id", sa.String(length=50), nullable=True),
        sa.Column("private_request_id", sa.String(length=20), nullable=True),
        sa.Column("traveler_id", sa.String(length=20), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False, server_default="other"),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("voided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_additional_fees_amount_positive"),
        sa.CheckConstraint(
            "(booking_id IS NOT NULL AND private_request_id IS NULL) "
            "OR (booking_id IS NULL AND private_request_id IS NOT NULL)",
            name="ck_additional_fees_exactly_one_parent",
        ),
        sa.ForeignKeyConstraint(["booking_id"], ["trip_bookings.booking_id"]),
        sa.ForeignKeyConstraint(["private_request_id"], ["private_trip_requests.request_id"]),
        sa.ForeignKeyConstraint(["traveler_id"], ["travelers.traveler_id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["voided_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("fee_id"),
    )
    op.create_index("ix_additional_fees_booking_id", "additional_fees", ["booking_id"])
    op.create_index("ix_additional_fees_private_request_id", "additional_fees", ["private_request_id"])
    op.create_index("ix_additional_fees_traveler_id", "additional_fees", ["traveler_id"])
    op.create_index("ix_additional_fees_created_at", "additional_fees", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_additional_fees_created_at", table_name="additional_fees")
    op.drop_index("ix_additional_fees_traveler_id", table_name="additional_fees")
    op.drop_index("ix_additional_fees_private_request_id", table_name="additional_fees")
    op.drop_index("ix_additional_fees_booking_id", table_name="additional_fees")
    op.drop_table("additional_fees")

    for index_name in (
        "ix_private_trip_transactions_request_entry",
        "ix_private_trip_transactions_reverses_id",
        "ix_private_trip_transactions_created_at",
        "ix_private_trip_transactions_traveler_id",
        "ix_private_trip_transactions_request_id",
        "ix_private_trip_transactions_idempotency_key",
        "ix_private_trip_transactions_public_ref",
    ):
        op.drop_index(index_name, table_name="private_trip_transactions")
    op.drop_table("private_trip_transactions")

    op.drop_column("private_trip_requests", "agreed_price_currency")
    op.drop_column("private_trip_requests", "agreed_price_amount")
    op.drop_column("travelers", "private_trips_count")
