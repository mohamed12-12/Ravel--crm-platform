"""add_booking_transactions_ledger

Creates the immutable payment/refund ledger. Additive only: no existing table
is altered, no data is written. Backfilling is a separate, re-runnable script
(scripts/backfill_booking_ledger.py) so a slow data pass never holds a schema
lock and can be dry-run and reviewed before it writes anything.

Revision ID: e5a1d73c8b46
Revises: d4e8f2a91c37
Create Date: 2026-08-17 00:00:00.000001

"""
from alembic import op
import sqlalchemy as sa


revision = "e5a1d73c8b46"
down_revision = "d4e8f2a91c37"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    # Guard before create: this schema is reached both through alembic and
    # through UnifiedCRMService's runtime ALTER TABLE self-migrations, so
    # every recent migration in this repo checks first.
    if _table_exists("booking_transactions"):
        return

    op.create_table(
        "booking_transactions",
        sa.Column("transaction_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("public_ref", sa.String(length=24), nullable=True),
        sa.Column("booking_id", sa.String(length=50), nullable=False),
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
        sa.Column("is_inferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reverses_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.ForeignKeyConstraint(["booking_id"], ["trip_bookings.booking_id"],
                                name="fk_booking_transactions_booking"),
        sa.ForeignKeyConstraint(["traveler_id"], ["travelers.traveler_id"],
                                name="fk_booking_transactions_traveler"),
        sa.ForeignKeyConstraint(["reverses_id"], ["booking_transactions.transaction_id"],
                                name="fk_booking_transactions_reverses"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_booking_transactions_created_by"),
        # The first financial CHECK constraints in this schema. Every other
        # money invariant here lives in Python only.
        sa.CheckConstraint("amount > 0", name="ck_booking_transactions_amount_positive"),
        sa.CheckConstraint("entry_type IN ('payment', 'refund')",
                           name="ck_booking_transactions_entry_type"),
    )
    op.create_index("ix_booking_transactions_booking_id", "booking_transactions", ["booking_id"])
    op.create_index("ix_booking_transactions_traveler_id", "booking_transactions", ["traveler_id"])
    op.create_index("ix_booking_transactions_created_at", "booking_transactions", ["created_at"])
    op.create_index("ix_booking_transactions_reverses_id", "booking_transactions", ["reverses_id"])
    op.create_index("ix_booking_transactions_booking_entry", "booking_transactions",
                    ["booking_id", "entry_type"])
    op.create_index("ix_booking_transactions_public_ref", "booking_transactions",
                    ["public_ref"], unique=True)
    # Makes a re-run of the backfill, or a double-submitted form, impossible
    # to post twice rather than merely unlikely.
    op.create_index("ix_booking_transactions_idempotency_key", "booking_transactions",
                    ["idempotency_key"], unique=True)


def downgrade():
    if _table_exists("booking_transactions"):
        op.drop_table("booking_transactions")
