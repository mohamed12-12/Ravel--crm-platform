"""add_idempotency_key_columns_reconciliation

Same situation as f4c8b21e9a3d: idempotency_key was added to the
TripBooking, Lead, and HandoffQueue SQLAlchemy models (used by
agent_crm_bridge.py's _booking_by_idempotency/_handoff_by_idempotency raw
SQL lookups to deduplicate agent-driven writes) but no migration ever
created the column. A database built purely from `flask db upgrade` is
missing it, so every create_handoff_case/create_booking_draft call that
reaches the idempotency lookup raises a "column does not exist" error at
the database level -- silently absorbed by write_tool_executor.execute()'s
generic except Exception into a generic "write_failed" result, which is
why this surfaced as a customer-facing "I couldn't submit the review
request" message with no actionable trace anywhere in the agent's own
logs. Idempotent (checks for the column first), so this is safe to run
whether or not a given database already has these columns out-of-band.

Revision ID: a3f6c9e2d817
Revises: f4c8b21e9a3d
Create Date: 2026-08-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "a3f6c9e2d817"
down_revision = "f4c8b21e9a3d"
branch_labels = None
depends_on = None


_IDEMPOTENCY_KEY_TABLES = ["trip_bookings", "leads", "handoff_queue"]


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    for table_name in _IDEMPOTENCY_KEY_TABLES:
        _add_column_if_missing(table_name, sa.Column("idempotency_key", sa.Text(), nullable=True))


def downgrade():
    for table_name in _IDEMPOTENCY_KEY_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("idempotency_key")
