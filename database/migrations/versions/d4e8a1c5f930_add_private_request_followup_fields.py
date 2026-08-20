"""Add employee follow-up and assignment-audit fields to private trip requests

Revision ID: d4e8a1c5f930
Revises: f9c2d1e8a6b4
Create Date: 2026-08-20 07:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "d4e8a1c5f930"
down_revision = "f9c2d1e8a6b4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("private_trip_requests") as batch_op:
        batch_op.add_column(sa.Column("priority", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("current_step", sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column("channel", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("follow_up_status", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("follow_up_due_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("last_contact_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("customer_response_status", sa.String(length=100), nullable=True))
        # Mirrors leads.assigned_to/assigned_at/assigned_by_user_id so this
        # table can reuse apps/api/app/services/assignments.py's generic
        # apply_assignment()/assignment_history() as-is instead of a second,
        # private-request-only assignment implementation.
        batch_op.add_column(sa.Column("assigned_to", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("assigned_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("assigned_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))


def downgrade():
    with op.batch_alter_table("private_trip_requests") as batch_op:
        batch_op.drop_column("assigned_by_user_id")
        batch_op.drop_column("assigned_at")
        batch_op.drop_column("assigned_to")
        batch_op.drop_column("customer_response_status")
        batch_op.drop_column("last_contact_at")
        batch_op.drop_column("follow_up_due_date")
        batch_op.drop_column("follow_up_status")
        batch_op.drop_column("channel")
        batch_op.drop_column("current_step")
        batch_op.drop_column("priority")
